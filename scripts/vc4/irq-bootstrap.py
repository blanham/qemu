#!/usr/bin/env python3
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parents[2]

def write(path: str, content: str) -> None:
    p = ROOT / path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dedent(content).lstrip(), encoding="utf-8")

def replace(path: str, old: str, new: str) -> None:
    p = ROOT / path
    text = p.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old[:80]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")

write("include/hw/vc4/bcm2835_vc4_intc.h", r'''
/*
 * Broadcom BCM283x VideoCore IV vectored interrupt controller
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_VC4_BCM2835_VC4_INTC_H
#define HW_VC4_BCM2835_VC4_INTC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_VC4_INTC "bcm2835-vc4-intc"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835VC4IntcState, BCM2835_VC4_INTC)

#define BCM2835_VC4_INTC_GPU_IRQ "gpu-irq"
#define BCM2835_VC4_INTC_NUM_IRQS 64

struct BCM2835VC4IntcState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    qemu_irq irq;

    uint32_t control;
    uint32_t source[2];
    uint32_t force[2];
    uint32_t mask[8];
    uint32_t vaddr;
    uint32_t wakeup;
    uint32_t profile;

    int16_t active_vector;
    int16_t pending_vector;
    int8_t active_priority;
    int8_t pending_priority;
};

bool bcm2835_vc4_intc_acknowledge(BCM2835VC4IntcState *s,
                                  uint32_t *vector,
                                  uint32_t *vector_base);
void bcm2835_vc4_intc_complete(BCM2835VC4IntcState *s);

#endif
''')

