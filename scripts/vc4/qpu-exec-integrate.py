#!/usr/bin/env python3
"""Integrate and harden the bounded VC4 QPU executor and its unit test."""

from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: expected exactly one anchor, found {count}"
        )
    return text.replace(old, new, 1)


def replace_count(
    text: str, old: str, new: str, expected: int, label: str
) -> str:
    count = text.count(old)
    if count != expected:
        raise SystemExit(
            f"{label}: expected {expected} anchors, found {count}"
        )
    return text.replace(old, new)


def patch_executor() -> None:
    path = Path("hw/display/vc4_qpu_exec.c")
    text = path.read_text(encoding="utf-8")
    wrong_add = (
        "vc4_qpu_exec_write(state, instruction->ws,\n"
        "                            instruction->waddr_add"
    )
    correct_add = (
        "vc4_qpu_exec_write(state, !instruction->ws,\n"
        "                            instruction->waddr_add"
    )
    wrong_mul = (
        "vc4_qpu_exec_write(state, !instruction->ws,\n"
        "                            instruction->waddr_mul"
    )
    correct_mul = (
        "vc4_qpu_exec_write(state, instruction->ws,\n"
        "                            instruction->waddr_mul"
    )

    if wrong_add not in text and wrong_mul not in text:
        if text.count(correct_add) != 2 or text.count(correct_mul) != 2:
            raise SystemExit(
                f"{path}: write-swap mapping is neither known-bad nor "
                "known-good"
            )
        print(f"{path}: QPU write-swap mapping already corrected")
    else:
        text = replace_count(
            text, wrong_add, correct_add, 2,
            "QPU add-pipeline write-swap mapping",
        )
        text = replace_count(
            text, wrong_mul, correct_mul, 2,
            "QPU mul-pipeline write-swap mapping",
        )

    text = text.replace(
        "vc4_qpu_vector_splat(&value, instruction->word);",
        "vc4_qpu_vector_splat(&value, (uint32_t)instruction->word);",
    )
    text = text.replace(
        "state->pc = current;",
        "state->pc = (uint32_t)current;",
    )
    path.write_text(text, encoding="utf-8")


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
    patch_executor()
    patch_display_meson()
    patch_unit_meson()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
