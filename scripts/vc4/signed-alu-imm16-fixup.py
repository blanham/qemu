#!/usr/bin/env python3
"""Sign-extend VideoCore IV scalar ALU 16-bit immediates."""

from __future__ import annotations

from pathlib import Path


PATH = Path("target/vc4/translate.c")
OLD = """        vc4_gen_alu_imm(ctx, 14, op, rd, rd, i2);
"""
NEW = """        vc4_gen_alu_imm(ctx, 14, op, rd, rd, (int16_t)i2);
"""


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    old_count = text.count(OLD)
    new_count = text.count(NEW)

    if old_count == 1 and new_count == 0:
        PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        print("sign-extended VC4 scalar ALU imm16")
        return 0

    if old_count == 0 and new_count == 1:
        print("VC4 scalar ALU imm16 is already sign-extended")
        return 0

    raise SystemExit(
        "unexpected VC4 scalar ALU imm16 decoder state: "
        f"old={old_count} new={new_count}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
