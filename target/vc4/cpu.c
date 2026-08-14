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
#include "system/tcg.h"
#include "hw/core/qdev-properties.h"
#include "hw/vc4/bcm2835_vc4_intc.h"

QEMU_BUILD_BUG_ON(offsetof(VC4CPU, parent_obj) != 0);
QEMU_BUILD_BUG_ON(offsetof(VC4CPU, env) != sizeof(CPUState));

static inline CPUArchState *vc4_tcg_env(CPUState *cs)
{
    return (CPUArchState *)(void *)vc4_cpu_env(cs);
}

static void vc4_cpu_set_pc(CPUState *cs, vaddr value)
{
    vc4_cpu_env(cs)->pc = value;
}

static vaddr vc4_cpu_get_pc(CPUState *cs)
{
    return vc4_cpu_env(cs)->pc;
}

static TCGTBCPUState vc4_get_tb_cpu_state(CPUState *cs)
{
    CPUVC4State *env = vc4_cpu_env(cs);

    return (TCGTBCPUState) {
        .pc = env->pc,
        .flags = 0,
    };
}

static void vc4_cpu_synchronize_from_tb(CPUState *cs,
                                        const TranslationBlock *tb)
{
    tcg_debug_assert(!tcg_cflags_has(cs, CF_PCREL));
    vc4_cpu_env(cs)->pc = tb->pc;
}

static void vc4_restore_state_to_opc(CPUState *cs,
                                     const TranslationBlock *tb,
                                     const uint64_t *data)
{
    vc4_cpu_env(cs)->pc = data[0];
}

static bool vc4_cpu_has_work(CPUState *cs)
{
    CPUVC4State *env = vc4_cpu_env(cs);

    return (env->sr & VC4_SR_I) &&
           cpu_test_interrupt(cs, CPU_INTERRUPT_HARD);
}

static bool vc4_cpu_debug_halted(Object *obj, Error **errp)
{
    return CPU(obj)->halted;
}

static bool vc4_cpu_debug_stop(Object *obj, Error **errp)
{
    return CPU(obj)->stop;
}

static bool vc4_cpu_debug_stopped(Object *obj, Error **errp)
{
    return CPU(obj)->stopped;
}

static bool vc4_cpu_debug_exit_request(Object *obj, Error **errp)
{
    return qatomic_read(&CPU(obj)->exit_request);
}

static bool vc4_cpu_debug_thread_kicked(Object *obj, Error **errp)
{
    return qatomic_read(&CPU(obj)->thread_kicked);
}

static bool vc4_cpu_debug_hard_interrupt(Object *obj, Error **errp)
{
    return cpu_test_interrupt(CPU(obj), CPU_INTERRUPT_HARD);
}

static bool vc4_cpu_debug_has_work(Object *obj, Error **errp)
{
    return vc4_cpu_has_work(CPU(obj));
}

static int vc4_cpu_mmu_index(CPUState *cs, bool ifetch)
{
    return 0;
}

static void vc4_cpu_reset_hold(Object *obj, ResetType type)
{
    VC4CPU *cpu = VC4_CPU(obj);
    VC4CPUClass *vcc = VC4_CPU_GET_CLASS(obj);
    CPUVC4State *env = vc4_cpu_env(CPU(obj));

    if (vcc->parent_phases.hold) {
        vcc->parent_phases.hold(obj, type);
    }

    memset(env, 0, offsetof(CPUVC4State, end_reset_fields));
    env->pc = cpu->reset_pc;
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

    if (qemu_tcg_mttcg_enabled()) {
        error_setg(errp,
                   "VideoCore IV requires single-threaded TCG for now");
        return;
    }

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

static int64_t vc4_cpu_get_arch_id(CPUState *cs)
{
    return cs->cpu_index;
}

void vc4_cpu_dump_state(CPUState *cs, FILE *f, int flags)
{
    CPUVC4State *env = vc4_cpu_env(cs);
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

static void vc4_irq_push(CPUState *cs, CPUVC4State *env, uint32_t value)
{
    uint32_t sp = env->gpr[VC4_REG_SP] - 4;

    env->gpr[VC4_REG_SP] = sp;
    cpu_stl_le_data(vc4_tcg_env(cs), sp, value);
}

static bool vc4_cpu_enter_irq(VC4CPU *cpu)
{
    CPUState *cs = CPU(cpu);
    CPUVC4State *env = vc4_cpu_env(cs);
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

    vc4_irq_push(cs, env, env->pc);
    vc4_irq_push(cs, env, saved_sr);
    env->exception_depth++;

    vector_entry = cpu_ldl_le_data(vc4_tcg_env(cs),
                                   vector_base + vector * 4);

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
    CPUVC4State *env = vc4_cpu_env(cs);

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
    CPUVC4State *env = vc4_cpu_env(cs);

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

static const Property vc4_cpu_properties[] = {
    DEFINE_PROP_UINT32("reset-pc", VC4CPU, reset_pc, 0),
};

static void vc4_cpu_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    CPUClass *cc = CPU_CLASS(klass);
    VC4CPUClass *vcc = VC4_CPU_CLASS(klass);
    ResettableClass *rc = RESETTABLE_CLASS(klass);

    device_class_set_parent_realize(dc, vc4_cpu_realize,
                                    &vcc->parent_realize);
    device_class_set_props(dc, vc4_cpu_properties);
    resettable_class_set_parent_phases(rc, NULL, vc4_cpu_reset_hold, NULL,
                                       &vcc->parent_phases);

    object_class_property_add_bool(klass, "vc4-debug-halted",
                                   vc4_cpu_debug_halted, NULL);
    object_class_property_add_bool(klass, "vc4-debug-stop",
                                   vc4_cpu_debug_stop, NULL);
    object_class_property_add_bool(klass, "vc4-debug-stopped",
                                   vc4_cpu_debug_stopped, NULL);
    object_class_property_add_bool(klass, "vc4-debug-exit-request",
                                   vc4_cpu_debug_exit_request, NULL);
    object_class_property_add_bool(klass, "vc4-debug-thread-kicked",
                                   vc4_cpu_debug_thread_kicked, NULL);
    object_class_property_add_bool(klass, "vc4-debug-hard-interrupt",
                                   vc4_cpu_debug_hard_interrupt, NULL);
    object_class_property_add_bool(klass, "vc4-debug-has-work",
                                   vc4_cpu_debug_has_work, NULL);

    cc->class_by_name = vc4_cpu_class_by_name;
    cc->dump_state = vc4_cpu_dump_state;
    cc->get_arch_id = vc4_cpu_get_arch_id;
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
