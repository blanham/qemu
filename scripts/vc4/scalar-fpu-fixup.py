#!/usr/bin/env python3
"""Materialize the first scalar VideoCore IV floating-point tranche.

The official Raspberry Pi bootcode reaches FLTU at VPU PC 0x269a and then
immediately uses FDIV and FCMP.  Keep the implementation deterministic by
routing arithmetic through QEMU softfloat, and reject the still-unverified
six-bit floating-immediate form instead of silently treating it as integer
ALU input.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


helper_h = ROOT / "target/vc4/helper.h"
replace_once(
    helper_h,
    """DEF_HELPER_FLAGS_4(vc4_mulhd, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)
""",
    """DEF_HELPER_FLAGS_4(vc4_mulhd, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_FLAGS_3(vc4_float_conv, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_3(vc4_float_op, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_2(vc4_float_cmp, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32)
DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)
""",
    "scalar floating-point helper declarations",
)


op_helper = ROOT / "target/vc4/op_helper.c"
replace_once(
    op_helper,
    """#include "qemu/log.h"
#include "cpu.h"
""",
    """#include "qemu/log.h"
#include "fpu/softfloat.h"
#include "cpu.h"
""",
    "softfloat include",
)
replace_once(
    op_helper,
    """static void vc4_push(CPUArchState *envp, CPUVC4State *env, unsigned reg)
""",
    r"""static void vc4_float_status_init(float_status *status,
                                  FloatRoundMode rounding)
{
    memset(status, 0, sizeof(*status));
    set_float_rounding_mode(rounding, status);
    set_float_detect_tininess(float_tininess_after_rounding, status);
    set_default_nan_mode(true, status);
    set_float_default_nan_pattern(0x40, status);
    set_snan_rule(float_snan_bit_is_zero, status);
}

static uint32_t vc4_float_integer_scale(uint32_t value, int32_t shift)
{
    if (shift >= 0) {
        return shift < 32 ? value << shift : 0;
    }

    shift = -shift;
    return shift < 32 ? value >> shift : 0;
}

uint32_t helper_vc4_float_conv(uint32_t op, uint32_t value,
                               uint32_t shift_raw)
{
    float_status status;
    float32 input = make_float32(value);
    int32_t shift = (int32_t)shift_raw;
    uint32_t result;

    vc4_float_status_init(&status, float_round_nearest_even);

    switch (op) {
    case 0:                         /* FTRUNC */
        result = float32_to_int32_scalbn(input, float_round_to_zero,
                                         0, &status);
        return vc4_float_integer_scale(result, shift);
    case 1:                         /* FLOOR */
        result = float32_to_int32_scalbn(input, float_round_down,
                                         0, &status);
        return vc4_float_integer_scale(result, shift);
    case 2:                         /* FLTS */
        return float32_val(int32_to_float32_scalbn((int32_t)value,
                                                   -shift, &status));
    case 3:                         /* FLTU */
        return float32_val(uint32_to_float32_scalbn(value,
                                                    -shift, &status));
    default:
        return 0;
    }
}

uint32_t helper_vc4_float_op(uint32_t op, uint32_t a, uint32_t b)
{
    float_status status;
    float32 fa = make_float32(a);
    float32 fb = make_float32(b);
    float32 result;

    vc4_float_status_init(&status, float_round_nearest_even);

    switch (op) {
    case 0:                         /* FADD */
        result = float32_add(fa, fb, &status);
        break;
    case 1:                         /* FSUB */
        result = float32_sub(fa, fb, &status);
        break;
    case 2:                         /* FMUL */
        result = float32_mul(fa, fb, &status);
        break;
    case 3:                         /* FDIV */
        result = float32_div(fa, fb, &status);
        break;
    case 5:                         /* FABS */
        result = float32_abs(fb);
        break;
    case 6:                         /* FRSB */
        result = float32_sub(fb, fa, &status);
        break;
    case 7:                         /* FMAX */
        result = float32_minmax(fa, fb, &status, float_minmax_isnum);
        break;
    case 8:                         /* FRCP */
        result = float32_div(float32_one, fb, &status);
        break;
    case 9:                         /* FRSQRT */
        result = float32_div(float32_one,
                             float32_sqrt(fb, &status), &status);
        break;
    case 10:                        /* FNMUL */
        result = float32_chs(float32_mul(fa, fb, &status));
        break;
    case 11:                        /* FMIN */
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
    }

    return float32_val(result);
}

uint32_t helper_vc4_float_cmp(uint32_t a, uint32_t b)
{
    float_status status;
    FloatRelation relation;

    vc4_float_status_init(&status, float_round_nearest_even);
    relation = float32_compare(make_float32(a), make_float32(b), &status);

    switch (relation) {
    case float_relation_equal:
        return VC4_SR_Z;
    case float_relation_less:
        /* Public VC4 reverse-engineering reports N|C for a < b. */
        return VC4_SR_N | VC4_SR_C;
    case float_relation_greater:
        return 0;
    case float_relation_unordered:
        return VC4_SR_V;
    default:
        g_assert_not_reached();
    }
}

static void vc4_push(CPUArchState *envp, CPUVC4State *env, unsigned reg)
""",
    "scalar floating-point helpers",
)


translate = ROOT / "target/vc4/translate.c"
replace_once(
    translate,
    """};

typedef struct DisasContext {
""",
    """};

enum VC4FloatConvOp {
    VC4_FCONV_FTRUNC = 0,
    VC4_FCONV_FLOOR,
    VC4_FCONV_FLTS,
    VC4_FCONV_FLTU,
};

enum VC4FloatOp {
    VC4_FOP_FADD = 0,
    VC4_FOP_FSUB,
    VC4_FOP_FMUL,
    VC4_FOP_FDIV,
    VC4_FOP_FCMP,
    VC4_FOP_FABS,
    VC4_FOP_FRSB,
    VC4_FOP_FMAX,
    VC4_FOP_FRCP,
    VC4_FOP_FRSQRT,
    VC4_FOP_FNMUL,
    VC4_FOP_FMIN,
    VC4_FOP_FLD1,
    VC4_FOP_FLD0,
};

typedef struct DisasContext {
""",
    "scalar floating-point opcode enums",
)
replace_once(
    translate,
    """static unsigned vc4_mem_size(unsigned format)
""",
    r"""static void vc4_gen_float_conv(DisasContext *ctx, unsigned cond,
                               unsigned op, unsigned rd, unsigned ra,
                               int32_t shift)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGLabel *skip;

    if (rd == VC4_REG_PC) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);
    gen_helper_vc4_float_conv(result, tcg_constant_i32(op),
                              vc4_get_reg(ctx, ra),
                              tcg_constant_i32(shift));
    vc4_set_reg(ctx, rd, result);
    vc4_gen_end_predicate(skip);
}