write("hw/vc4/bcm2835_vc4_intc.c", r'''
/*
 * Broadcom BCM283x VideoCore IV vectored interrupt controller
 *
 * The register layout follows the Broadcom-generated intctrl0/intctrl1
 * headers retained by the open VideoCore firmware projects.  Each external
 * source has a three-bit priority in a four-bit mask slot.  Zero masks a
 * source; priorities 1..7 are eligible, subject to IC_C.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/vc4/bcm2835_vc4_intc.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

enum {
    VC4_INTC_C          = 0x00,
    VC4_INTC_S          = 0x04,
    VC4_INTC_SRC0       = 0x08,
    VC4_INTC_SRC1       = 0x0c,
    VC4_INTC_MASK0      = 0x10,
    VC4_INTC_MASK7      = 0x2c,
    VC4_INTC_VADDR      = 0x30,
    VC4_INTC_WAKEUP     = 0x34,
    VC4_INTC_PROFILE    = 0x38,
    VC4_INTC_FORCE0     = 0x40,
    VC4_INTC_FORCE1     = 0x44,
    VC4_INTC_FORCE0_SET = 0x48,
    VC4_INTC_FORCE1_SET = 0x4c,
    VC4_INTC_FORCE0_CLR = 0x50,
    VC4_INTC_FORCE1_CLR = 0x54,
};

static unsigned vc4_intc_source_priority(const BCM2835VC4IntcState *s,
                                         unsigned source)
{
    return extract32(s->mask[source >> 3], (source & 7) * 4, 3);
}

static uint32_t vc4_intc_status_field(int vector, int priority)
{
    if (vector < 0) {
        return 0;
    }

    return (vector & 0x7f) | ((priority & 7) << 8);
}

static void vc4_intc_update(BCM2835VC4IntcState *s)
{
    uint64_t pending = ((uint64_t)(s->source[1] | s->force[1]) << 32) |
                       (s->source[0] | s->force[0]);
    int best_source = -1;
    int best_priority = -1;
    unsigned threshold = s->control & 7;
    unsigned source;

    for (source = 0; source < BCM2835_VC4_INTC_NUM_IRQS; source++) {
        unsigned priority;

        if (!(pending & (UINT64_C(1) << source))) {
            continue;
        }

        priority = vc4_intc_source_priority(s, source);
        if (priority == 0 || priority <= threshold) {
            continue;
        }

        if ((int)priority > best_priority) {
            best_source = source;
            best_priority = priority;
        }
    }

    s->pending_vector = best_source < 0 ? -1 : best_source + 64;
    s->pending_priority = best_priority;

    /*
     * The CPU acknowledges the vector through the device API.  Hold the
     * output low while a handler is active; RTI completes it and lets a
     * still-level source retrigger.
     */
    qemu_set_irq(s->irq,
                 s->active_vector < 0 && s->pending_vector >= 0);
}

static void vc4_intc_set_irq(void *opaque, int irq, int level)
{
    BCM2835VC4IntcState *s = opaque;
    unsigned bank = irq >> 5;
    unsigned bit = irq & 31;

    s->source[bank] = deposit32(s->source[bank], bit, 1, level != 0);
    vc4_intc_update(s);
}

bool bcm2835_vc4_intc_acknowledge(BCM2835VC4IntcState *s,
                                  uint32_t *vector,
                                  uint32_t *vector_base)
{
    if (s->active_vector >= 0 || s->pending_vector < 0) {
        return false;
    }

    s->active_vector = s->pending_vector;
    s->active_priority = s->pending_priority;
    *vector = s->active_vector;
    *vector_base = s->vaddr;
    vc4_intc_update(s);
    return true;
}

void bcm2835_vc4_intc_complete(BCM2835VC4IntcState *s)
{
    s->active_vector = -1;
    s->active_priority = -1;
    vc4_intc_update(s);
}

static uint64_t vc4_intc_read(void *opaque, hwaddr offset, unsigned size)
{
    BCM2835VC4IntcState *s = opaque;
    uint32_t current;
    uint32_t next;

    if (offset >= VC4_INTC_MASK0 && offset <= VC4_INTC_MASK7) {
        return s->mask[(offset - VC4_INTC_MASK0) >> 2];
    }

    switch (offset) {
    case VC4_INTC_C:
        return s->control;
    case VC4_INTC_S:
        current = vc4_intc_status_field(
            s->active_vector >= 0 ? s->active_vector : s->pending_vector,
            s->active_vector >= 0 ? s->active_priority :
                                    s->pending_priority);
        next = vc4_intc_status_field(s->pending_vector,
                                     s->pending_priority);
        return current | (next << 16);
    case VC4_INTC_SRC0:
        return s->source[0] | s->force[0];
    case VC4_INTC_SRC1:
        return s->source[1] | s->force[1];
    case VC4_INTC_VADDR:
        return s->vaddr;
    case VC4_INTC_WAKEUP:
        return s->wakeup;
    case VC4_INTC_PROFILE:
        return s->profile;
    case VC4_INTC_FORCE0:
    case VC4_INTC_FORCE0_SET:
    case VC4_INTC_FORCE0_CLR:
        return s->force[0];
    case VC4_INTC_FORCE1:
    case VC4_INTC_FORCE1_SET:
    case VC4_INTC_FORCE1_CLR:
        return s->force[1];
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: bad read offset 0x%" HWADDR_PRIx "\n",
                      TYPE_BCM2835_VC4_INTC, offset);
        return 0;
    }
}

static void vc4_intc_write(void *opaque, hwaddr offset, uint64_t value,
                           unsigned size)
{
    BCM2835VC4IntcState *s = opaque;
    unsigned bank;

    if (offset >= VC4_INTC_MASK0 && offset <= VC4_INTC_MASK7) {
        s->mask[(offset - VC4_INTC_MASK0) >> 2] =
            value & 0x77777777u;
        vc4_intc_update(s);
        return;
    }

    switch (offset) {
    case VC4_INTC_C:
        s->control = value & 0xf;
        break;
    case VC4_INTC_VADDR:
        s->vaddr = value & 0xfffffe00u;
        break;
    case VC4_INTC_WAKEUP:
        s->wakeup = value & 0xfffffffeu;
        break;
    case VC4_INTC_PROFILE:
        s->profile = value & 0xffff;
        break;
    case VC4_INTC_FORCE0:
    case VC4_INTC_FORCE1:
        bank = (offset == VC4_INTC_FORCE1);
        s->force[bank] = value;
        break;
    case VC4_INTC_FORCE0_SET:
    case VC4_INTC_FORCE1_SET:
        bank = (offset == VC4_INTC_FORCE1_SET);
        s->force[bank] |= value;
        break;
    case VC4_INTC_FORCE0_CLR:
    case VC4_INTC_FORCE1_CLR:
        bank = (offset == VC4_INTC_FORCE1_CLR);
        s->force[bank] &= ~value;
        break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: bad write offset 0x%" HWADDR_PRIx
                      " value 0x%" PRIx64 "\n",
                      TYPE_BCM2835_VC4_INTC, offset, value);
        return;
    }

    vc4_intc_update(s);
}

static const MemoryRegionOps vc4_intc_ops = {
    .read = vc4_intc_read,
    .write = vc4_intc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
};

static void vc4_intc_reset(DeviceState *dev)
{
    BCM2835VC4IntcState *s = BCM2835_VC4_INTC(dev);

    s->control = 0;
    memset(s->source, 0, sizeof(s->source));
    memset(s->force, 0, sizeof(s->force));
    memset(s->mask, 0, sizeof(s->mask));
    s->vaddr = 0;
    s->wakeup = 0x10000000;
    s->profile = 0;
    s->active_vector = -1;
    s->pending_vector = -1;
    s->active_priority = -1;
    s->pending_priority = -1;
    qemu_set_irq(s->irq, 0);
}

static const VMStateDescription vmstate_vc4_intc = {
    .name = TYPE_BCM2835_VC4_INTC,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, BCM2835VC4IntcState),
        VMSTATE_UINT32_ARRAY(source, BCM2835VC4IntcState, 2),
        VMSTATE_UINT32_ARRAY(force, BCM2835VC4IntcState, 2),
        VMSTATE_UINT32_ARRAY(mask, BCM2835VC4IntcState, 8),
        VMSTATE_UINT32(vaddr, BCM2835VC4IntcState),
        VMSTATE_UINT32(wakeup, BCM2835VC4IntcState),
        VMSTATE_UINT32(profile, BCM2835VC4IntcState),
        VMSTATE_INT16(active_vector, BCM2835VC4IntcState),
        VMSTATE_INT16(pending_vector, BCM2835VC4IntcState),
        VMSTATE_INT8(active_priority, BCM2835VC4IntcState),
        VMSTATE_INT8(pending_priority, BCM2835VC4IntcState),
        VMSTATE_END_OF_LIST()
    },
};

static void vc4_intc_init(Object *obj)
{
    BCM2835VC4IntcState *s = BCM2835_VC4_INTC(obj);

    memory_region_init_io(&s->iomem, obj, &vc4_intc_ops, s,
                          TYPE_BCM2835_VC4_INTC, 0x100);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
    qdev_init_gpio_in_named(DEVICE(s), vc4_intc_set_irq,
                            BCM2835_VC4_INTC_GPU_IRQ,
                            BCM2835_VC4_INTC_NUM_IRQS);
}

static void vc4_intc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, vc4_intc_reset);
    dc->vmsd = &vmstate_vc4_intc;
}

static const TypeInfo vc4_intc_type_info = {
    .name = TYPE_BCM2835_VC4_INTC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835VC4IntcState),
    .instance_init = vc4_intc_init,
    .class_init = vc4_intc_class_init,
};

static void vc4_intc_register_types(void)
{
    type_register_static(&vc4_intc_type_info);
}

type_init(vc4_intc_register_types)
''')

