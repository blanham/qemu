#!/usr/bin/env python3
"""Verify the deterministic XRGB8888 image painted by the VC4 page-flip witness."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

PATTERN_NAME = "vc4-native-kms-pageflip-xrgb8888-v1"
CHECKER_SIZE = 32
DARK_RED = 0x18
BRIGHT_RED = 0xFF


def read_ppm(path: Path) -> tuple[int, int, bytes, int]:
    data = path.read_bytes()
    position = 0
    tokens: list[bytes] = []

    while len(tokens) < 4:
        while position < len(data) and data[position] in b" \t\r\n":
            position += 1
        if position >= len(data):
            raise ValueError("truncated PPM header")
        if data[position] == ord("#"):
            newline = data.find(b"\n", position)
            if newline < 0:
                raise ValueError("unterminated PPM comment")
            position = newline + 1
            continue
        end = position
        while end < len(data) and data[end] not in b" \t\r\n":
            end += 1
        tokens.append(data[position:end])
        position = end

    if tokens[0] != b"P6":
        raise ValueError(f"unsupported PPM magic {tokens[0]!r}")
    width = int(tokens[1])
    height = int(tokens[2])
    maximum = int(tokens[3])
    if width <= 0 or height <= 0 or maximum != 255:
        raise ValueError(
            f"unsupported PPM geometry {width}x{height} maximum={maximum}"
        )

    while position < len(data) and data[position] in b" \t\r\n":
        position += 1
    expected_length = width * height * 3
    available = len(data) - position
    if available < expected_length:
        raise ValueError(
            f"truncated PPM pixels: expected {expected_length}, got {available}"
        )
    return width, height, data[position:position + expected_length], (
        available - expected_length
    )


def expected_pixel(x: int, y: int, width: int, height: int) -> tuple[int, int, int]:
    checker = ((x // CHECKER_SIZE) ^ (y // CHECKER_SIZE)) & 1
    red = BRIGHT_RED if checker else DARK_RED
    green = x * 255 // (width - 1) if width > 1 else 0
    blue = 255 - y * 255 // (height - 1) if height > 1 else 0xFF
    return red, green, blue


def verify(path: Path, tolerance: int, max_mismatches: int) -> dict[str, Any]:
    width, height, pixels, trailing_bytes = read_ppm(path)
    mismatched_pixels = 0
    max_channel_error = 0
    first_mismatch: dict[str, Any] | None = None

    for y in range(height):
        for x in range(width):
            offset = (y * width + x) * 3
            actual = tuple(pixels[offset:offset + 3])
            expected = expected_pixel(x, y, width, height)
            errors = tuple(
                abs(actual[channel] - expected[channel])
                for channel in range(3)
            )
            pixel_error = max(errors)
            max_channel_error = max(max_channel_error, pixel_error)
            if pixel_error <= tolerance:
                continue
            mismatched_pixels += 1
            if first_mismatch is None:
                first_mismatch = {
                    "x": x,
                    "y": y,
                    "actual_rgb": list(actual),
                    "expected_rgb": list(expected),
                    "channel_errors": list(errors),
                }

    total_pixels = width * height
    passed = mismatched_pixels <= max_mismatches
    return {
        "schema_version": 1,
        "pattern": PATTERN_NAME,
        "image": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": width,
        "height": height,
        "total_pixels": total_pixels,
        "tolerance": tolerance,
        "max_mismatches": max_mismatches,
        "mismatched_pixels": mismatched_pixels,
        "matching_fraction": (total_pixels - mismatched_pixels) / total_pixels,
        "max_channel_error": max_channel_error,
        "first_mismatch": first_mismatch,
        "trailing_bytes": trailing_bytes,
        "passed": passed,
    }


def self_test() -> None:
    width = 97
    height = 65
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend(expected_pixel(x, y, width, height))

    import tempfile

    with tempfile.TemporaryDirectory(prefix="vc4-pageflip-image-") as temp_dir:
        image = Path(temp_dir) / "pattern.ppm"
        image.write_bytes(
            f"P6\n{width} {height}\n255\n".encode("ascii") + pixels
        )
        if not verify(image, 0, 0)["passed"]:
            raise AssertionError("generated reference pattern did not pass")
        damaged = bytearray(image.read_bytes())
        damaged[-1] ^= 1
        image.write_bytes(damaged)
        if verify(image, 0, 0)["passed"]:
            raise AssertionError("corrupted reference pattern unexpectedly passed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", nargs="?", type=Path)
    parser.add_argument("--json", dest="json_path", type=Path)
    parser.add_argument("--tolerance", type=int, default=0)
    parser.add_argument("--max-mismatches", type=int, default=0)
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        print("VC4 page-flip image verifier self-test passed")
        return 0
    if args.image is None:
        parser.error("image is required unless --self-test is used")
    if not args.image.is_file():
        parser.error(f"image does not exist: {args.image}")
    if not 0 <= args.tolerance <= 255:
        parser.error("--tolerance must be between 0 and 255")
    if args.max_mismatches < 0:
        parser.error("--max-mismatches must be non-negative")

    try:
        record = verify(args.image, args.tolerance, args.max_mismatches)
    except Exception as exc:
        record = {
            "schema_version": 1,
            "pattern": PATTERN_NAME,
            "image": str(args.image),
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }

    rendered = json.dumps(record, indent=2, sort_keys=True) + "\n"
    if args.json_path is not None:
        args.json_path.parent.mkdir(parents=True, exist_ok=True)
        args.json_path.write_text(rendered, encoding="utf-8")
    sys.stdout.write(rendered)
    return 0 if record.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