static bool vc4_gen_float_op(DisasContext *ctx, unsigned cond,
                             unsigned op, unsigned rd,
                             unsigned ra, TCGv_i32 b)
{
    TCGv_i32 result = tcg_temp_new_i32();
    TCGLabel *skip;

    if (op > VC4_FOP_FLD0) {
        return false;
    }

    skip = vc4_gen_skip_if_false(cond);
    if (op == VC4_FOP_FCMP) {
        gen_helper_vc4_float_cmp(result, vc4_get_reg(ctx, ra), b);
        vc4_write_nzcv(result);
    } else {
        if (rd == VC4_REG_PC) {
            tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        }
        gen_helper_vc4_float_op(result, tcg_constant_i32(op),
                                vc4_get_reg(ctx, ra), b);
        vc4_set_reg(ctx, rd, result);
    }
    vc4_gen_end_predicate(skip);
    return true;
}

static unsigned vc4_mem_size(unsigned format)
""",
    "scalar floating-point TCG generators",
)
replace_once(
    translate,
    """    if ((i1 & 0xfc00) == 0xc000 && (i2 & 0x0060) == 0x0000) {
""",
    r"""    if ((i1 & 0xff80) == 0xca00 && (i2 & 0x0040) != 0) {
        cond = (i2 >> 7) & 0xf;
        op = (i1 >> 5) & 3;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        offset = vc4_sext(i2 & 0x3f, 0x20);
        vc4_gen_float_conv(ctx, cond, op, rd, ra, offset);
        return true;
    }

    if ((i1 & 0xfe00) == 0xc800 && (i2 & 0x0060) == 0x0000) {
        cond = (i2 >> 7) & 0xf;
        op = (i1 >> 5) & 0xf;
        rd = i1 & 0x1f;
        ra = (i2 >> 11) & 0x1f;
        rb = i2 & 0x1f;
        return vc4_gen_float_op(ctx, cond, op, rd, ra,
                                vc4_get_reg(ctx, rb));
    }

    if ((i1 & 0xfe00) == 0xc800 && (i2 & 0x0040) != 0) {
        /* The six-bit floating immediate encoding is not verified yet. */
        return false;
    }

    if ((i1 & 0xfc00) == 0xc000 && (i2 & 0x0060) == 0x0000) {
""",
    "floating-point decode before scalar integer groups",
)
replace_once(
    translate,
    """    if ((i1 & 0xfe00) == 0xc800 ||
        ((i1 & 0xff80) == 0xca00 && (i2 & 0x40))) {
        return false;               /* floating-point group */
    }

""",
    """,
    "obsolete floating-point rejection",
)

print("Materialized scalar VC4 floating-point conversion and register operations.")
