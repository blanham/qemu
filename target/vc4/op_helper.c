/*
 * VideoCore IV VPU TCG helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/bitops.h"
#include "qemu/log.h"
#include "cpu.h"
#include "fpu/softfloat.h"
#include "hw/vc4/bcm2835_vc4_intc.h"
#include "exec/helper-proto-common.h"
#define HELPER_H "target/vc4/helper.h"
#include "exec/helper-proto.h.inc"
#undef HELPER_H
#include "accel/tcg/cpu-ldst.h"
#include "accel/tcg/cpu-loop.h"

static inline CPUVC4State *vc4_helper_env(CPUArchState *envp)
{
    return (CPUVC4State *)(void *)envp;
}

static uint32_t vc4_bitreverse(uint32_t value)
{
    value = ((value & 0x55555555u) << 1) |
            ((value >> 1) & 0x55555555u);
    value = ((value & 0x33333333u) << 2) |
            ((value >> 2) & 0x33333333u);
    value = ((value & 0x0f0f0f0fu) << 4) |
            ((value >> 4) & 0x0f0f0f0fu);
    value = bswap32(value);
    return value;
}

uint32_t helper_vc4_complex_alu(uint32_t op, uint32_t a, uint32_t b)
{
    unsigned shift = b & 31;
    unsigned width;

    switch (op) {
    case 9:
        return ror32(a, shift);
    case 14:
        width = b;
        if (width == 0) {
            return 0;
        }
        if (width >= 32) {
            return a;
        }
        return extract32(a, 0, width);
    case 15:
        return (int32_t)a > (int32_t)b ? a : b;
    case 16:
        return a | (1u << shift);
    case 17:
        return (int32_t)a < (int32_t)b ? a : b;
    case 18:
        return a & ~(1u << shift);
    case 20:
        return a ^ (1u << shift);
    case 24:
        width = b;
        if (width == 0) {
            return 0;
        }
        if (width >= 32) {
            return a;
        }
        return sextract32(a, 0, width);
    case 27:
        return b ? clz32(b) : 32;
    case 29:
        return vc4_bitreverse(b);
    case 31:
        return b == INT32_MIN ? b : ABS((int32_t)b);
    default:
        return 0;
    }
}

uint32_t helper_vc4_div(uint32_t a, uint32_t b,
                        uint32_t a_unsigned, uint32_t b_unsigned)
{
    if (b == 0) {
        return UINT32_MAX;
    }

    if (a_unsigned && b_unsigned) {
        return a / b;
    }
    if (a_unsigned) {
        return (uint64_t)a / (int64_t)(int32_t)b;
    }
    if (b_unsigned) {
        return (int64_t)(int32_t)a / (uint64_t)b;
    }
    if (a == INT32_MIN && b == UINT32_MAX) {
        return INT32_MIN;
    }
    return (int32_t)a / (int32_t)b;
}

uint32_t helper_vc4_mulhd(uint32_t a, uint32_t b,
                          uint32_t a_unsigned, uint32_t b_unsigned)
{
    if (a_unsigned && b_unsigned) {
        return ((uint64_t)a * (uint64_t)b) >> 32;
    }
    if (a_unsigned) {
        return ((int64_t)(uint64_t)a * (int64_t)(int32_t)b) >> 32;
    }
    if (b_unsigned) {
        return ((int64_t)(int32_t)a * (int64_t)(uint64_t)b) >> 32;
    }
    return ((int64_t)(int32_t)a * (int64_t)(int32_t)b) >> 32;
}

static void vc4_push(CPUArchState *envp, CPUVC4State *env, unsigned reg)
{
    uint32_t sp = env->gpr[VC4_REG_SP] - 4;

    env->gpr[VC4_REG_SP] = sp;
    cpu_stl_le_data(envp, sp, vc4_env_get_reg(env, reg));
}

static void vc4_pop(CPUArchState *envp, CPUVC4State *env, unsigned reg)
{
    uint32_t sp = env->gpr[VC4_REG_SP];
    uint32_t value = cpu_ldl_le_data(envp, sp);

    vc4_env_set_reg(env, reg, value);
    env->gpr[VC4_REG_SP] = sp + 4;
}

void helper_vc4_push_pop(CPUArchState *envp, uint32_t push, uint32_t lrpc,
                         uint32_t start, uint32_t count)
{
    CPUVC4State *env = vc4_helper_env(envp);
    int i;

    if (push) {
        if (lrpc) {
            vc4_push(envp, env, VC4_REG_LR);
        }
        if (lrpc && (((count - 1) & 0xf) == 0xf)) {
            if ((count - 1) == 0x1f) {
                vc4_push(envp, env, start);
            }
        } else {
            for (i = 0; i < count; i++) {
                vc4_push(envp, env, (start + i) % VC4_NUM_REGS);
            }
        }
    } else {
        if (lrpc && (((count - 1) & 0xf) == 0xf)) {
            if ((count - 1) == 0x1f) {
                vc4_pop(envp, env, start);
            }
        } else {
            for (i = count - 1; i >= 0; i--) {
                vc4_pop(envp, env, (start + i) % VC4_NUM_REGS);
            }
        }
        if (lrpc) {
            vc4_pop(envp, env, VC4_REG_PC);
        }
    }
}

void helper_vc4_rti(CPUArchState *envp)
{
    CPUVC4State *env = vc4_helper_env(envp);
    VC4CPU *cpu = vc4_env_archcpu(env);
    uint32_t sp = env->gpr[VC4_REG_SP];

    env->sr = cpu_ldl_le_data(envp, sp);
    env->pc = cpu_ldl_le_data(envp, sp + 4);
    env->gpr[VC4_REG_SP] = sp + 8;

    if (env->exception_depth) {
        env->exception_depth--;
        if (env->exception_depth == 0) {
            env->gpr[28] = env->gpr[VC4_REG_SP];
            env->gpr[VC4_REG_SP] = env->normal_sp;
        }
    }

    if (cpu->intc) {
        bcm2835_vc4_intc_complete(cpu->intc);
    }
}

/*
 * The initial frontend keeps uncommon scalar floating-point instructions on
 * a helper slow path.  This lets production firmware advance without hiding
 * genuinely unknown opcodes, and the decoder can later be lowered directly
 * to TCG once the architectural behaviour is covered by regressions.
 */
