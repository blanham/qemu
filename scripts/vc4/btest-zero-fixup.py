#!/usr/bin/env python3
"""Restore documented VideoCore IV BTEST zero-flag semantics."""

from __future__ import annotations

from pathlib import Path

TRANSLATOR = Path("target/vc4/translate.c")
MARKER = "VC4 BTEST sets Z when the selected bit is clear."

LEGACY = r'''    case VC4_OP_BTST:
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

SELECTED_BIT = r'''    case VC4_OP_BTST:
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

CORRECT = r'''    case VC4_OP_BTST:
        tcg_gen_andi_i32(tmp, b, 31);
        tcg_gen_movi_i32(result, 1);
        tcg_gen_shl_i32(result, result, tmp);
        tcg_gen_and_i32(result, result, a);
        /*
         * VC4 BTEST sets Z when the selected bit is clear.  This is the
         * recovered scalar ISA contract and matches test-style zero flags.
         */
        tcg_gen_setcondi_i32(TCG_COND_EQ, result, result, 0);
        tcg_gen_andi_i32(tmp, cpu_sr, ~VC4_SR_Z);
        tcg_gen_shli_i32(result, result, 3);
        tcg_gen_or_i32(cpu_sr, tmp, result);
        writes_result = false;
        break;
'''


def main() -> int:
    text = TRANSLATOR.read_text(encoding="utf-8")

    correct_count = text.count(CORRECT)
    if correct_count == 1:
        print("VideoCore IV BTEST zero semantics are already materialized.")
        return 0
    if correct_count > 1:
        raise RuntimeError(
            f"{TRANSLATOR}: found {correct_count} corrected BTEST blocks"
        )

    candidates = [
        ("selected-bit regression", SELECTED_BIT, text.count(SELECTED_BIT)),
        ("legacy zero semantics", LEGACY, text.count(LEGACY)),
    ]
    matches = [(label, block) for label, block, count in candidates if count == 1]
    ambiguous = [(label, count) for label, _, count in candidates if count > 1]
    if ambiguous:
        raise RuntimeError(f"{TRANSLATOR}: ambiguous BTEST anchors: {ambiguous}")
    if len(matches) != 1:
        counts = {label: count for label, _, count in candidates}
        raise RuntimeError(
            f"{TRANSLATOR}: expected one known BTEST implementation, got {counts}"
        )

    label, old = matches[0]
    TRANSLATOR.write_text(text.replace(old, CORRECT), encoding="utf-8")
    print(f"Restored VideoCore IV BTEST zero semantics from {label}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
