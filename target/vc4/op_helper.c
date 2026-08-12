/*
 * VideoCore IV VPU TCG helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/bitops.h"
#include "qemu/log.h"
#include "cpu.h"
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

G_NORETURN void helper_vc4_raise_illegal(CPUArchState *envp, uint32_t pc,
                                         uint32_t opcode)
{
    CPUVC4State *env = vc4_helper_env(envp);
    CPUState *cs = env_cpu(envp);

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
