#!/usr/bin/env python3
"""Materialize verified VideoCore IV six-bit floating immediates.

The scalar floating-point register form was already implemented. Official
Raspberry Pi bootcode also uses the immediate form, where the six encoded bits
represent a compact IEEE-754 single-precision value. Keep this materializer
idempotent so CI can verify that the source change is present.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


translate = ROOT / "target/vc4/translate.c"
replace_once(
    translate,
    """    vc4_set_reg(ctx, rd, result);
    vc4_gen_end_predicate(skip);
}

static bool vc4_gen_float_op(DisasContext *ctx, unsigned cond,
                             unsigned op, unsigned rd,
                             unsigned ra, TCGv_i32 b)
""",
    """    vc4_set_reg(ctx, rd, result);
    vc4_gen_end_predicate(skip);
}

/*
 * The six-bit immediate is an IEEE-754 shorthand. Bit 5 is the sign.
 * A zero three-bit exponent encodes signed zero and ignores the fraction.
 * Otherwise bits 4:2 select IEEE exponent 125 through 131, and bits 1:0
 * become the two most-significant fraction bits.
 */
static uint32_t vc4_float_imm6_to_bits(unsigned imm)
{
    uint32_t bits;
    unsigned exponent;

    g_assert(imm < 64);

    bits = (imm & 0x20) << 26;
    exponent = (imm >> 2) & 0x7;
    if (exponent != 0) {
        bits |= (exponent + 124) << 23;
        bits |= (imm & 0x3) << 21;
    }
    return bits;
}

static bool vc4_gen_float_op(DisasContext *ctx, unsigned cond,
                             unsigned op, unsigned rd,
                             unsigned ra, TCGv_i32 b)
""",
    "six-bit floating-immediate expansion helper",
)
replace_once(
    translate,
    """    if ((i1 & 0xfe00) == 0xc800 && (i2 & 0x0040) != 0) {
        /* The six-bit floating immediate encoding is not verified yet. */
        return false;
    }
""",
    """    if ((i1 & 0xfe00) == 0xc800 && (i2 & 0x0040) != 0) {
        cond = (i2 >> 7) & 0xf;
        op = (i1 >> 5) & 0xf;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        return vc4_gen_float_op(
            ctx, cond, op, rd, ra,
            tcg_constant_i32(
                (int32_t)vc4_float_imm6_to_bits(i2 & 0x3f)));
    }
""",
    "six-bit floating-immediate decode",
)

print("Materialized VC4 six-bit scalar floating immediates.")