write("target/vc4/cpu.h", r'''
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

enum {
    VC4_EXCP_ILLEGAL = 1,
    VC4_EXCP_IRQ,
};

typedef struct CPUArchState {
    uint32_t gpr[VC4_NUM_GPRS];
    uint32_t sr;
    uint32_t pc;

    /*
     * In exception mode architectural SP (r25) is banked onto r28.  The
     * first implementation materializes the alias on entry and writes the
     * updated exception stack pointer back to r28 on the outermost RTI.
     */
    uint32_t normal_sp;
    uint8_t exception_depth;

    /* Fields up to this point are cleared by a CPU reset. */
    struct {} end_reset_fields;
} CPUVC4State;

struct ArchCPU {
    CPUState parent_obj;
    CPUVC4State env;

    BCM2835VC4IntcState *intc;
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
''')

write("target/vc4/cpu.c", r'''
/*
 * QEMU VideoCore IV VPU CPU
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/qemu-print.h"
#include "cpu.h"
#include "exec/cputlb.h"
#include "exec/page-protection.h"
#include "exec/translation-block.h"
#include "exec/target_page.h"
#include "accel/tcg/cpu-ldst.h"
#include "accel/tcg/cpu-ops.h"
#include "tcg/debug-assert.h"
#include "hw/vc4/bcm2835_vc4_intc.h"

static void vc4_cpu_set_pc(CPUState *cs, vaddr value)
{
    cpu_env(cs)->pc = value;
}

static vaddr vc4_cpu_get_pc(CPUState *cs)
{
    return cpu_env(cs)->pc;
}

static TCGTBCPUState vc4_get_tb_cpu_state(CPUState *cs)
{
    CPUVC4State *env = cpu_env(cs);

    return (TCGTBCPUState) {
        .pc = env->pc,
        .flags = 0,
    };
}

static void vc4_cpu_synchronize_from_tb(CPUState *cs,
                                        const TranslationBlock *tb)
{
    tcg_debug_assert(!tcg_cflags_has(cs, CF_PCREL));
    cpu_env(cs)->pc = tb->pc;
}

static void vc4_restore_state_to_opc(CPUState *cs,
                                     const TranslationBlock *tb,
                                     const uint64_t *data)
{
    cpu_env(cs)->pc = data[0];
}

static bool vc4_cpu_has_work(CPUState *cs)
{
    CPUVC4State *env = cpu_env(cs);

    return (env->sr & VC4_SR_I) &&
           cpu_test_interrupt(cs, CPU_INTERRUPT_HARD);
}

static int vc4_cpu_mmu_index(CPUState *cs, bool ifetch)
{
    return 0;
}

static void vc4_cpu_reset_hold(Object *obj, ResetType type)
{
    VC4CPUClass *vcc = VC4_CPU_GET_CLASS(obj);
    CPUVC4State *env = cpu_env(CPU(obj));

    if (vcc->parent_phases.hold) {
        vcc->parent_phases.hold(obj, type);
    }

    memset(env, 0, offsetof(CPUVC4State, end_reset_fields));
}

static ObjectClass *vc4_cpu_class_by_name(const char *cpu_model)
{
    g_autofree char *typename = NULL;
    ObjectClass *oc;

    oc = object_class_by_name(cpu_model);
    if (oc && object_class_dynamic_cast(oc, TYPE_VC4_CPU)) {
        return oc;
    }

    typename = g_strdup_printf(VC4_CPU_TYPE_NAME("%s"), cpu_model);
    return object_class_by_name(typename);
}

static void vc4_cpu_realize(DeviceState *dev, Error **errp)
{
    CPUState *cs = CPU(dev);
    VC4CPUClass *vcc = VC4_CPU_GET_CLASS(dev);
    Error *local_err = NULL;

    cpu_exec_realizefn(cs, &local_err);
    if (local_err) {
        error_propagate(errp, local_err);
        return;
    }

    qemu_init_vcpu(cs);
    cpu_reset(cs);
    vcc->parent_realize(dev, errp);
}

static const gchar *vc4_gdb_arch_name(CPUState *cs)
{
    return "videocore4";
}

void vc4_cpu_dump_state(CPUState *cs, FILE *f, int flags)
{
    CPUVC4State *env = cpu_env(cs);
    int i;

    qemu_fprintf(f, "pc=%08x sr=%08x\n", env->pc, env->sr);
    for (i = 0; i < 28; i += 4) {
        qemu_fprintf(f,
                     "r%-2d=%08x r%-2d=%08x r%-2d=%08x r%-2d=%08x\n",
                     i, env->gpr[i],
                     i + 1, env->gpr[i + 1],
                     i + 2, env->gpr[i + 2],
                     i + 3, env->gpr[i + 3]);
    }
    qemu_fprintf(f,
                 "r28=%08x r29=%08x r30=%08x r31=%08x\n",
                 env->gpr[28], env->gpr[29], env->sr, env->pc);
}

hwaddr vc4_cpu_get_phys_addr_debug(CPUState *cs, vaddr addr)
{
    return addr;
}

static bool vc4_cpu_tlb_fill(CPUState *cs, vaddr addr, int size,
                             MMUAccessType access_type, int mmu_idx,
                             bool probe, uintptr_t retaddr)
{
    vaddr page = addr & TARGET_PAGE_MASK;

    tlb_set_page(cs, page, page, PAGE_READ | PAGE_WRITE | PAGE_EXEC,
                 mmu_idx, TARGET_PAGE_SIZE);
    return true;
}

static void vc4_cpu_set_irq(void *opaque, int irq, int level)
{
    CPUState *cs = CPU(opaque);

    if (level) {
        cpu_interrupt(cs, CPU_INTERRUPT_HARD);
    } else {
        cpu_reset_interrupt(cs, CPU_INTERRUPT_HARD);
    }
}

static void vc4_irq_push(CPUVC4State *env, uint32_t value)
{
    uint32_t sp = env->gpr[VC4_REG_SP] - 4;

    env->gpr[VC4_REG_SP] = sp;
    cpu_stl_le_data(env, sp, value);
}

static bool vc4_cpu_enter_irq(VC4CPU *cpu)
{
    CPUState *cs = CPU(cpu);
    CPUVC4State *env = &cpu->env;
    uint32_t vector;
    uint32_t vector_base;
    uint32_t vector_entry;
    uint32_t saved_sr = env->sr;

    if (!cpu->intc ||
        !bcm2835_vc4_intc_acknowledge(cpu->intc, &vector, &vector_base)) {
        return false;
    }

    if (env->exception_depth == 0) {
        env->normal_sp = env->gpr[VC4_REG_SP];
        env->gpr[VC4_REG_SP] = env->gpr[28];
    }

    /*
     * Hardware pushes PC and then SR onto the descending exception stack.
     * RTI therefore sees SR at *SP and PC at *(SP + 4).
     */
    vc4_irq_push(env, env->pc);
    vc4_irq_push(env, saved_sr);
    env->exception_depth++;

    vector_entry = cpu_ldl_le_data(env, vector_base + vector * 4);

    env->sr = saved_sr & ~(VC4_SR_U | VC4_SR_I | VC4_SR_S);
    if (vector_entry & 1) {
        env->sr |= VC4_SR_S;
    }
    env->pc = vector_entry & ~1u;
    cs->halted = 0;
    return true;
}

static void vc4_cpu_do_interrupt(CPUState *cs)
{
    CPUVC4State *env = cpu_env(cs);

    switch (cs->exception_index) {
    case VC4_EXCP_IRQ:
        if (!vc4_cpu_enter_irq(VC4_CPU(cs))) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "VideoCore IV: spurious external interrupt\n");
        }
        break;
    case VC4_EXCP_ILLEGAL:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VideoCore IV: illegal instruction at 0x%08x\n",
                      env->pc);
        cs->halted = 1;
        break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VideoCore IV: unknown exception %d at 0x%08x\n",
                      cs->exception_index, env->pc);
        cs->halted = 1;
        break;
    }

    cs->exception_index = -1;
}

static bool vc4_cpu_exec_interrupt(CPUState *cs, int interrupt_request)
{
    CPUVC4State *env = cpu_env(cs);

    if ((interrupt_request & CPU_INTERRUPT_HARD) &&
        (env->sr & VC4_SR_I)) {
        cs->exception_index = VC4_EXCP_IRQ;
        vc4_cpu_do_interrupt(cs);
        return true;
    }

    return false;
}

static void vc4_cpu_init(Object *obj)
{
    qdev_init_gpio_in(DEVICE(obj), vc4_cpu_set_irq, 1);
}

#include "hw/core/sysemu-cpu-ops.h"

static const struct SysemuCPUOps vc4_sysemu_ops = {
    .has_work = vc4_cpu_has_work,
    .get_phys_addr_debug = vc4_cpu_get_phys_addr_debug,
};

static const TCGCPUOps vc4_tcg_ops = {
    .guest_default_memory_order = TCG_MO_ALL,
    .mttcg_supported = false,

    .initialize = vc4_translate_init,
    .translate_code = vc4_translate_code,
    .get_tb_cpu_state = vc4_get_tb_cpu_state,
    .synchronize_from_tb = vc4_cpu_synchronize_from_tb,
    .restore_state_to_opc = vc4_restore_state_to_opc,
    .mmu_index = vc4_cpu_mmu_index,
    .tlb_fill = vc4_cpu_tlb_fill,
    .pointer_wrap = cpu_pointer_wrap_uint32,

    .cpu_exec_interrupt = vc4_cpu_exec_interrupt,
    .cpu_exec_halt = vc4_cpu_has_work,
    .cpu_exec_reset = cpu_reset,
    .do_interrupt = vc4_cpu_do_interrupt,
};

static void vc4_cpu_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    CPUClass *cc = CPU_CLASS(klass);
    VC4CPUClass *vcc = VC4_CPU_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    device_class_set_parent_realize(dc, vc4_cpu_realize,
                                    &vcc->parent_realize);
    resettable_class_set_parent_phases(rc, NULL, vc4_cpu_reset_hold, NULL,
                                       &vcc->parent_phases);

    cc->class_by_name = vc4_cpu_class_by_name;
    cc->dump_state = vc4_cpu_dump_state;
    cc->set_pc = vc4_cpu_set_pc;
    cc->get_pc = vc4_cpu_get_pc;
    cc->gdb_read_register = vc4_cpu_gdb_read_register;
    cc->gdb_write_register = vc4_cpu_gdb_write_register;
    cc->gdb_num_core_regs = VC4_NUM_REGS;
    cc->gdb_arch_name = vc4_gdb_arch_name;
    cc->sysemu_ops = &vc4_sysemu_ops;
    cc->tcg_ops = &vc4_tcg_ops;
}

static const TypeInfo vc4_cpu_types[] = {
    {
        .name = TYPE_VC4_CPU,
        .parent = TYPE_CPU,
        .instance_size = sizeof(VC4CPU),
        .instance_align = __alignof(VC4CPU),
        .instance_init = vc4_cpu_init,
        .abstract = true,
        .class_size = sizeof(VC4CPUClass),
        .class_init = vc4_cpu_class_init,
    },
    {
        .name = TYPE_VC4_VPU_CPU,
        .parent = TYPE_VC4_CPU,
    },
};

DEFINE_TYPES(vc4_cpu_types)
''')

