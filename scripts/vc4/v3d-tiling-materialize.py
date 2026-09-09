#!/usr/bin/env python3
"""Integrate the staged, checksummed VC4 RGBA8888 T/LT tiling slice."""

from __future__ import annotations

import hashlib
from pathlib import Path


EXPECTED = {
    "include/hw/display/vc4_tiling.h":
        "bd6a4a7c448896e979f3e1d49a023ac2e9c2c7282b6f892471c3489e058c2c98",
    "hw/display/vc4_tiling.c":
        "9576eeeed25e56af5e152ed3201a272a4b735d976c3263cdfaded88faee462a6",
    "tests/unit/test-vc4-tiling.c":
        "91472c4d7fae8f0ea84aed7d41e87aa8902f3dd56c8bb05233778f3c91f3c9de",
    "scripts/vc4/v3d-tiling-smoke.py":
        "103c086db4c70a6cf0545ae17f6c9552e5ec84347b9ec8000fcbd35e574f0dc7",
}
STORE_PATH = Path("scripts/vc4/v3d-tiling-store.inc")
STORE_SHA256 = "021475f685c63acddd4eef25ca566873722f4f8c5440604fa9e79ff74f46e214"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def require_once(text: str, anchor: str, label: str) -> None:
    count = text.count(anchor)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")


def validate_staged_sources() -> None:
    for name, expected in EXPECTED.items():
        path = Path(name)
        if not path.is_file():
            raise SystemExit(f"missing staged source: {name}")
        actual = sha256(path)
        if actual != expected:
            raise SystemExit(
                f"{name}: sha256 {actual}, expected {expected}"
            )
    if STORE_PATH.is_file() and sha256(STORE_PATH) != STORE_SHA256:
        raise SystemExit("tiled render-store replacement checksum mismatch")


def patch_v3d() -> None:
    path = Path("hw/display/bcm2835_v3d.c")
    text = path.read_text(encoding="utf-8")

    include_anchor = '#include "hw/display/vc4_v3d_pipeline.h"\n'
    include_line = '#include "hw/display/vc4_tiling.h"\n'
    if include_line not in text:
        require_once(text, include_anchor, "tiling include")
        text = text.replace(include_anchor, include_anchor + include_line, 1)

    local_define = '#define VC4_TILING_FORMAT_LINEAR             0\n'
    if local_define in text:
        require_once(text, local_define, "local linear tiling define")
        text = text.replace(local_define, "", 1)

    new_start = "static bool bcm2835_v3d_tiled_pixel_address("
    old_start = "static bool bcm2835_v3d_store_clear_tile("
    next_function = "\nstatic unsigned bcm2835_v3d_packet_size"
    if new_start not in text:
        require_once(text, old_start, "render-store function")
        if not STORE_PATH.is_file():
            raise SystemExit("missing tiled render-store replacement")
        start = text.index(old_start)
        end = text.find(next_function, start)
        if end < 0:
            raise SystemExit("render-store next-function anchor not found")
        replacement = STORE_PATH.read_text(encoding="utf-8")
        text = text[:start] + replacement + text[end:]

    path.write_text(text, encoding="utf-8")


def patch_display_meson() -> None:
    path = Path("hw/display/meson.build")
    text = path.read_text(encoding="utf-8")
    old = "'vc4_v3d_pipeline.c', 'bcm2835_hdmi.c'"
    new = "'vc4_v3d_pipeline.c', 'vc4_tiling.c', 'bcm2835_hdmi.c'"
    if new not in text:
        require_once(text, old, "display source list")
        text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def patch_unit_meson() -> None:
    path = Path("tests/unit/meson.build")
    text = path.read_text(encoding="utf-8")
    entry = """    'test-vc4-tiling': [
      meson.project_source_root() / 'hw/display/vc4_tiling.c',
    ],
"""
    if entry in text:
        return
    anchor = """    'test-vc4-v3d-pipeline': [
      meson.project_source_root() / 'hw/display/vc4_qpu.c',
      meson.project_source_root() / 'hw/display/vc4_qpu_exec.c',
      meson.project_source_root() / 'hw/display/vc4_v3d_frontier.c',
      meson.project_source_root() / 'hw/display/vc4_v3d_pipeline.c',
    ],
"""
    require_once(text, anchor, "unit test source list")
    text = text.replace(anchor, anchor + entry, 1)
    path.write_text(text, encoding="utf-8")


def main() -> int:
    validate_staged_sources()
    patch_v3d()
    patch_display_meson()
    patch_unit_meson()
    STORE_PATH.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
