/*
 * VideoCore IV VPU CPU definitions
 *
 * Initial ISA model based on the public VideoCore IV reverse-engineering
 * corpus and the libresim VC4 interpreter.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef VC4_CPU_H
#define VC4_CPU_H

#include "cpu-qom.h"
#include "exec/cpu-common.h"
#include "exec/cpu-interrupt.h"

#ifdef CONFIG_USER_ONLY
#error "VideoCore IV VPU does not support user-mode emulation"
#endif

#define VC4_NUM_REGS 32
#define VC4_NUM_GPRS 30

#define VC4_REG_SP 25
#define VC4_REG_LR 26
#define VC4_REG_SR 30
#define VC4_REG_PC 31

#define VC4_SR_V (1u << 0)
#define VC4_SR_C (1u << 1)
#define VC4_SR_N (1u << 2)
#define VC4_SR_Z (1u << 3)

#define VC4_CPUID_VALUE 0x04000104u

enum {
    VC4_EXCP_ILLEGAL = 1,
};

typedef struct CPUArchState {
    uint32_t gpr[VC4_NUM_GPRS];
    uint32_t sr;
    uint32_t pc;

    /* Fields up to this point are cleared by a CPU reset. */
    struct {} end_reset_fields;
} CPUVC4State;

struct ArchCPU {
    CPUState parent_obj;
    CPUVC4State env;
};

struct VC4CPUClass {
    CPUClass parent_class;

    DeviceRealize parent_realize;
    ResettablePhases parent_phases;
};

#define CPU_RESOLVING_TYPE TYPE_VC4_CPU

static inline uint32_t vc4_env_get_reg(const CPUVC4State *env, unsigned reg)
{
    if (reg < VC4_NUM_GPRS) {
        return env->gpr[reg];
    }
    if (reg == VC4_REG_SR) {
        return env->sr;
    }
    return env->pc;
}

static inline void vc4_env_set_reg(CPUVC4State *env, unsigned reg,
                                   uint32_t value)
{
    if (reg < VC4_NUM_GPRS) {
        env->gpr[reg] = value;
    } else if (reg == VC4_REG_SR) {
        env->sr = value;
    } else {
        env->pc = value;
    }
}

void vc4_translate_init(void);
void vc4_translate_code(CPUState *cs, TranslationBlock *tb,
                        int *max_insns, vaddr pc, void *host_pc);

void vc4_cpu_dump_state(CPUState *cs, FILE *f, int flags);
hwaddr vc4_cpu_get_phys_addr_debug(CPUState *cs, vaddr addr);
int vc4_cpu_gdb_read_register(CPUState *cs, GByteArray *buf, int reg);
int vc4_cpu_gdb_write_register(CPUState *cs, uint8_t *buf, int reg);

#endif /* VC4_CPU_H */