write("target/vc4/helper.h", r'''
/*
 * VideoCore IV VPU TCG helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

DEF_HELPER_FLAGS_3(complex_alu, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32)
DEF_HELPER_FLAGS_4(div, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_FLAGS_4(mulhd, TCG_CALL_NO_RWG_SE,
                   i32, i32, i32, i32, i32)
DEF_HELPER_5(push_pop, void, env, i32, i32, i32, i32)
DEF_HELPER_1(rti, void, env)
DEF_HELPER_3(raise_illegal, noreturn, env, i32, i32)
DEF_HELPER_1(halt, noreturn, env)
''')

replace("target/vc4/op_helper.c",
        '#include "cpu.h"\n#include "exec/helper-proto.h"',
        '#include "cpu.h"\n#include "hw/vc4/bcm2835_vc4_intc.h"\n'
        '#include "exec/helper-proto.h"')

replace("target/vc4/op_helper.c",
r'''G_NORETURN void helper_raise_illegal(CPUVC4State *env, uint32_t pc,
                                     uint32_t opcode)
''',
r'''void helper_rti(CPUVC4State *env)
{
    VC4CPU *cpu = env_archcpu(env);
    uint32_t sp = env->gpr[VC4_REG_SP];

    env->sr = cpu_ldl_le_data(env, sp);
    env->pc = cpu_ldl_le_data(env, sp + 4);
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

G_NORETURN void helper_raise_illegal(CPUVC4State *env, uint32_t pc,
                                     uint32_t opcode)
''')

