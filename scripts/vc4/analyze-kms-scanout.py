#!/usr/bin/env python3
"""Compare QEMU screendumps with the VC4 guest's deterministic KMS pattern."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from typing import BinaryIO


MODE_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_CONNECTOR_OK connector=(?P<connector>\d+) "
    r"crtc=(?P<crtc>\d+) mode=(?P<width>\d+)x(?P<height>\d+) "
    r"clock=(?P<clock>\d+)"
)


def read_token(stream: BinaryIO) -> bytes:
    token = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            if token:
                return bytes(token)
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
        magic = read_token(stream)
        if magic != b"P6":
            raise ValueError(f"unsupported PPM magic {magic!r}")
        width = int(read_token(stream))
        height = int(read_token(stream))
        maximum = int(read_token(stream))
        if width <= 0 or height <= 0:
            raise ValueError(f"invalid PPM dimensions {width}x{height}")
        if maximum != 255:
            raise ValueError(f"unsupported PPM maximum {maximum}")
        pixels = stream.read(width * height * 3)
        if len(pixels) != width * height * 3:
            raise ValueError(
                f"short PPM payload: {len(pixels)} != {width * height * 3}"
            )
        return width, height, pixels


def expected_pixel(x: int, y: int, width: int, height: int) -> tuple[int, int, int]:
    red = x * 255 // (width - 1) if width > 1 else 0
    green = y * 255 // (height - 1) if height > 1 else 0
    blue = 0xFF if ((x // 32) ^ (y // 32)) & 1 else 0x20
    return red, green, blue


def frame_metrics(
    path: Path,
    mode_width: int,
    mode_height: int,
) -> dict[str, object]:
    try:
        width, height, pixels = read_ppm(path)
    except (OSError, ValueError) as error:
        return {
            "filename": path.name,
            "valid": False,
            "error": str(error),
        }

    sample_columns = min(32, width)
    sample_rows = min(24, height)
    xs = sorted({
        0,
        max(0, width - 1),
        *(index * (width - 1) // max(1, sample_columns - 1)
          for index in range(sample_columns)),
    })
    ys = sorted({
        0,
        max(0, height - 1),
        *(index * (height - 1) // max(1, sample_rows - 1)
          for index in range(sample_rows)),
    })

    absolute_error = 0
    samples = 0
    values: list[int] = []
    black = 0
    for y in ys:
        for x in xs:
            offset = (y * width + x) * 3
            actual = tuple(pixels[offset:offset + 3])
            expected = expected_pixel(x, y, width, height)
            absolute_error += sum(
                abs(actual[channel] - expected[channel])
                for channel in range(3)
            )
            values.extend(actual)
            black += int(max(actual) <= 4)
            samples += 1

    mean_absolute_error = absolute_error / max(1, samples * 3)
    dynamic_range = max(values) - min(values) if values else 0
    mean_value = sum(values) / max(1, len(values))
    dimensions_match = width == mode_width and height == mode_height
    pattern_match = dimensions_match and mean_absolute_error <= 18.0
    blank = dynamic_range <= 4 or mean_value <= 2.0 or black == samples
    return {
        "filename": path.name,
        "valid": True,
        "width": width,
        "height": height,
        "dimensions_match": dimensions_match,
        "mean_absolute_error": mean_absolute_error,
        "dynamic_range": dynamic_range,
        "mean_value": mean_value,
        "black_samples": black,
        "sample_count": samples,
        "blank": blank,
        "pattern_match": pattern_match,
    }


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
    modeset_ok = "VC4_LINUX_KMS_MODESET_OK" in serial
    matches = list(MODE_RE.finditer(serial))
    mode = (
        {key: int(value) for key, value in matches[-1].groupdict().items()}
        if matches
        else None
    )
    frame_paths = sorted(args.frames_dir.glob("frame-*.ppm"))
    frames = [
        frame_metrics(path, mode["width"], mode["height"])
        if mode
        else {
            "filename": path.name,
            "valid": False,
            "error": "guest mode marker is unavailable",
        }
        for path in frame_paths
    ]
    valid_frames = [frame for frame in frames if frame.get("valid")]
    matching_frames = [frame for frame in valid_frames if frame.get("pattern_match")]
    dimension_frames = [
        frame for frame in valid_frames if frame.get("dimensions_match")
    ]
    nonblank_frames = [frame for frame in valid_frames if not frame.get("blank")]
    best = min(
        valid_frames,
        key=lambda frame: float(frame.get("mean_absolute_error", float("inf"))),
        default=None,
    )

    capture_record_path = args.frames_dir / "qmp-capture.json"
    capture_record = {}
    if capture_record_path.is_file():
        try:
            value = json.loads(capture_record_path.read_text())
            if isinstance(value, dict):
                capture_record = value
        except json.JSONDecodeError:
            pass

    if not modeset_ok:
        classification = "kms-modeset-regression"
    elif not mode:
        classification = "kms-mode-marker-unavailable"
    elif not valid_frames:
        classification = "qmp-screendump-unavailable"
    elif matching_frames:
        classification = "linux-vc4-kms-scanout-clear"
    elif not nonblank_frames:
        classification = "kms-scanout-blank"
    elif not dimension_frames:
        classification = "kms-scanout-dimensions-mismatch"
    else:
        classification = "kms-scanout-pattern-mismatch"

    passed = classification == "linux-vc4-kms-scanout-clear"
    record = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "passed": passed,
        "classification": classification,
        "modeset_ok": modeset_ok,
        "mode": mode,
        "frame_count": len(frame_paths),
        "valid_frame_count": len(valid_frames),
        "matching_frame_count": len(matching_frames),
        "best_frame": best,
        "frames": frames,
        "capture_record": capture_record,
        "serial_tail": serial.splitlines()[-240:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 pinned Linux native KMS host scanout frontier",
        "",
        f"- Validation passed: **`{passed}`**",
        f"- Frontier: **`{classification}`**",
        f"- Guest modeset completed: `{modeset_ok}`",
        f"- Guest mode: `{mode}`",
        f"- Captured frames: `{len(frame_paths)}`",
        f"- Pattern-matching frames: `{len(matching_frames)}`",
        f"- Best frame: `{best}`",
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
