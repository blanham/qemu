#!/usr/bin/env python3
"""Summarize the pinned Linux VC4 KMS frontier from serial evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from typing import Any

EXPECTED_MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
    "VC4_LINUX_KMS_RESOURCES_OK",
    "VC4_LINUX_KMS_CRTC_OK",
    "VC4_LINUX_KMS_CONNECTOR_OBJECT_OK",
    "VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK",
    "VC4_LINUX_KMS_CONNECTED_OK",
    "VC4_LINUX_KMS_MODE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
)

VISIBLE_CLEAR = "linux-vc4-kms-visible-scanout-clear"

KMS_DONE_RE = re.compile(
    r"VC4_LINUX_KMS_DONE crtcs=(?P<crtcs>\d+) "
    r"connector_objects=(?P<connector_objects>\d+) "
    r"physical=(?P<physical>\d+) connected=(?P<connected>\d+) "
    r"modes=(?P<modes>\d+)"
)
CONNECTOR_RE = re.compile(
    r"VC4_LINUX_KMS_CONNECTOR id=(?P<id>\d+) "
    r"type=(?P<type>\d+) type_id=(?P<type_id>\d+) "
    r"connection=(?P<connection>\d+) encoder=(?P<encoder>\d+) "
    r"modes=(?P<modes>\d+) encoders=(?P<encoders>\d+) "
    r"props=(?P<props>\d+) physical=(?P<physical>\d+)"
)
BIND_RE = re.compile(
    r"vc4-drm soc:gpu: bound (?P<device>\S+) "
    r"\(ops (?P<ops>\S+) \[vc4\]\)"
)
FLIP_TIMEOUT_RE = re.compile(
    r"\[CRTC:(?P<id>\d+):(?P<name>[^\]]+)\]\s+flip_done timed out"
)
COMMIT_TIMEOUT_RE = re.compile(
    r"\[(?P<object_type>[A-Z]+):(?P<id>\d+)"
    r"(?::(?P<name>[^\]]+))?\]\s+commit wait timed out"
)
FBDEV_RE = re.compile(r"fb0:\s+vc4drmfb frame buffer device")
HDMI_WAIT_RE = re.compile(
    r"Timeout waiting for (?P<register>VC4_HDMI_[A-Z0-9_]+)"
)
GENERIC_COMMIT_TIMEOUT = "Timed out waiting for commit"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--serial", required=True, type=pathlib.Path)
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
        key, result = value.split("=", 1)
        if not key or not result:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        outcomes[key] = result
    return outcomes


def visible_scanout(result: dict[str, Any]) -> dict[str, Any]:
    screenshot_value = result.get("screenshot")
    screenshot = screenshot_value if isinstance(screenshot_value, dict) else {}
    samples_value = screenshot.get("samples")
    matches_value = screenshot.get("matches")
    samples = samples_value if isinstance(samples_value, dict) else {}
    matches = matches_value if isinstance(matches_value, dict) else {}

    return {
        "framebuffer_marker_seen": result.get("framebuffer_marker_seen") is True,
        "screenshot_available": screenshot.get("available") is True,
        "quadrants_match": screenshot.get("quadrants_match") is True,
        "probe_passed": result.get("passed") is True,
        "width": screenshot.get("width"),
        "height": screenshot.get("height"),
        "samples": samples,
        "matches": matches,
        "error": screenshot.get("error"),
    }


def classify(
    markers: dict[str, bool],
    outcomes: dict[str, str],
    flip_timeouts: list[dict[str, Any]],
    commit_timeouts: list[dict[str, Any]],
    generic_commit_timeout: bool,
    fbdev_registered: bool,
    hdmi_wait_timeouts: list[str],
    scanout: dict[str, Any],
) -> str:
    # kms_runtime is classified from its durable probe evidence below.  This
    # preserves the actual failure boundary instead of flattening a black
    # scanout, missing screenshot, or missing framebuffer into a generic step
    # failure.
    failed_steps = [
        key
        for key, value in outcomes.items()
        if key != "kms_runtime" and value != "success"
    ]
    if failed_steps:
        return "workflow-" + "-".join(failed_steps) + "-failed"

    # The workflow proves module loading and SUBMIT_CL in a dedicated
    # render-only boot.  Do not reinterpret absent render markers in the
    # separate native-KMS boot as a renderer regression.
    if "render_regression" not in outcomes:
        if not markers["VC4_LINUX_MODULE_CLOSURE_OK"]:
            return "vc4-module-closure-unavailable"
        if not markers["VC4_LINUX_DRM_SUBMIT_OK"]:
            return "vc4-render-submit-regression"

    # Kernel atomic-commit evidence is deeper than the PID 1 topology probe.
    # Preserve it before falling back to absent marker classifications.
    if flip_timeouts:
        return "vc4-kms-flip-done-timeout"
    if commit_timeouts or generic_commit_timeout:
        if fbdev_registered:
            return "vc4-kms-commit-wait-timeout"
        return "vc4-kms-pre-fbdev-commit-timeout"
    if hdmi_wait_timeouts:
        return "vc4-hdmi-register-wait-timeout"

    if not markers["VC4_LINUX_KMS_RESOURCES_OK"]:
        return "vc4-component-bind-frontier"
    if not markers["VC4_LINUX_KMS_CRTC_OK"]:
        return "vc4-kms-crtc-unavailable"
    if not markers["VC4_LINUX_KMS_CONNECTOR_OBJECT_OK"]:
        return "vc4-kms-connector-object-unavailable"
    if not markers["VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK"]:
        return "vc4-kms-physical-connector-unavailable"
    if not markers["VC4_LINUX_KMS_CONNECTED_OK"]:
        return "vc4-kms-physical-connector-disconnected"
    if not markers["VC4_LINUX_KMS_MODE_OK"]:
        return "vc4-kms-connected-without-modes"
    if not markers["VC4_LINUX_KMS_TOPOLOGY_OK"]:
        return "vc4-kms-topology-incomplete"

    if not scanout["framebuffer_marker_seen"]:
        return "vc4-kms-framebuffer-witness-unavailable"
    if not scanout["screenshot_available"]:
        return "vc4-kms-screenshot-unavailable"
    if not scanout["quadrants_match"]:
        return "vc4-kms-visible-scanout-mismatch"
    if outcomes.get("kms_runtime", "success") != "success":
        return "vc4-kms-runtime-failed"
    if not scanout["probe_passed"]:
        return "vc4-kms-probe-failed"
    return VISIBLE_CLEAR


def main() -> int:
    args = parse_args()
    outcomes = parse_outcomes(args.outcome)
    result: dict[str, Any] = {}
    if args.result.is_file():
        result = json.loads(args.result.read_text())
    serial = args.serial.read_text(errors="replace") if args.serial.is_file() else ""

    markers = {marker: marker in serial for marker in EXPECTED_MARKERS}
    counts = {
        "crtcs": None,
        "connector_objects": None,
        "physical": None,
        "connected": None,
        "modes": None,
    }
    match = KMS_DONE_RE.search(serial)
    if match:
        counts = {key: int(value) for key, value in match.groupdict().items()}

    connectors = [
        {key: int(value) for key, value in match.groupdict().items()}
        for match in CONNECTOR_RE.finditer(serial)
    ]
    components = [match.groupdict() for match in BIND_RE.finditer(serial)]
    flip_timeouts = [
        {
            "id": int(match.group("id")),
            "name": match.group("name"),
        }
        for match in FLIP_TIMEOUT_RE.finditer(serial)
    ]
    commit_timeouts = [
        {
            "object_type": match.group("object_type"),
            "id": int(match.group("id")),
            "name": match.group("name"),
        }
        for match in COMMIT_TIMEOUT_RE.finditer(serial)
    ]
    generic_commit_timeout = GENERIC_COMMIT_TIMEOUT in serial
    fbdev_registered = FBDEV_RE.search(serial) is not None
    hdmi_wait_timeouts = [
        match.group("register") for match in HDMI_WAIT_RE.finditer(serial)
    ]
    scanout = visible_scanout(result)

    classification = classify(
        markers,
        outcomes,
        flip_timeouts,
        commit_timeouts,
        generic_commit_timeout,
        fbdev_registered,
        hdmi_wait_timeouts,
        scanout,
    )
    passed = classification == VISIBLE_CLEAR
    render_submission_preserved = (
        outcomes["render_regression"] == "success"
        if "render_regression" in outcomes
        else markers["VC4_LINUX_DRM_SUBMIT_OK"]
    )

    record = {
        "schema_version": 3,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "passed": passed,
        "classification": classification,
        "outcomes": outcomes,
        "markers": markers,
        "counts": counts,
        "components": components,
        "connectors": connectors,
        "kernel_commit": {
            "fbdev_registered": fbdev_registered,
            "flip_done_timeouts": flip_timeouts,
            "commit_wait_timeouts": commit_timeouts,
            "generic_commit_timeout": generic_commit_timeout,
            "hdmi_register_wait_timeouts": hdmi_wait_timeouts,
        },
        "visible_scanout": scanout,
        "probe_result": result,
        "serial_tail": serial.splitlines()[-400:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 pinned Linux KMS frontier",
        "",
        f"Validation passed: **{'true' if passed else 'false'}**",
        "",
        f"Frontier: **`{classification}`**",
        "",
        f"- Render submission preserved: `{render_submission_preserved}`",
        f"- Native VC4 fbdev registered: `{fbdev_registered}`",
        f"- Native framebuffer write witnessed: `{scanout['framebuffer_marker_seen']}`",
        f"- Native screenshot captured: `{scanout['screenshot_available']}`",
        f"- Native RGB quadrant scanout matched: `{scanout['quadrants_match']}`",
        f"- Native probe passed: `{scanout['probe_passed']}`",
        f"- Flip-done timeouts: `{len(flip_timeouts)}`",
        f"- Object commit-wait timeouts: `{len(commit_timeouts)}`",
        f"- Generic commit timeout: `{generic_commit_timeout}`",
        f"- HDMI register wait timeouts: `{len(hdmi_wait_timeouts)}`",
        f"- CRTC discovered by witness: `{markers['VC4_LINUX_KMS_CRTC_OK']}`",
        f"- Physical connector discovered: `{markers['VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK']}`",
        f"- Physical connector connected: `{markers['VC4_LINUX_KMS_CONNECTED_OK']}`",
        f"- Display mode discovered: `{markers['VC4_LINUX_KMS_MODE_OK']}`",
        f"- Complete KMS topology: `{markers['VC4_LINUX_KMS_TOPOLOGY_OK']}`",
        "",
        "## Resource counts",
        "",
        f"- CRTCs: `{counts['crtcs']}`",
        f"- Connector objects: `{counts['connector_objects']}`",
        f"- Physical connectors: `{counts['physical']}`",
        f"- Connected physical connectors: `{counts['connected']}`",
        f"- Modes on connected physical connectors: `{counts['modes']}`",
    ]
    if scanout["samples"]:
        lines.extend(("", "## Visible scanout samples", ""))
        for name in ("red", "green", "blue", "white"):
            if name not in scanout["samples"]:
                continue
            lines.append(
                f"- `{name}`: RGB `{scanout['samples'][name]}`, "
                f"matched `{scanout['matches'].get(name)}`"
            )
    if scanout["error"]:
        lines.extend(("", "## Visible scanout error", ""))
        lines.append(f"- `{scanout['error']}`")
    if hdmi_wait_timeouts:
        lines.extend(("", "## HDMI hardware wait evidence", ""))
        lines.extend(f"- `{register}`" for register in hdmi_wait_timeouts)
    if flip_timeouts or commit_timeouts:
        lines.extend(("", "## Kernel atomic-commit timeout evidence", ""))
        lines.extend(
            f"- CRTC `{item['id']}:{item['name']}`: `flip_done timed out`"
            for item in flip_timeouts
        )
        lines.extend(
            "- {object_type} `{id}{name}`: `commit wait timed out`".format(
                object_type=item["object_type"],
                id=item["id"],
                name=f":{item['name']}" if item["name"] else "",
            )
            for item in commit_timeouts
        )
    if components:
        lines.extend(("", "## Bound VC4 components", ""))
        lines.extend(
            f"- `{component['device']}` via `{component['ops']}`"
            for component in components
        )
    if connectors:
        lines.extend(("", "## DRM connectors", ""))
        lines.extend(
            "- id `{id}`, type `{type}:{type_id}`, connection `{connection}`, "
            "modes `{modes}`, physical `{physical}`".format(**connector)
            for connector in connectors
        )
    args.markdown.write_text("\n".join(lines) + "\n")

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