replace("target/vc4/translate.c",
r'''    switch (insn) {
    case 0x0000:
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        gen_helper_halt(tcg_env);
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    case 0x0001:                    /* NOP */
        return true;
    case 0x0004:
        tcg_gen_ori_i32(cpu_sr, cpu_sr, 0x40000000);
        return true;
    case 0x0005:
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~0x40000000u);
        return true;
    case 0x000a:                    /* RTI: exception model not wired yet */
        return false;
    default:
        break;
    }
''',
r'''    switch (insn) {
    case 0x0000:                    /* BKPT/HALT */
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        gen_helper_halt(tcg_env);
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    case 0x0001:                    /* NOP */
        return true;
    case 0x0002:                    /* SLEEP */
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        gen_helper_halt(tcg_env);
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    case 0x0003:                    /* USER */
        tcg_gen_ori_i32(cpu_sr, cpu_sr, VC4_SR_U);
        return true;
    case 0x0004:                    /* EI */
        tcg_gen_ori_i32(cpu_sr, cpu_sr, VC4_SR_I);
        return true;
    case 0x0005:                    /* DI */
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~VC4_SR_I);
        return true;
    case 0x0006:                    /* CBCLR */
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~VC4_SR_CB_MASK);
        return true;
    case 0x0007:
    case 0x0008:
    case 0x0009: {                  /* CBADD1/2/3 */
        TCGv_i32 cb = tcg_temp_new_i32();

        tcg_gen_extract_i32(cb, cpu_sr, 4, 2);
        tcg_gen_addi_i32(cb, cb, insn - 0x0006);
        tcg_gen_andi_i32(cb, cb, 3);
        tcg_gen_andi_i32(cpu_sr, cpu_sr, ~VC4_SR_CB_MASK);
        tcg_gen_shli_i32(cb, cb, 4);
        tcg_gen_or_i32(cpu_sr, cpu_sr, cb);
        return true;
    }
    case 0x000a:                    /* RTI */
        gen_helper_rti(tcg_env);
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    default:
        break;
    }
''')

