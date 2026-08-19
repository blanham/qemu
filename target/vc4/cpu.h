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

typedef struct BCM2835VC4IntcState BCM2835VC4IntcState;

#define VC4_NUM_REGS 32
#define VC4_NUM_GPRS 30
#define VC4_NUM_PREGS 32
#define VC4_PREG_MUTEX_BASE 16
#define VC4_NUM_PREG_MUTEXES \
    (VC4_NUM_PREGS - VC4_PREG_MUTEX_BASE)

#define VC4_REG_SP 25
#define VC4_REG_LR 26
#define VC4_REG_SR 30
#define VC4_REG_PC 31

#define VC4_SR_V       (1u << 0)
#define VC4_SR_C       (1u << 1)
#define VC4_SR_N       (1u << 2)
#define VC4_SR_Z       (1u << 3)
#define VC4_SR_CB_MASK (3u << 4)
#define VC4_SR_S       (1u << 29)
#define VC4_SR_I       (1u << 30)
#define VC4_SR_U       (1u << 31)

#define VC4_CPUID_VALUE 0x04000104u
#define VC4_MAX_EXCEPTION_DEPTH 32

enum {
    VC4_EXCP_ILLEGAL = 1,
    VC4_EXCP_IRQ,
};

#ifdef VC4_SECONDARY_FRONTEND
typedef struct CPUVC4State {
#else
typedef struct CPUArchState {
#endif
    uint32_t gpr[VC4_NUM_GPRS];
    uint32_t sr;
    uint32_t pc;

    /*
     * p0-p15 are processor-local control registers.  p16-p31 are
     * architecturally shared single-bit mutexes.  Current machines expose
     * one VPU core, so retain the mutex bank in CPU state until the second
     * VPU and a shared core complex are modeled.
     */
    uint32_t preg[VC4_PREG_MUTEX_BASE];
    uint32_t preg_mutexes;

    /*
     * In exception mode architectural SP (r25) is banked onto r28.  The
     * first implementation materializes the alias on entry and writes the
     * updated exception stack pointer back to r28 on the outermost RTI.
     */
    uint32_t normal_sp;
    uint32_t external_irq_frames;
    uint8_t exception_depth;

    /* Fields up to this point are cleared by a CPU reset. */
    struct {} end_reset_fields;
} CPUVC4State;

#ifndef VC4_SECONDARY_FRONTEND
struct ArchCPU {
    CPUState parent_obj;
    CPUVC4State env;

    BCM2835VC4IntcState *intc;
};
#else
struct VC4CPU {
    CPUState parent_obj;
    CPUVC4State env;

    BCM2835VC4IntcState *intc;
};
#endif

struct VC4CPUClass {
    CPUClass parent_class;

    DeviceRealize parent_realize;
    ResettablePhases parent_phases;
};

#ifndef VC4_SECONDARY_FRONTEND
#define CPU_RESOLVING_TYPE TYPE_VC4_CPU
#endif

static inline CPUVC4State *vc4_cpu_env(CPUState *cs)
{
    return (CPUVC4State *)(cs + 1);
}

static inline const CPUVC4State *vc4_cpu_env_const(const CPUState *cs)
{
    return (const CPUVC4State *)(cs + 1);
}

static inline CPUState *vc4_env_cpu(CPUVC4State *env)
{
    return (CPUState *)((char *)env - sizeof(CPUState));
}

static inline VC4CPU *vc4_env_archcpu(CPUVC4State *env)
{
    return VC4_CPU(vc4_env_cpu(env));
}

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

bool vc4_cpu_enter_swi(VC4CPU *cpu, uint32_t number,
                       uint32_t return_pc);

void vc4_translate_init(void);
void vc4_translate_code(CPUState *cs, TranslationBlock *tb,
                        int *max_insns, vaddr pc, void *host_pc);

void vc4_cpu_dump_state(CPUState *cs, FILE *f, int flags);
hwaddr vc4_cpu_get_phys_addr_debug(CPUState *cs, vaddr addr);
int vc4_cpu_gdb_read_register(CPUState *cs, GByteArray *buf, int reg);
int vc4_cpu_gdb_write_register(CPUState *cs, uint8_t *buf, int reg);

#endif /* VC4_CPU_H */
