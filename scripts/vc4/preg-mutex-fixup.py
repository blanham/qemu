#!/usr/bin/env python3
"""Materialize VC4 processor-control-register move decoding."""

from __future__ import annotations

from pathlib import Path

PATH = Path("target/vc4/translate.c")
READ_MARKER = "gen_helper_vc4_preg_read"
WRITE_MARKER = "gen_helper_vc4_preg_write"
DECODE_MARKER = "mov rd, pa"

SET_REG_ANCHOR = r'''static void vc4_set_reg(DisasContext *ctx, unsigned reg, TCGv_i32 value)
{
    if (reg < VC4_NUM_GPRS) {
        tcg_gen_mov_i32(cpu_gpr[reg], value);
    } else if (reg == VC4_REG_SR) {
        tcg_gen_mov_i32(cpu_sr, value);
    } else {
        tcg_gen_mov_i32(cpu_pc, value);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

'''

PREG_GENERATORS = r'''static void vc4_gen_preg_read(DisasContext *ctx, unsigned rd,
                               unsigned preg)
{
    TCGv_i32 value = tcg_temp_new_i32();

    gen_helper_vc4_preg_read(value, tcg_env,
                             tcg_constant_i32(preg));
    vc4_set_reg(ctx, rd, value);
}

static void vc4_gen_preg_write(DisasContext *ctx, unsigned preg,
                                unsigned ra)
{
    gen_helper_vc4_preg_write(tcg_env,
                              tcg_constant_i32(preg),
                              vc4_get_reg(ctx, ra));
}

'''

DECODE_ANCHOR = r'''static bool vc4_decode_scalar32(DisasContext *ctx, uint16_t i1, uint16_t i2)
{
    unsigned cond, op, rd, ra, rb, format;
    uint32_t raw;
    int32_t offset;

'''

PREG_DECODER = r'''    if ((i1 & 0xffe0) == 0xcc00 && (i2 & 0xffe0) == 0) {
        /* 1100 1100 000 d:5 ... a:5: mov pd, ra */
        vc4_gen_preg_write(ctx, i1 & 0x1f, i2 & 0x1f);
        return true;
    }

    if ((i1 & 0xffe0) == 0xcc20 && (i2 & 0xffe0) == 0) {
        /* 1100 1100 001 d:5 ... a:5: mov rd, pa */
        vc4_gen_preg_read(ctx, i1 & 0x1f, i2 & 0x1f);
        return true;
    }

'''


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{PATH}: expected one {description} anchor, found {count}"
        )
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    markers = (READ_MARKER in text, WRITE_MARKER in text, DECODE_MARKER in text)

    if all(markers):
        print("VC4 processor-control-register decoding is already materialized.")
        return 0
    if any(markers):
        raise RuntimeError(
            f"{PATH}: partial processor-control-register decoder state: {markers}"
        )

    text = replace_once(
        text,
        SET_REG_ANCHOR,
        SET_REG_ANCHOR + PREG_GENERATORS,
        "register-write helper",
    )
    text = replace_once(
        text,
        DECODE_ANCHOR,
        DECODE_ANCHOR + PREG_DECODER,
        "scalar32 decoder",
    )
    PATH.write_text(text, encoding="utf-8")
    print("Materialized VC4 processor-control-register move decoding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
