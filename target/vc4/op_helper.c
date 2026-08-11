/*
 * VideoCore IV VPU TCG helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/bitops.h"
#include "qemu/log.h"
#include "cpu.h"
#include "exec/helper-proto.h"
#include "accel/tcg/cpu-ldst.h"
#include "accel/tcg/cpu-loop.h"

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

uint32_t helper_complex_alu(uint32_t op, uint32_t a, uint32_t b)
{
    unsigned shift = b & 31;
    unsigned width;

    switch (op) {
    case 9:                         /* ROR */
        return ror32(a, shift);
    case 14:                        /* EXTU */
        width = b;
        if (width == 0) {
            return 0;
        }
        if (width >= 32) {
            return a;
        }
        return extract32(a, 0, width);
    case 15:                        /* MAX */
        return (int32_t)a > (int32_t)b ? a : b;
    case 16:                        /* BSET */
        return a | (1u << shift);
    case 17:                        /* MIN */
        return (int32_t)a < (int32_t)b ? a : b;
    case 18:                        /* BCLR */
        return a & ~(1u << shift);
    case 20:                        /* BCHG */
        return a ^ (1u << shift);
    case 24:                        /* EXTS */
        width = b;
        if (width == 0) {
            return 0;
        }
        if (width >= 32) {
            return a;
        }
        return sextract32(a, 0, width);
    case 27:                        /* CLZ */
        return b ? clz32(b) : 32;
    case 29:                        /* BREV */
        return vc4_bitreverse(b);
    case 31:                        /* ABS */
        return b == INT32_MIN ? b : ABS((int32_t)b);
    default:
        return 0;
    }
}

uint32_t helper_div(uint32_t a, uint32_t b,
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

uint32_t helper_mulhd(uint32_t a, uint32_t b,
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

static void vc4_push(CPUVC4State *env, unsigned reg)
{
    uint32_t sp = env->gpr[VC4_REG_SP] - 4;

    env->gpr[VC4_REG_SP] = sp;
    cpu_stl_le_data(env, sp, vc4_env_get_reg(env, reg));
}

static void vc4_pop(CPUVC4State *env, unsigned reg)
{
    uint32_t sp = env->gpr[VC4_REG_SP];
    uint32_t value = cpu_ldl_le_data(env, sp);

    vc4_env_set_reg(env, reg, value);
    env->gpr[VC4_REG_SP] = sp + 4;
}

void helper_push_pop(CPUVC4State *env, uint32_t push, uint32_t lrpc,
                     uint32_t start, uint32_t count)
{
    int i;

    if (push) {
        if (lrpc) {
            vc4_push(env, VC4_REG_LR);
        }
        if (lrpc && (((count - 1) & 0xf) == 0xf)) {
            if ((count - 1) == 0x1f) {
                vc4_push(env, start);
            }
        } else {
            for (i = 0; i < count; i++) {
                vc4_push(env, (start + i) % VC4_NUM_REGS);
            }
        }
    } else {
        if (lrpc && (((count - 1) & 0xf) == 0xf)) {
            if ((count - 1) == 0x1f) {
                vc4_pop(env, start);
            }
        } else {
            for (i = count - 1; i >= 0; i--) {
                vc4_pop(env, (start + i) % VC4_NUM_REGS);
            }
        }
        if (lrpc) {
            vc4_pop(env, VC4_REG_PC);
        }
    }
}

G_NORETURN void helper_raise_illegal(CPUVC4State *env, uint32_t pc,
                                     uint32_t opcode)
{
    CPUState *cs = env_cpu(env);

    env->pc = pc;
    cs->exception_index = VC4_EXCP_ILLEGAL;
    qemu_log_mask(LOG_UNIMP,
                  "VideoCore IV: unimplemented opcode 0x%04x at 0x%08x\n",
                  opcode & 0xffff, pc);
    cpu_loop_exit(cs);
}

G_NORETURN void helper_halt(CPUVC4State *env)
{
    CPUState *cs = env_cpu(env);

    cs->halted = 1;
    cpu_loop_exit(cs);
}