enum VC4FloatOp {
    VC4_FADD = 0,
    VC4_FSUB,
    VC4_FMUL,
    VC4_FDIV,
    VC4_FCMP,
    VC4_FABS,
    VC4_FRSB,
    VC4_FMAX,
    VC4_FRCP,
    VC4_FRSQRT,
    VC4_FNMUL,
    VC4_FMIN,
    VC4_FCEIL,
    VC4_FFLOOR,
    VC4_FLOG2,
    VC4_FEXP2,
};

enum VC4FloatConvOp {
    VC4_FTRUNC = 0,
    VC4_FLOOR,
    VC4_FLTS,
    VC4_FLTU,
};

static int32_t vc4_sext(uint32_t value, uint32_t sign_bit)
{
    return (value ^ sign_bit) - sign_bit;
}

static bool vc4_condition_passed(uint32_t sr, unsigned cond)
{
    bool v = (sr & VC4_SR_V) != 0;
    bool c = (sr & VC4_SR_C) != 0;
    bool n = (sr & VC4_SR_N) != 0;
    bool z = (sr & VC4_SR_Z) != 0;
    bool result;

    switch (cond >> 1) {
    case 0:                         /* EQ */
        result = z;
        break;
    case 1:                         /* CS */
        result = c;
        break;
    case 2:                         /* NS */
        result = n;
        break;
    case 3:                         /* VS */
        result = v;
        break;
    case 4:                         /* HI: !C && !Z */
        result = !c && !z;
        break;
    case 5:                         /* GE: N == V */
        result = n == v;
        break;
    case 6:                         /* GT: N == V && !Z */
        result = n == v && !z;
        break;
    case 7:                         /* always / never */
        result = true;
        break;
    default:
        g_assert_not_reached();
    }

    return (cond & 1) ? !result : result;
}

static float_status vc4_float_status(FloatRoundMode rounding_mode)
{
    float_status status = { 0 };

    set_float_rounding_mode(rounding_mode, &status);
    return status;
}

