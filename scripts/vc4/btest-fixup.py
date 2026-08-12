#!/usr/bin/env python3
"""Materialize VideoCore IV BTEST flag semantics."""

from __future__ import annotations

from pathlib import Path

MARKER = "VC4 BTEST copies the selected bit into Z."
OLD = r'''    case VC4_OP_BTST:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_movi_i32(result, 1);
        tcg_gen_shl_i32(result, result, tmp);
        tcg_gen_and_i32(result, result, a);
        tcg_gen_setcondi_i32(TCG_COND_EQ, result, result, 0);
        tcg_gen_andi_i32(tmp, cpu_sr, ~VC4_SR_Z);
        tcg_gen_shli_i32(result, result, 3);
        tcg_gen_or_i32(cpu_sr, tmp, result);
        writes_result = false;
        break;
'''
NEW = r'''    case VC4_OP_BTST:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_movi_i32(result, 1);
        tcg_gen_shl_i32(result, result, tmp);
        tcg_gen_and_i32(result, result, a);
        /*
         * VC4 BTEST copies the selected bit into Z.  Firmware therefore uses
         * BTEST followed by BEQ to remain in a loop while that bit is set.
         */
        tcg_gen_setcondi_i32(TCG_COND_NE, result, result, 0);
        tcg_gen_andi_i32(tmp, cpu_sr, ~VC4_SR_Z);
        tcg_gen_shli_i32(result, result, 3);
        tcg_gen_or_i32(cpu_sr, tmp, result);
        writes_result = false;
        break;
'''


def main() -> int:
    path = Path("target/vc4/translate.c")
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("VideoCore IV BTEST flag semantics are already materialized.")
        return 0

    count = text.count(OLD)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one BTEST implementation anchor, found {count}"
        )

    path.write_text(text.replace(OLD, NEW), encoding="utf-8")
    print("Materialized VideoCore IV BTEST selected-bit flag semantics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