write("hw/vc4/raspi3_vpu.c", r'''
/*
 * VideoCore-first Raspberry Pi 3B bring-up machine
 *
 * This machine exposes the BCM283x GPU-bus view to a VideoCore IV VPU.  It is
 * deliberately VC4-only until QEMU can safely run ARM and VC4 TCG CPUs in one
 * process.  The hardware boundary is kept compatible with the existing Pi
 * peripheral models so it can become the firmware side of the normal raspi3b
 * machine later.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/units.h"
#include "hw/core/boards.h"
#include "hw/core/loader.h"
#include "hw/core/sysbus.h"
#include "hw/arm/bcm2835_peripherals.h"
#include "hw/vc4/bcm2835_vc4_intc.h"
#include "system/memory.h"
#include "system/qtest.h"
#include "target/vc4/cpu.h"

#define TYPE_VC4_RASPI3_MACHINE MACHINE_TYPE_NAME("raspi3b-vc4")
OBJECT_DECLARE_SIMPLE_TYPE(VC4Raspi3MachineState, VC4_RASPI3_MACHINE)

#define RASPI3_BOARD_REVISION 0x00a02082u
#define RASPI3_DEFAULT_VCRAM  (64 * MiB)
#define VC4_IC0_OFFSET 0x2000
#define VC4_IC1_OFFSET 0x2800

struct VC4Raspi3MachineState {
    MachineState parent_obj;

    BCM2835PeripheralState peripherals;
    BCM2835VC4IntcState vpu_intc[2];
};

static void vc4_raspi3_machine_initfn(Object *obj)
{
    VC4Raspi3MachineState *s = VC4_RASPI3_MACHINE(obj);

    object_initialize_child(obj, "peripherals", &s->peripherals,
                            TYPE_BCM2835_PERIPHERALS);
    object_initialize_child(obj, "vpu-intc0", &s->vpu_intc[0],
                            TYPE_BCM2835_VC4_INTC);
    object_initialize_child(obj, "vpu-intc1", &s->vpu_intc[1],
                            TYPE_BCM2835_VC4_INTC);
}

static void vc4_raspi3_init(MachineState *machine)
{
    VC4Raspi3MachineState *s = VC4_RASPI3_MACHINE(machine);
    BCMSocPeripheralBaseState *ps =
        BCM_SOC_PERIPHERALS_BASE(&s->peripherals);
    MemoryRegion *sysmem = get_system_memory();
    Object *ram_obj = OBJECT(machine->ram);
    VC4CPU *cpu;
    const char *image = machine->kernel_filename;
    uint64_t vcram_size;
    ssize_t image_size;
    unsigned i;

    if (machine->ram_size < 16 * MiB) {
        error_report("raspi3b-vc4 requires at least 16 MiB of RAM");
        exit(EXIT_FAILURE);
    }

    vcram_size = MIN((uint64_t)RASPI3_DEFAULT_VCRAM,
                     machine->ram_size / 2);

    object_property_add_const_link(OBJECT(&s->peripherals), "ram", ram_obj);
    object_property_set_int(OBJECT(&s->peripherals), "board-rev",
                            RASPI3_BOARD_REVISION, &error_abort);
    object_property_set_int(OBJECT(&s->peripherals), "vcram-size",
                            vcram_size, &error_abort);
    object_property_set_int(OBJECT(&s->peripherals), "vcram-base",
                            machine->ram_size - vcram_size, &error_abort);

    if (!sysbus_realize(SYS_BUS_DEVICE(&s->peripherals), &error_fatal)) {
        g_assert_not_reached();
    }

    for (i = 0; i < ARRAY_SIZE(s->vpu_intc); i++) {
        hwaddr offset = i ? VC4_IC1_OFFSET : VC4_IC0_OFFSET;

        if (!sysbus_realize(SYS_BUS_DEVICE(&s->vpu_intc[i]),
                            &error_fatal)) {
            g_assert_not_reached();
        }
        memory_region_add_subregion_overlap(
            &ps->peri_mr, offset,
            sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->vpu_intc[i]), 0), 0);
    }

    /*
     * bcm2835_ic receives the raw 64-source GPU interrupt fabric for the ARM
     * side.  Its mirror outputs let the VPU controller observe the same
     * sources without changing every peripheral connection.
     */
    for (i = 0; i < BCM2835_VC4_INTC_NUM_IRQS; i++) {
        qdev_connect_gpio_out_named(
            DEVICE(&ps->ic), BCM2835_IC_GPU_IRQ_OUT, i,
            qdev_get_gpio_in_named(DEVICE(&s->vpu_intc[0]),
                                   BCM2835_VC4_INTC_GPU_IRQ, i));
    }

    /*
     * The VideoCore sees RAM through four cache-policy aliases and sees the
     * peripheral window at 0x7e000000.  bcm2835_peripherals already constructs
     * precisely that 4 GiB GPU-bus container; make it the VPU CPU's physical
     * address space instead of mapping the ARM-side 0x3f000000 window.
     */
    memory_region_add_subregion(sysmem, 0, &ps->gpu_bus_mr);

    cpu = VC4_CPU(cpu_create(machine->cpu_type));
    if (!cpu) {
        error_report("Unable to create VideoCore IV VPU CPU");
        exit(EXIT_FAILURE);
    }

    cpu->intc = &s->vpu_intc[0];
    sysbus_connect_irq(SYS_BUS_DEVICE(&s->vpu_intc[0]), 0,
                       qdev_get_gpio_in(DEVICE(cpu), 0));

    if (!image) {
        image = machine->firmware;
    }

    if (image) {
        image_size = load_image_targphys(image, 0, machine->ram_size, NULL);
        if (image_size < 0) {
            error_report("Could not load VideoCore IV firmware '%s'", image);
            exit(EXIT_FAILURE);
        }
    } else if (!qtest_enabled()) {
        warn_report("no VideoCore IV firmware supplied; starting with zeroed RAM");
    }

    cpu->env.pc = 0;
}

static void vc4_raspi3_machine_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "Raspberry Pi 3B (VideoCore IV firmware-side bring-up)";
    mc->init = vc4_raspi3_init;
    mc->default_cpu_type = TYPE_VC4_VPU_CPU;
    mc->default_ram_size = 1 * GiB;
    mc->default_ram_id = "raspi3b-vc4.ram";
    mc->min_cpus = 1;
    mc->max_cpus = 1;
    mc->default_cpus = 1;
}

static const TypeInfo vc4_raspi3_machine_type = {
    .name = TYPE_VC4_RASPI3_MACHINE,
    .parent = TYPE_MACHINE,
    .instance_size = sizeof(VC4Raspi3MachineState),
    .instance_init = vc4_raspi3_machine_initfn,
    .class_init = vc4_raspi3_machine_class_init,
};

static void vc4_raspi3_machine_register_types(void)
{
    type_register_static(&vc4_raspi3_machine_type);
}

type_init(vc4_raspi3_machine_register_types)
''')