static uint32_t vc4_float_minmax(uint32_t a_bits, uint32_t b_bits,
                                 bool maximum, float_status *status)
{
    float32 a = make_float32(a_bits);
    float32 b = make_float32(b_bits);
    FloatRelation relation;

    if (float32_is_any_nan(a)) {
        return float32_is_any_nan(b) ?
            float32_val(float32_default_nan(status)) : b_bits;
    }
    if (float32_is_any_nan(b)) {
        return a_bits;
    }

    relation = float32_compare_quiet(a, b, status);
    if (relation == float_relation_equal &&
        float32_is_zero(a) && float32_is_zero(b)) {
        /* IEEE-compatible signed-zero selection. */
        return maximum ? (a_bits & b_bits) : (a_bits | b_bits);
    }

    if (maximum) {
        return relation == float_relation_less ? b_bits : a_bits;
    }
    return relation == float_relation_greater ? b_bits : a_bits;
}

static uint32_t vc4_float_result(unsigned op, uint32_t a_bits,
                                 uint32_t b_bits, float_status *status,
                                 bool *writes_result, uint32_t *flags)
{
    float32 a = make_float32(a_bits);
    float32 b = make_float32(b_bits);
    float32 result;
    FloatRelation relation;

    *writes_result = true;
    *flags = 0;

    switch (op) {
    case VC4_FADD:
        result = float32_add(a, b, status);
        break;
    case VC4_FSUB:
        result = float32_sub(a, b, status);
        break;
    case VC4_FMUL:
        result = float32_mul(a, b, status);
        break;
    case VC4_FDIV:
        result = float32_div(a, b, status);
        break;
    case VC4_FCMP:
        relation = float32_compare_quiet(a, b, status);
        *writes_result = false;
        if (relation == float_relation_equal) {
            *flags = VC4_SR_Z;
        } else if (relation == float_relation_less) {
            *flags = VC4_SR_N | VC4_SR_C;
        } else if (relation == float_relation_unordered) {
            /* The recovered scalar model leaves NZCV clear for unordered. */
            *flags = 0;
        }
        return 0;
    case VC4_FABS:
        result = float32_abs(b);
        break;
    case VC4_FRSB:
        result = float32_sub(b, a, status);
        break;
    case VC4_FMAX:
        return vc4_float_minmax(a_bits, b_bits, true, status);
    case VC4_FRCP:
        result = float32_div(float32_one, b, status);
        break;
    case VC4_FRSQRT:
        result = float32_div(float32_one, float32_sqrt(b, status), status);
        break;
    case VC4_FNMUL:
        result = float32_chs(float32_mul(a, b, status));
        break;
    case VC4_FMIN:
        return vc4_float_minmax(a_bits, b_bits, false, status);
    case VC4_FCEIL:
        set_float_rounding_mode(float_round_up, status);
        result = float32_round_to_int(b, status);
        break;
    case VC4_FFLOOR:
        set_float_rounding_mode(float_round_down, status);
        result = float32_round_to_int(b, status);
        break;
    case VC4_FLOG2:
        result = float32_log2(b, status);
        break;
    case VC4_FEXP2:
        result = float32_exp2(b, status);
        break;
    default:
        g_assert_not_reached();
    }

    return float32_val(result);
}

static uint32_t vc4_sasl(uint32_t value, int32_t shift)
{
    int64_t wide_shift = shift;

    if (wide_shift >= 32) {
        return 0;
    }
    if (wide_shift >= 0) {
        return value << wide_shift;
    }
    if (wide_shift <= -32) {
        return (value & UINT32_C(0x80000000)) ? UINT32_MAX : 0;
    }
    return (uint32_t)((int32_t)value >> -wide_shift);
}

static int vc4_float_scale(int32_t shift)
{
    int64_t scale = -(int64_t)shift;

    /* More than this already overflows/underflows every float32 input. */
    return CLAMP(scale, -512, 512);
}

