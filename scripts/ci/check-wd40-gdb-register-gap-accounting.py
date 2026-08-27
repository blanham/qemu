#!/usr/bin/env python3
"""Validate GDB supplemental-feature register-gap accounting."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "gdbstub/gdbstub.c"


def function_block(text: str) -> str:
    start_marker = "void gdb_register_coprocessor(CPUState *cpu,"
    end_marker = "\nvoid gdb_unregister_coprocessor_all(CPUState *cpu)"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "gdbstub/gdbstub.c: could not isolate gdb_register_coprocessor"
        )
    return text[start:end]


def main() -> None:
    block = function_block(PATH.read_text(encoding="utf-8"))
    required = (
        "int base_reg = cpu->gdb_num_regs;",
        "if (base_reg < feature->base_reg)",
        "base_reg = feature->base_reg;",
        "gdb_register_feature(cpu, base_reg, get_reg, set_reg, feature);",
        "cpu->gdb_num_regs = base_reg + feature->num_regs;",
        "Include an explicit gap before the feature",
        "following feature overlap the tail of this one",
    )
    missing = [marker for marker in required if marker not in block]
    if missing:
        raise SystemExit(
            f"gdbstub/gdbstub.c: missing gap-accounting markers {missing!r}"
        )

    forbidden = (
        "cpu->gdb_num_regs += feature->num_regs;",
        "cpu->gdb_num_regs = cpu->gdb_num_regs + feature->num_regs;",
    )
    present = [marker for marker in forbidden if marker in block]
    if present:
        raise SystemExit(
            f"gdbstub/gdbstub.c: stale count-only update remains {present!r}"
        )

    assignment = "cpu->gdb_num_regs = base_reg + feature->num_regs;"
    if block.count(assignment) != 1:
        raise SystemExit(
            "gdbstub/gdbstub.c: expected exactly one gap-aware count update"
        )
    if block.find("gdb_register_feature(cpu, base_reg") > block.find(assignment):
        raise SystemExit(
            "gdbstub/gdbstub.c: register count advances before feature setup"
        )

    old_count = 70
    feature_base = 71
    feature_count = 34
    base_reg = max(old_count, feature_base)
    next_reg = base_reg + feature_count
    if next_reg != 105:
        raise SystemExit("internal gap-accounting arithmetic check failed")

    print(
        "WD40 GDB register gaps: explicit feature bases advance the next "
        "free register"
    )


if __name__ == "__main__":
    main()