write("hw/vc4/meson.build", r'''
vc4_ss = ss.source_set()
vc4_ss.add(when: 'CONFIG_VC4_VPU', if_true: files(
  'vc4_vpu.c',
  'bcm2835_vc4_intc.c',
))
vc4_ss.add(when: 'CONFIG_VC4_RASPI3', if_true: files(
  'raspi3_vpu.c',
  '../arm/bcm2835_peripherals.c',
))

hw_arch += {'vc4': vc4_ss}
''')

replace("include/hw/intc/bcm2835_ic.h",
        '#define BCM2835_IC_ARM_IRQ "arm-irq"\n',
        '#define BCM2835_IC_ARM_IRQ "arm-irq"\n'
        '#define BCM2835_IC_GPU_IRQ_OUT "gpu-irq-out"\n')

replace("include/hw/intc/bcm2835_ic.h",
        '    qemu_irq irq;\n    qemu_irq fiq;\n',
        '    qemu_irq irq;\n    qemu_irq fiq;\n'
        '    qemu_irq gpu_irq_out[64];\n')

replace("hw/intc/bcm2835_ic.c",
r'''    s->gpu_irq_level = deposit64(s->gpu_irq_level, irq, 1, level != 0);
    bcm2835_ic_update(s);
''',
r'''    s->gpu_irq_level = deposit64(s->gpu_irq_level, irq, 1, level != 0);
    qemu_set_irq(s->gpu_irq_out[irq], level);
    bcm2835_ic_update(s);
''')

replace("hw/intc/bcm2835_ic.c",
r'''    qdev_init_gpio_in_named(DEVICE(s), bcm2835_ic_set_arm_irq,
                            BCM2835_IC_ARM_IRQ, ARM_IRQS);

    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
''',
r'''    qdev_init_gpio_in_named(DEVICE(s), bcm2835_ic_set_arm_irq,
                            BCM2835_IC_ARM_IRQ, ARM_IRQS);
    qdev_init_gpio_out_named(DEVICE(s), s->gpu_irq_out,
                             BCM2835_IC_GPU_IRQ_OUT, GPU_IRQS);

    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
''')

replace("hw/intc/bcm2835_ic.c",
r'''static void bcm2835_ic_reset(DeviceState *d)
{
    BCM2835ICState *s = BCM2835_IC(d);

    s->gpu_irq_enable = 0;
''',
r'''static void bcm2835_ic_reset(DeviceState *d)
{
    BCM2835ICState *s = BCM2835_IC(d);
    int i;

    for (i = 0; i < GPU_IRQS; i++) {
        qemu_set_irq(s->gpu_irq_out[i], 0);
    }
    s->gpu_irq_enable = 0;
''')