static uint32_t vc4_float_convert(unsigned op, uint32_t value,
                                  int32_t shift, float_status *status)
{
    float32 result;
    int32_t converted;

    switch (op) {
    case VC4_FTRUNC:
        converted = float32_to_int32_round_to_zero(make_float32(value),
                                                    status);
        return vc4_sasl(converted, shift);
    case VC4_FLOOR:
        set_float_rounding_mode(float_round_down, status);
        converted = float32_to_int32(make_float32(value), status);
        return vc4_sasl(converted, shift);
    case VC4_FLTS:
        result = int32_to_float32((int32_t)value, status);
        return float32_val(float32_scalbn(result, vc4_float_scale(shift),
                                          status));
    case VC4_FLTU:
        result = uint32_to_float32(value, status);
        return float32_val(float32_scalbn(result, vc4_float_scale(shift),
                                          status));
    default:
        g_assert_not_reached();
    }
}

static bool vc4_execute_float_slow(CPUVC4State *env, uint32_t pc,
                                   uint16_t i1, uint16_t i2,
                                   uint32_t *next_pc)
{
    unsigned cond = (i2 >> 7) & 0xf;
    unsigned rd = i1 & 0x1f;
    unsigned ra = (i2 >> 11) & 0x1f;
    float_status status = vc4_float_status(float_round_nearest_even);
    uint32_t result;

    *next_pc = pc + 4;
    if (!vc4_condition_passed(env->sr, cond)) {
        return true;
    }

    if ((i1 & 0xfe00) == 0xc800) {
        unsigned op = (i1 >> 5) & 0xf;
        uint32_t a = vc4_env_get_reg(env, ra);
        uint32_t b;
        uint32_t flags;
        bool writes_result;

        if (i2 & 0x40) {
            int32_t imm = vc4_sext(i2 & 0x3f, 0x20);

            b = float32_val(int32_to_float32(imm, &status));
        } else {
            b = vc4_env_get_reg(env, i2 & 0x1f);
        }

        result = vc4_float_result(op, a, b, &status,
                                  &writes_result, &flags);
        if (!writes_result) {
            env->sr = (env->sr & ~UINT32_C(0xf)) | flags;
        } else if (rd == VC4_REG_PC) {
            *next_pc = result;
        } else {
            vc4_env_set_reg(env, rd, result);
        }
        return true;
    }

    if ((i1 & 0xff80) == 0xca00) {
        unsigned op = (i1 >> 5) & 3;
        int32_t shift;

        if (i2 & 0x40) {
            shift = vc4_sext(i2 & 0x3f, 0x20);
        } else {
            shift = vc4_env_get_reg(env, i2 & 0x1f);
        }

        result = vc4_float_convert(op, vc4_env_get_reg(env, ra),
                                   shift, &status);
        if (rd == VC4_REG_PC) {
            *next_pc = result;
        } else {
            vc4_env_set_reg(env, rd, result);
        }
        return true;
    }

    return false;
}

G_NORETURN void helper_vc4_raise_illegal(CPUArchState *envp, uint32_t pc,
                                         uint32_t opcode)
{
    CPUVC4State *env = vc4_helper_env(envp);
    CPUState *cs = env_cpu(envp);
    uint16_t i1 = opcode;
    uint32_t next_pc;

    if ((i1 & 0xfe00) == 0xc800 || (i1 & 0xff80) == 0xca00) {
        uint16_t i2 = cpu_lduw_le_data(envp, pc + 2);

        if (vc4_execute_float_slow(env, pc, i1, i2, &next_pc)) {
            env->pc = next_pc;
            cpu_loop_exit(cs);
        }
    }

    env->pc = pc;
    cs->exception_index = VC4_EXCP_ILLEGAL;
    qemu_log_mask(LOG_UNIMP,
                  "VideoCore IV: unimplemented opcode 0x%04x at 0x%08x\n",
                  opcode & 0xffff, pc);
    cpu_loop_exit(cs);
}

G_NORETURN void helper_vc4_halt(CPUArchState *envp)
{
    CPUVC4State *env = vc4_helper_env(envp);
    CPUState *cs = env_cpu(envp);

    qemu_log_mask(CPU_LOG_EXEC, "VideoCore IV: HALT at 0x%08x\n", env->pc);
    cs->halted = 1;
    cpu_loop_exit(cs);
}
