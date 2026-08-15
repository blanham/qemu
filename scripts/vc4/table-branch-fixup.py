#!/usr/bin/env python3
"""Materialize VideoCore IV scalar table-branch decoding."""

from __future__ import annotations

from pathlib import Path

MARKER = "Table branches index signed byte/halfword entries after the instruction."
HELPER_ANCHOR = '''static void vc4_gen_illegal(DisasContext *ctx, uint16_t opcode)
'''
HELPER = r'''static void vc4_gen_table_branch(DisasContext *ctx, unsigned reg,
                                 bool halfword)
{
    TCGv_i32 index = tcg_temp_new_i32();
    TCGv_i32 address = tcg_temp_new_i32();
    TCGv_i32 displacement = tcg_temp_new_i32();
    TCGv_i32 target = tcg_temp_new_i32();
    TCGv_i32 base = tcg_constant_i32(ctx->base.pc_next);

    /*
     * Table branches index signed byte/halfword entries after the instruction.
     * Each entry is a displacement in halfwords relative to that same base.
     */
    if (halfword) {
        tcg_gen_shli_i32(index, vc4_get_reg(ctx, reg), 1);
    } else {
        tcg_gen_mov_i32(index, vc4_get_reg(ctx, reg));
    }
    tcg_gen_add_i32(address, base, index);
    vc4_gen_qemu_ld_i32(displacement, address, 0,
                        halfword ? (MO_SW | MO_LE) : MO_SB);
    tcg_gen_shli_i32(displacement, displacement, 1);
    tcg_gen_add_i32(target, base, displacement);
    tcg_gen_mov_i32(cpu_pc, target);
    ctx->base.is_jmp = DISAS_JUMP;
}

'''
DECODE_OLD = r'''    if ((insn & 0xffe0) == 0x0080 || (insn & 0xffe0) == 0x00a0) {
        return false;               /* TBB/TBH */
    }
'''
DECODE_NEW = r'''    if ((insn & 0xffe0) == 0x0080) {
        vc4_gen_table_branch(ctx, insn & 0x1f, false);
        return true;
    }
    if ((insn & 0xffe0) == 0x00a0) {
        vc4_gen_table_branch(ctx, insn & 0x1f, true);
        return true;
    }
'''


def main() -> int:
    path = Path("target/vc4/translate.c")
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("VideoCore IV scalar table branches are already materialized.")
        return 0

    helper_count = text.count(HELPER_ANCHOR)
    decode_count = text.count(DECODE_OLD)
    if helper_count != 1:
        raise RuntimeError(
            f"{path}: expected one helper insertion anchor, found {helper_count}"
        )
    if decode_count != 1:
        raise RuntimeError(
            f"{path}: expected one TBB/TBH decode anchor, found {decode_count}"
        )

    text = text.replace(HELPER_ANCHOR, HELPER + HELPER_ANCHOR)
    text = text.replace(DECODE_OLD, DECODE_NEW)
    path.write_text(text, encoding="utf-8")
    print("Materialized VideoCore IV scalar TBB/TBH table branches.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
