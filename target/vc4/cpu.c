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
#include "accel/tcg/cpu-ops.h"
#include "tcg/debug-assert.h"

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
    return cpu_test_interrupt(cs, CPU_INTERRUPT_HARD);
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
    for (i = 0; i < VC4_NUM_REGS; i += 4) {
        qemu_fprintf(f,
                     "r%-2d=%08x r%-2d=%08x r%-2d=%08x r%-2d=%08x\n",
                     i, vc4_env_get_reg(env, i),
                     i + 1, vc4_env_get_reg(env, i + 1),
                     i + 2, vc4_env_get_reg(env, i + 2),
                     i + 3, vc4_env_get_reg(env, i + 3));
    }
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

static bool vc4_cpu_exec_interrupt(CPUState *cs, int interrupt_request)
{
    return false;
}

static void vc4_cpu_do_interrupt(CPUState *cs)
{
    CPUVC4State *env = cpu_env(cs);

    if (cs->exception_index == VC4_EXCP_ILLEGAL) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VideoCore IV: illegal instruction at 0x%08x\n",
                      env->pc);
        cs->halted = 1;
    }
    cs->exception_index = -1;
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
