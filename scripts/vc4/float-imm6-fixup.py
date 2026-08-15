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


op_helper = ROOT / "target/vc4/op_helper.c"
replace_once(
    op_helper,
    """static uint32_t vc4_float_integer_scale(uint32_t value, int32_t shift)
{
""",
    """/*
 * QEMU's generic exp2 helper is polynomial based and can land one ULP below
 * an exact power of two. VC4 firmware expects integer exponents to produce
 * exact powers, so recognize the exactly representable int32 subset and use
 * scalbn for that path.
 */
static bool vc4_float_exact_i32(float32 input, int32_t *value)
{
    float_status status;
    float32 roundtrip;
    int32_t converted;

    if (float32_is_any_nan(input) || float32_is_infinity(input)) {
        return false;
    }

    vc4_float_status_init(&status, float_round_to_zero);
    converted = float32_to_int32_round_to_zero(input, &status);
    roundtrip = int32_to_float32(converted, &status);

    if (float32_is_zero(input) && float32_is_zero(roundtrip)) {
        *value = 0;
        return true;
    }
    if (float32_val(input) != float32_val(roundtrip)) {
        return false;
    }

    *value = converted;
    return true;
}

static uint32_t vc4_float_integer_scale(uint32_t value, int32_t shift)
{
""",
    "exact integer scalar exp2 support",
)
replace_once(
    op_helper,
    """    case 11:                        /* FMIN */
        result = float32_minmax(fa, fb, &status,
                                float_minmax_ismin | float_minmax_isnum);
        break;
    case 12:                        /* FLD1 */
        result = float32_one;
        break;
    case 13:                        /* FLD0 */
        result = float32_zero;
        break;
    default:
        return 0;
""",
    """    case 11:                        /* FMIN */
        result = float32_minmax(fa, fb, &status,
                                float_minmax_ismin | float_minmax_isnum);
        break;
    case 12:                        /* FCEIL */
        set_float_rounding_mode(float_round_up, &status);
        result = float32_round_to_int(fb, &status);
        break;
    case 13:                        /* FFLOOR */
        set_float_rounding_mode(float_round_down, &status);
        result = float32_round_to_int(fb, &status);
        break;
    case 14:                        /* FLOG2 */
        result = float32_log2(fb, &status);
        break;
    case 15: {                      /* FEXP2 */
        int32_t exponent;

        if (vc4_float_exact_i32(fb, &exponent)) {
            result = float32_scalbn(float32_one,
                                    CLAMP(exponent, -512, 512), &status);
        } else {
            result = float32_exp2(fb, &status);
        }
        break;
    }
    default:
        return 0;
""",
    "complete scalar floating-point helper opcodes",
)

translate = ROOT / "target/vc4/translate.c"
replace_once(
    translate,
    """    VC4_FOP_FNMUL,
    VC4_FOP_FMIN,
    VC4_FOP_FLD1,
    VC4_FOP_FLD0,
};
""",
    """    VC4_FOP_FNMUL,
    VC4_FOP_FMIN,
    VC4_FOP_FCEIL,
    VC4_FOP_FFLOOR,
    VC4_FOP_FLOG2,
    VC4_FOP_FEXP2,
};
""",
    "complete scalar floating-point opcode names",
)
replace_once(
    translate,
    """    if (op > VC4_FOP_FLD0) {
        return false;
    }
""",
    """    if (op > VC4_FOP_FEXP2) {
        return false;
    }
""",
    "complete scalar floating-point opcode range",
)
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

print("Materialized VC4 scalar float opcodes and six-bit immediates.")
