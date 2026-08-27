#!/usr/bin/env python3
"""Account for explicit GDB feature register-number gaps."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "gdbstub/gdbstub.c"

OLD = """    /* Add to end of list.  */
    cpu->gdb_num_regs += feature->num_regs;
"""

NEW = """    /*
     * Include an explicit gap before the feature in the next free
     * register number.  Adding only feature->num_regs would make the
     * following feature overlap the tail of this one.
     */
    cpu->gdb_num_regs = base_reg + feature->num_regs;
"""


def main() -> None:
    text = PATH.read_text(encoding="utf-8")
    new_count = text.count(NEW)
    if new_count == 1:
        return
    if new_count != 0:
        raise RuntimeError(
            f"{PATH}: expected at most one materialized gap fix, "
            f"found {new_count}"
        )

    old_count = text.count(OLD)
    if old_count != 1:
        raise RuntimeError(
            f"{PATH}: expected one GDB register-count update, "
            f"found {old_count}"
        )
    PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
