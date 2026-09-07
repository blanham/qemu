#!/usr/bin/env python3
"""Integrate the bounded VC4 QPU executor and its unit test."""

from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: expected exactly one anchor, found {count}"
        )
    return text.replace(old, new, 1)


def patch_display_meson() -> None:
    path = Path("hw/display/meson.build")
    text = path.read_text(encoding="utf-8")
    if "'vc4_qpu_exec.c'" in text:
        print(f"{path}: QPU executor already integrated")
        return
    text = replace_once(
        text,
        "'vc4_qpu.c', 'vc4_v3d_frontier.c'",
        "'vc4_qpu.c', 'vc4_qpu_exec.c', 'vc4_v3d_frontier.c'",
        "VC4 display source list",
    )
    path.write_text(text, encoding="utf-8")


def patch_unit_meson() -> None:
    path = Path("tests/unit/meson.build")
    text = path.read_text(encoding="utf-8")
    if "'test-vc4-qpu'" in text:
        print(f"{path}: QPU executor unit test already integrated")
        return
    anchor = """    'test-virtio-dmabuf': [meson.project_source_root() / 'hw/display/virtio-dmabuf.c'],
"""
    replacement = anchor + """    'test-vc4-qpu': [
      meson.project_source_root() / 'hw/display/vc4_qpu.c',
      meson.project_source_root() / 'hw/display/vc4_qpu_exec.c',
    ],
"""
    text = replace_once(
        text, anchor, replacement, "VC4 QPU unit-test entry"
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_display_meson()
    patch_unit_meson()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
