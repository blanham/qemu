#!/usr/bin/env python3
"""Validate ordered host-visible patterns across a native VC4 page flip."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from typing import BinaryIO


MODE_RE = re.compile(
    r"VC4_LINUX_KMS_PAGEFLIP_CONNECTOR_OK connector=(?P<connector>\d+) "
    r"crtc=(?P<crtc>\d+) mode=(?P<width>\d+)x(?P<height>\d+)"
)
FAIL_RE = re.compile(
    r"VC4_LINUX_KMS_PAGEFLIP_FAILED stage=(?P<stage>[^ ]+) "
    r"errno=(?P<errno>-?\d+)"
)


def read_token(stream: BinaryIO) -> bytes:
    token = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise ValueError("unexpected end of PPM header")
        if byte == b"#" and not token:
            stream.readline()
            continue
        if byte.isspace():
            if token:
                return bytes(token)
            continue
        token.extend(byte)


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    with path.open("rb") as stream:
        if read_token(stream) != b"P6":
            raise ValueError("unsupported PPM format")
        width = int(read_token(stream))
        height = int(read_token(stream))
        maximum = int(read_token(stream))
        if maximum != 255:
            raise ValueError(f"unsupported PPM maximum {maximum}")
        pixels = stream.read(width * height * 3)
        if len(pixels) != width * height * 3:
            raise ValueError("short PPM payload")
        return width, height, pixels


def expected_pixel(
    x: int,
    y: int,
    width: int,
    height: int,
    second: bool,
) -> tuple[int, int, int]:
    red = x * 255 // (width - 1) if width > 1 else 0
    green = y * 255 // (height - 1) if height > 1 else 0
    checker = bool(((x // 32) ^ (y // 32)) & 1)
    if second:
        red = 255 - red
        green = 255 - green
        checker = not checker
    blue = 0xFF if checker else 0x20
    return red, green, blue


def frame_metrics(path: Path, mode: dict[str, int]) -> dict[str, object]:
    try:
        width, height, pixels = read_ppm(path)
    except (OSError, ValueError) as error:
        return {"filename": path.name, "valid": False, "error": str(error)}

    columns = min(32, width)
    rows = min(24, height)
    xs = sorted({
        0,
        width - 1,
        *(index * (width - 1) // max(1, columns - 1)
          for index in range(columns)),
    })
    ys = sorted({
        0,
        height - 1,
        *(index * (height - 1) // max(1, rows - 1)
          for index in range(rows)),
    })
    errors = [0, 0]
    samples = 0
    values: list[int] = []
    for y in ys:
        for x in xs:
            offset = (y * width + x) * 3
            actual = tuple(pixels[offset:offset + 3])
            values.extend(actual)
            for second in (False, True):
                expected = expected_pixel(x, y, width, height, second)
                errors[int(second)] += sum(
                    abs(actual[channel] - expected[channel])
                    for channel in range(3)
                )
            samples += 1

    mae_a = errors[0] / max(1, samples * 3)
    mae_b = errors[1] / max(1, samples * 3)
    dimensions_match = (
        width == mode["width"] and height == mode["height"]
    )
    return {
        "filename": path.name,
        "valid": True,
        "width": width,
        "height": height,
        "dimensions_match": dimensions_match,
        "mean_absolute_error_a": mae_a,
        "mean_absolute_error_b": mae_b,
        "match_a": dimensions_match and mae_a <= 18.0,
        "match_b": dimensions_match and mae_b <= 18.0,
        "dynamic_range": max(values) - min(values) if values else 0,
    }


def frame_index(frame: dict[str, object]) -> int:
    match = re.search(r"(\d+)", str(frame.get("filename", "")))
    return int(match.group(1)) if match else -1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, type=Path)
    parser.add_argument("--frames-dir", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    serial = args.serial.read_text(errors="replace") if args.serial.is_file() else ""
    mode_matches = list(MODE_RE.finditer(serial))
    mode = (
        {key: int(value) for key, value in mode_matches[-1].groupdict().items()}
        if mode_matches
        else None
    )
    failures = [
        {
            "stage": match.group("stage"),
            "errno": int(match.group("errno")),
        }
        for match in FAIL_RE.finditer(serial)
    ]
    markers = {
        marker: marker in serial
        for marker in (
            "VC4_LINUX_KMS_PAGEFLIP_START",
            "VC4_LINUX_KMS_PAGEFLIP_BUFFER_A_OK",
            "VC4_LINUX_KMS_PAGEFLIP_BUFFER_B_OK",
            "VC4_LINUX_KMS_PAGEFLIP_PATTERN_A_OK",
            "VC4_LINUX_KMS_PAGEFLIP_SUBMIT_OK",
            "VC4_LINUX_KMS_PAGEFLIP_EVENT_OK",
            "VC4_LINUX_KMS_PAGEFLIP_PATTERN_B_OK",
            "VC4_LINUX_KMS_PAGEFLIP_OK",
            "VC4_LINUX_KMS_PAGEFLIP_TIMEOUT",
        )
    }
    frames = [
        frame_metrics(path, mode)
        if mode
        else {"filename": path.name, "valid": False, "error": "mode unavailable"}
        for path in sorted(args.frames_dir.glob("frame-*.ppm"))
    ]
    matches_a = [frame for frame in frames if frame.get("match_a")]
    matches_b = [frame for frame in frames if frame.get("match_b")]
    first_a = min((frame_index(frame) for frame in matches_a), default=None)
    first_b = min((frame_index(frame) for frame in matches_b), default=None)
    ordered = (
        first_a is not None and first_b is not None and first_b > first_a
    )

    if not markers["VC4_LINUX_KMS_PAGEFLIP_START"]:
        classification = "pageflip-probe-not-invoked"
    elif not mode:
        classification = "pageflip-mode-unavailable"
    elif not markers["VC4_LINUX_KMS_PAGEFLIP_PATTERN_A_OK"]:
        classification = "pageflip-initial-modeset-failed"
    elif not markers["VC4_LINUX_KMS_PAGEFLIP_SUBMIT_OK"]:
        classification = "pageflip-submit-failed"
    elif not markers["VC4_LINUX_KMS_PAGEFLIP_EVENT_OK"]:
        classification = (
            "pageflip-event-timeout"
            if markers["VC4_LINUX_KMS_PAGEFLIP_TIMEOUT"]
            else "pageflip-event-unavailable"
        )
    elif not markers["VC4_LINUX_KMS_PAGEFLIP_OK"]:
        classification = "pageflip-guest-incomplete"
    elif not frames:
        classification = "pageflip-screendump-unavailable"
    elif not matches_a:
        classification = "pageflip-pattern-a-not-visible"
    elif not matches_b:
        classification = "pageflip-pattern-b-not-visible"
    elif not ordered:
        classification = "pageflip-pattern-order-invalid"
    else:
        classification = "linux-vc4-kms-pageflip-clear"

    passed = classification == "linux-vc4-kms-pageflip-clear"
    record = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "passed": passed,
        "classification": classification,
        "mode": mode,
        "markers": markers,
        "failures": failures,
        "frame_count": len(frames),
        "pattern_a_frames": [frame.get("filename") for frame in matches_a],
        "pattern_b_frames": [frame.get("filename") for frame in matches_b],
        "first_pattern_a_frame": first_a,
        "first_pattern_b_frame": first_b,
        "ordered": ordered,
        "frames": frames,
        "serial_tail": serial.splitlines()[-300:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    lines = [
        "# VC4 pinned Linux native KMS page-flip frontier",
        "",
        f"- Validation passed: **`{passed}`**",
        f"- Frontier: **`{classification}`**",
        f"- Guest mode: `{mode}`",
        f"- Page-flip event: `{markers['VC4_LINUX_KMS_PAGEFLIP_EVENT_OK']}`",
        f"- Pattern A frames: `{record['pattern_a_frames']}`",
        f"- Pattern B frames: `{record['pattern_b_frames']}`",
        f"- Ordered A→B: `{ordered}`",
        f"- Failures: `{failures}`",
        "",
        "## Frame metrics",
        "",
    ]
    lines.extend(f"- `{frame}`" for frame in frames)
    args.markdown.write_text("\n".join(lines) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
