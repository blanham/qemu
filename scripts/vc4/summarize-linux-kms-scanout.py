#!/usr/bin/env python3
"""Summarize the pinned Linux VC4 modeset, page-flip, and scanout frontier."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from typing import Any

MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
    "VC4_LINUX_KMS_SCANOUT_SUPERVISOR_START",
    "VC4_LINUX_KMS_SCANOUT_CONNECTOR_OK",
    "VC4_LINUX_KMS_SETCRTC_OK",
    "VC4_LINUX_KMS_PAGE_FLIP_IOCTL_OK",
    "VC4_LINUX_KMS_PAGE_FLIP_EVENT_OK",
    "VC4_LINUX_KMS_PAGE_FLIP_OK",
    "VC4_LINUX_KMS_SCANOUT_CRTC_OK",
    "VC4_LINUX_KMS_SCANOUT_ARMED",
    "VC4_LINUX_KMS_SCANOUT_SUPERVISOR_READY",
    "VC4_LINUX_KMS_SCANOUT_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
)

SELECTION_RE = re.compile(
    r"VC4_LINUX_KMS_SCANOUT_CONNECTOR_OK connector=(?P<connector>\d+) "
    r"crtc=(?P<crtc>\d+) encoder=(?P<encoder>\d+) "
    r"mode=(?P<mode>\S+) size=(?P<width>\d+)x(?P<height>\d+) "
    r"clock=(?P<clock>\d+) refresh=(?P<refresh>\d+)"
)
RESOURCE_RE = re.compile(
    r"VC4_LINUX_KMS_SCANOUT_RESOURCES crtcs=(?P<crtcs>\d+) "
    r"connectors=(?P<connectors>\d+) encoders=(?P<encoders>\d+) "
    r"fbs=(?P<fbs>\d+) min=(?P<min_width>\d+)x(?P<min_height>\d+) "
    r"max=(?P<max_width>\d+)x(?P<max_height>\d+)"
)
BUFFER_RE = re.compile(
    r"VC4_LINUX_KMS_SCANOUT_BUFFER_OK pattern=(?P<pattern>\d+) "
    r"handle=(?P<handle>\d+) fb=(?P<fb>\d+) pitch=(?P<pitch>\d+) "
    r"size=(?P<size>\d+) map=0x(?P<map>[0-9a-fA-F]+)"
)
FLIP_RE = re.compile(
    r"VC4_LINUX_KMS_PAGE_FLIP_EVENT_OK user_data=0x(?P<user_data>[0-9a-fA-F]+) "
    r"sequence=(?P<sequence>\d+) tv=(?P<seconds>\d+)\.(?P<microseconds>\d+)"
)
CRTC_RE = re.compile(
    r"VC4_LINUX_KMS_SCANOUT_CRTC_OK crtc=(?P<crtc>\d+) fb=(?P<fb>\d+) "
    r"x=(?P<x>\d+) y=(?P<y>\d+) mode_valid=(?P<mode_valid>\d+)"
)
FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_SCANOUT_FAILED stage=(?P<stage>\S+)"
    r"(?:[^\n]*?errno=(?P<errno>\d+))?"
)
FLIP_TIMEOUT_RE = re.compile(r"flip_done timed out")
COMMIT_TIMEOUT_RE = re.compile(r"commit wait timed out|Timed out waiting for commit")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--serial", required=True, type=pathlib.Path)
    parser.add_argument("--screenshot", required=True, type=pathlib.Path)
    parser.add_argument("--json", required=True, type=pathlib.Path)
    parser.add_argument("--markdown", required=True, type=pathlib.Path)
    parser.add_argument("--outcome", action="append", default=[])
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    return parser.parse_args()


def parse_outcomes(values: list[str]) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        name, outcome = value.split("=", 1)
        if not name or not outcome:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        outcomes[name] = outcome
    return outcomes


def ppm_token(data: bytes, offset: int) -> tuple[bytes, int]:
    length = len(data)
    while offset < length:
        if data[offset:offset + 1] == b"#":
            newline = data.find(b"\n", offset)
            offset = length if newline < 0 else newline + 1
            continue
        if not chr(data[offset]).isspace():
            break
        offset += 1
    start = offset
    while offset < length and not chr(data[offset]).isspace():
        offset += 1
    if start == offset:
        raise ValueError("missing PPM token")
    return data[start:offset], offset


def parse_ppm(path: pathlib.Path) -> dict[str, Any]:
    record: dict[str, Any] = {
        "present": path.is_file(),
        "width": None,
        "height": None,
        "max_value": None,
        "nonblack_pixels": 0,
        "nonblack_fraction": 0.0,
        "sampled_unique_colors": 0,
        "corners": {},
        "corner_signature_matches": False,
        "pattern_visible": False,
        "error": None,
    }
    if not path.is_file():
        return record

    try:
        data = path.read_bytes()
        magic, offset = ppm_token(data, 0)
        width_token, offset = ppm_token(data, offset)
        height_token, offset = ppm_token(data, offset)
        max_token, offset = ppm_token(data, offset)
        width = int(width_token)
        height = int(height_token)
        max_value = int(max_token)
        if magic != b"P6":
            raise ValueError(f"unsupported PPM magic {magic!r}")
        if width <= 0 or height <= 0 or max_value != 255:
            raise ValueError("unsupported PPM dimensions or channel depth")
        while offset < len(data) and chr(data[offset]).isspace():
            offset += 1
        expected = width * height * 3
        pixels = data[offset:offset + expected]
        if len(pixels) != expected:
            raise ValueError(
                f"truncated PPM payload: expected {expected}, got {len(pixels)}"
            )

        nonblack = 0
        sampled: set[tuple[int, int, int]] = set()
        sample_step = max(1, (width * height) // 8192)
        for index in range(width * height):
            base = index * 3
            color = (pixels[base], pixels[base + 1], pixels[base + 2])
            if color != (0, 0, 0):
                nonblack += 1
            if index % sample_step == 0:
                sampled.add(color)

        inset_x = min(24, max(0, width // 16))
        inset_y = min(24, max(0, height // 16))
        positions = {
            "top_left": (inset_x, inset_y),
            "top_right": (width - 1 - inset_x, inset_y),
            "bottom_left": (inset_x, height - 1 - inset_y),
            "bottom_right": (width - 1 - inset_x, height - 1 - inset_y),
        }
        corners: dict[str, list[int]] = {}
        for name, (x, y) in positions.items():
            base = (y * width + x) * 3
            corners[name] = [pixels[base], pixels[base + 1], pixels[base + 2]]

        expected_corners = {
            "top_left": (255, 0, 0),
            "top_right": (0, 255, 0),
            "bottom_left": (0, 0, 255),
            "bottom_right": (255, 255, 255),
        }

        def close(actual: list[int], expected_color: tuple[int, int, int]) -> bool:
            return all(abs(channel - wanted) <= 8
                       for channel, wanted in zip(actual, expected_color))

        signature = all(
            close(corners[name], expected_color)
            for name, expected_color in expected_corners.items()
        )
        fraction = nonblack / (width * height)
        record.update(
            width=width,
            height=height,
            max_value=max_value,
            nonblack_pixels=nonblack,
            nonblack_fraction=fraction,
            sampled_unique_colors=len(sampled),
            corners=corners,
            corner_signature_matches=signature,
            pattern_visible=(fraction > 0.25 and len(sampled) >= 6 and signature),
        )
    except (OSError, ValueError) as error:
        record["error"] = str(error)
    return record


def first_match(pattern: re.Pattern[str], text: str) -> dict[str, Any] | None:
    match = pattern.search(text)
    if match is None:
        return None
    return {
        key: int(value) if value.isdigit() else value
        for key, value in match.groupdict().items()
    }


def classify(
    outcomes: dict[str, str],
    markers: dict[str, bool],
    failures: list[dict[str, Any]],
    screenshot: dict[str, Any],
    selection: dict[str, Any] | None,
    flip_timeouts: int,
    commit_timeouts: int,
) -> str:
    failed_steps = [name for name, result in outcomes.items()
                    if result != "success"]
    if failed_steps:
        return "workflow-" + "-".join(failed_steps) + "-failed"
    if not markers["VC4_LINUX_MODULE_CLOSURE_OK"]:
        return "vc4-module-closure-unavailable"
    if not markers["VC4_LINUX_KMS_TOPOLOGY_OK"]:
        return "vc4-kms-topology-regression"
    if failures:
        return "vc4-kms-scanout-" + str(failures[0]["stage"])
    if not markers["VC4_LINUX_KMS_SCANOUT_CONNECTOR_OK"]:
        return "vc4-kms-scanout-connector-unavailable"
    if not markers["VC4_LINUX_KMS_SETCRTC_OK"]:
        return "vc4-kms-setcrtc-frontier"
    if flip_timeouts:
        return "vc4-kms-flip-done-timeout"
    if commit_timeouts:
        return "vc4-kms-commit-wait-timeout"
    if not markers["VC4_LINUX_KMS_PAGE_FLIP_IOCTL_OK"]:
        return "vc4-kms-page-flip-ioctl-frontier"
    if not markers["VC4_LINUX_KMS_PAGE_FLIP_EVENT_OK"]:
        return "vc4-kms-page-flip-event-frontier"
    if not markers["VC4_LINUX_KMS_SCANOUT_ARMED"]:
        return "vc4-kms-scanout-not-armed"
    if not screenshot["present"] or screenshot["error"] is not None:
        return "vc4-kms-scanout-capture-unavailable"
    if screenshot["nonblack_pixels"] == 0:
        return "vc4-kms-scanout-black"
    if selection is not None and (
        screenshot["width"] != selection["width"] or
        screenshot["height"] != selection["height"]
    ):
        return "vc4-kms-scanout-size-mismatch"
    if not screenshot["pattern_visible"]:
        return "vc4-kms-scanout-surface-mismatch"
    return "linux-vc4-kms-scanout-clear"


def main() -> int:
    args = parse_args()
    outcomes = parse_outcomes(args.outcome)
    result: dict[str, Any] = {}
    if args.result.is_file():
        result = json.loads(args.result.read_text())
    serial = args.serial.read_text(errors="replace") if args.serial.is_file() else ""

    markers = {marker: marker in serial for marker in MARKERS}
    failures = [
        {
            "stage": match.group("stage"),
            "errno": (int(match.group("errno"))
                      if match.group("errno") is not None else None),
        }
        for match in FAILURE_RE.finditer(serial)
    ]
    selection = first_match(SELECTION_RE, serial)
    resources = first_match(RESOURCE_RE, serial)
    buffers = [
        {
            key: int(value, 16) if key == "map" else int(value)
            for key, value in match.groupdict().items()
        }
        for match in BUFFER_RE.finditer(serial)
    ]
    flip_event = first_match(FLIP_RE, serial)
    crtc = first_match(CRTC_RE, serial)
    screenshot = parse_ppm(args.screenshot)
    flip_timeouts = len(FLIP_TIMEOUT_RE.findall(serial))
    commit_timeouts = len(COMMIT_TIMEOUT_RE.findall(serial))

    classification = classify(
        outcomes, markers, failures, screenshot, selection,
        flip_timeouts, commit_timeouts,
    )
    passed = classification == "linux-vc4-kms-scanout-clear"
    record = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "passed": passed,
        "classification": classification,
        "outcomes": outcomes,
        "markers": markers,
        "failures": failures,
        "selection": selection,
        "resources": resources,
        "buffers": buffers,
        "page_flip_event": flip_event,
        "crtc_after_flip": crtc,
        "kernel_timeouts": {
            "flip_done": flip_timeouts,
            "commit_wait": commit_timeouts,
        },
        "screenshot": screenshot,
        "probe_result": result,
        "serial_tail": serial.splitlines()[-500:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 pinned Linux scanout frontier",
        "",
        f"Validation passed: **{'true' if passed else 'false'}**",
        "",
        f"Frontier: **`{classification}`**",
        "",
        f"- KMS topology preserved: `{markers['VC4_LINUX_KMS_TOPOLOGY_OK']}`",
        f"- Legacy SETCRTC completed: `{markers['VC4_LINUX_KMS_SETCRTC_OK']}`",
        f"- Page-flip ioctl completed: `{markers['VC4_LINUX_KMS_PAGE_FLIP_IOCTL_OK']}`",
        f"- Page-flip event received: `{markers['VC4_LINUX_KMS_PAGE_FLIP_EVENT_OK']}`",
        f"- Final scanout held active: `{markers['VC4_LINUX_KMS_SCANOUT_ARMED']}`",
        f"- Render submission preserved: `{markers['VC4_LINUX_DRM_SUBMIT_OK']}`",
        f"- Flip-done timeouts: `{flip_timeouts}`",
        f"- Commit-wait timeouts: `{commit_timeouts}`",
        "",
        "## Host framebuffer capture",
        "",
        f"- Present: `{screenshot['present']}`",
        f"- Dimensions: `{screenshot['width']}x{screenshot['height']}`",
        f"- Nonblack pixels: `{screenshot['nonblack_pixels']}`",
        f"- Nonblack fraction: `{screenshot['nonblack_fraction']:.6f}`",
        f"- Sampled unique colors: `{screenshot['sampled_unique_colors']}`",
        f"- Corner signature: `{screenshot['corner_signature_matches']}`",
        f"- Pattern visible: `{screenshot['pattern_visible']}`",
    ]
    if selection is not None:
        lines.extend((
            "",
            "## Selected display path",
            "",
            f"- Connector: `{selection['connector']}`",
            f"- Encoder: `{selection['encoder']}`",
            f"- CRTC: `{selection['crtc']}`",
            f"- Mode: `{selection['mode']}` "
            f"(`{selection['width']}x{selection['height']}` at "
            f"`{selection['clock']}` kHz)",
        ))
    if failures:
        lines.extend(("", "## First scanout failure", ""))
        lines.append(
            f"- Stage `{failures[0]['stage']}`, errno `{failures[0]['errno']}`"
        )
    args.markdown.write_text("\n".join(lines) + "\n")

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
