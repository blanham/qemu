#!/usr/bin/env python3
"""Materialize the bounded VC4 QPU executor's tested build and ISA repairs."""
from __future__ import annotations

from pathlib import Path


def repair(text: str, old: str, new: str, label: str, count: int = 1) -> str:
    """Accept only the exact old or exact already-repaired source form."""
    if text.count(new) == count:
        return text
    if old in text:
        if text.count(old) != count:
            raise SystemExit(f"{label}: unexpected old anchor count")
        return text.replace(old, new)
    if text.count(new) != count:
        raise SystemExit(f"{label}: neither known-old nor known-new source")
    return text


def patch_executor() -> None:
    path = Path("hw/display/vc4_qpu_exec.c")
    text = path.read_text(encoding="utf-8")
    text = repair(text,
        '#include "qemu/osdep.h"\n#include "hw/display/vc4_qpu_exec.h"',
        '#include "qemu/osdep.h"\n#include <math.h>\n#include "hw/display/vc4_qpu_exec.h"',
        "math declarations")
    for pipeline, old_swap, new_swap in (
        ("add", "instruction->ws", "!instruction->ws"),
        ("mul", "!instruction->ws", "instruction->ws"),
    ):
        suffix = f",\n                            instruction->waddr_{pipeline}"
        text = repair(text,
            f"vc4_qpu_exec_write(state, {old_swap}" + suffix,
            f"vc4_qpu_exec_write(state, {new_swap}" + suffix,
            f"{pipeline} write swap", 2)

    # INT32_MAX is not exactly representable as binary32: conversion to float
    # rounds it to +2^31, so the old > INT32_MAX guard admits undefined casts.
    text = repair(text,
        "if (!isfinite(value) || value < INT32_MIN || value > INT32_MAX) {",
        "/* The positive limit is exclusive; -2^31 is representable. */\n"
        "            if (!isfinite(value) || value < -0x1p31f ||\n"
        "                value >= 0x1p31f) {",
        "float-to-int range")

    text = repair(text,
        "if (!read_func(opaque, address, bytes, sizeof(bytes))) {",
        "if (address > UINT32_MAX - (sizeof(bytes) - 1) ||\n"
        "        !read_func(opaque, address, bytes, sizeof(bytes))) {",
        "whole DMA read span", 2)

    text = repair(text,
        "    bool need_a;\n    bool need_b;\n",
        "    bool need_a;\n    bool need_b;\n"
        "    unsigned add_pack;\n    unsigned mul_pack;\n",
        "per-destination packing variables")
    anchor = """    add_execute &= instruction->op_add != VC4_QPU_ADD_NOP;
    mul_execute &= instruction->op_mul != VC4_QPU_MUL_NOP;
"""
    replacement = anchor + """
    /* PM=0 packing belongs to regfile A, not to the add pipeline.
     * Accumulator writes bypass that pack unit. Packed peripheral writes
     * remain unsupported rather than guessing their behavior.
     * See VideoCoreIV-AG100-R, pp. 27 and 30-31.
     */
    add_pack = instruction->ws ? 0 : instruction->pack;
    mul_pack = instruction->ws ? instruction->pack : 0;

    /* A 16-bit pack of a floating-point result means float16 conversion,
     * not truncating the low 16 bits.  That mode is not implemented yet.
     * Reject it before reads or either pipeline's writeback.
     */
    if ((add_execute && add_pack &&
         instruction->waddr_add < VC4_QPU_REGFILE_SIZE &&
         instruction->op_add == VC4_QPU_ADD_FSUB) ||
        (mul_execute && mul_pack &&
         instruction->waddr_mul < VC4_QPU_REGFILE_SIZE &&
         instruction->op_mul == VC4_QPU_MUL_FMUL)) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, instruction->pack);
    }
"""
    # The original two-line anchor is also a prefix of the repaired form.
    if replacement not in text:
        text = repair(text, anchor, replacement, "regfile A pack routing")
    text = repair(text,
        """    if (pack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, pack);
    }

    if (address >= 32 && address <= 35) {
        state->accumulator[address - 32] = *value;
        return true;
    }
""",
        """    if (address >= 32 && address <= 35) {
        state->accumulator[address - 32] = *value;
        return true;
    }
    if (address == VC4_QPU_REG_NULL) {
        return true;
    }
    if (pack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, pack);
    }
""", "accumulator pack bypass")
    text = repair(text,
        "instruction->pm ? 0 : instruction->pack)) {",
        "add_pack)) {", "add pack argument")
    text = repair(text,
        "instruction->pm ? instruction->pack : 0)) {",
        "mul_pack)) {", "mul pack argument")
    text = text.replace(
        "vc4_qpu_vector_splat(&value, instruction->word);",
        "vc4_qpu_vector_splat(&value, (uint32_t)instruction->word);")
    text = text.replace("state->pc = current;", "state->pc = (uint32_t)current;")
    path.write_text(text, encoding="utf-8")


def patch_display_meson() -> None:
    path = Path("hw/display/meson.build")
    text = path.read_text(encoding="utf-8")
    text = repair(text,
        "'vc4_qpu.c', 'vc4_v3d_frontier.c'",
        "'vc4_qpu.c', 'vc4_qpu_exec.c', 'vc4_v3d_frontier.c'",
        "display sources")
    path.write_text(text, encoding="utf-8")


def patch_unit_meson() -> None:
    path = Path("tests/unit/meson.build")
    text = path.read_text(encoding="utf-8")
    anchor = "    'test-virtio-dmabuf': [meson.project_source_root() / 'hw/display/virtio-dmabuf.c'],\n"
    for name in ("test-vc4-qpu", "test-vc4-qpu-contract"):
        entry = f"""    '{name}': [
      meson.project_source_root() / 'hw/display/vc4_qpu.c',
      meson.project_source_root() / 'hw/display/vc4_qpu_exec.c',
    ],
"""
        if f"'{name}'" not in text:
            if text.count(anchor) != 1:
                raise SystemExit("missing unit-test integration anchor")
            text = text.replace(anchor, anchor + entry, 1)
        elif entry not in text:
            raise SystemExit(f"{name}: unexpected existing test definition")
    path.write_text(text, encoding="utf-8")


def main() -> int:
    patch_executor()
    patch_display_meson()
    patch_unit_meson()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
