#!/usr/bin/env python3
"""Initialize every linked TCG frontend before the first vCPU thread.

TCG globals are copied from the initial context when the execution thread is
created.  Initializing a second guest frontend after the first CPU is realized
therefore leaves that thread with an incomplete global table.  Heterogeneous
machines use the small API added here to register every frontend before either
CPU is realized.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


# Public-to-TCG-internal API used by machines which link more than one guest
# frontend into a single executable.
ops_h = ROOT / "include/accel/tcg/cpu-ops.h"
replace_once(
    ops_h,
    """};

/**
 * cpu_check_watchpoint:
""",
    """};

/**
 * tcg_exec_initialize_frontend:
 * @tcg_ops: frontend operations to initialize
 *
 * Register one guest frontend's TCG globals.  A heterogeneous machine must
 * call this for every frontend it will instantiate before realizing its first
 * CPU, because qemu_init_vcpu() copies the initial TCG context into the
 * execution thread.
 */
void tcg_exec_initialize_frontend(const TCGCPUOps *tcg_ops);

/**
 * cpu_check_watchpoint:
""",
    "heterogeneous frontend initialization declaration",
)


# Factor the existing per-TCGCPUOps initialization out of CPU realization so a
# machine can populate the complete global table before qemu_init_vcpu().
tcg_exec = ROOT / "accel/tcg/cpu-exec.c"
replace_once(
    tcg_exec,
    """bool tcg_exec_realizefn(CPUState *cpu, Error **errp)
{
    static GHashTable *tcg_initialized_ops;
    const TCGCPUOps *tcg_ops = cpu->cc->tcg_ops;

    if (!tcg_initialized_ops) {
        tcg_initialized_ops = g_hash_table_new(g_direct_hash, g_direct_equal);
    }

    if (!g_hash_table_contains(tcg_initialized_ops, tcg_ops)) {
        /* Check mandatory TCGCPUOps handlers for every linked frontend. */
#ifndef CONFIG_USER_ONLY
        assert(tcg_ops->cpu_exec_halt);
        assert(tcg_ops->cpu_exec_interrupt);
        assert(tcg_ops->cpu_exec_reset);
        assert(tcg_ops->pointer_wrap);
#endif /* !CONFIG_USER_ONLY */
        assert(tcg_ops->translate_code);
        assert(tcg_ops->get_tb_cpu_state);
        assert(tcg_ops->mmu_index);
        tcg_ops->initialize();
        g_hash_table_add(tcg_initialized_ops, (void *)tcg_ops);
    }
""",
    """static GHashTable *tcg_initialized_ops;

void tcg_exec_initialize_frontend(const TCGCPUOps *tcg_ops)
{
    if (!tcg_initialized_ops) {
        tcg_initialized_ops = g_hash_table_new(g_direct_hash, g_direct_equal);
    }

    if (g_hash_table_contains(tcg_initialized_ops, tcg_ops)) {
        return;
    }

    /* Check mandatory TCGCPUOps handlers for every linked frontend. */
#ifndef CONFIG_USER_ONLY
    assert(tcg_ops->cpu_exec_halt);
    assert(tcg_ops->cpu_exec_interrupt);
    assert(tcg_ops->cpu_exec_reset);
    assert(tcg_ops->pointer_wrap);
#endif /* !CONFIG_USER_ONLY */
    assert(tcg_ops->translate_code);
    assert(tcg_ops->get_tb_cpu_state);
    assert(tcg_ops->mmu_index);
    tcg_ops->initialize();
    g_hash_table_add(tcg_initialized_ops, (gpointer)tcg_ops);
}

bool tcg_exec_realizefn(CPUState *cpu, Error **errp)
{
    const TCGCPUOps *tcg_ops = cpu->cc->tcg_ops;

    tcg_exec_initialize_frontend(tcg_ops);
""",
    "pre-realization TCG frontend initializer",
)


# The simple collision machine used cpu_create(), which realizes each CPU
# immediately.  Construct both objects first, initialize both frontends, then
# realize them in the original ARM-first order.
hetero = ROOT / "hw/arm/vc4_hetero.c"
replace_once(
    hetero,
    '#include "hw/core/loader.h"\n',
    '#include "hw/core/loader.h"\n#include "accel/tcg/cpu-ops.h"\n',
    "TCG CPU operations include in collision machine",
)
replace_once(
    hetero,
    """    CPUState *arm;
    CPUState *vc4;

    memory_region_add_subregion(sysmem, 0, machine->ram);
    rom_add_blob_fixed("vc4-hetero-polyglot", polyglot,
                       sizeof(polyglot), 0);

    arm = cpu_create(machine->cpu_type);
    vc4 = cpu_create(TYPE_VC4_VPU_CPU);

    vc4_hetero_set_pc(arm, 0);
""",
    """    Object *arm_obj;
    Object *vc4_obj;
    CPUState *arm;
    CPUState *vc4;

    memory_region_add_subregion(sysmem, 0, machine->ram);
    rom_add_blob_fixed("vc4-hetero-polyglot", polyglot,
                       sizeof(polyglot), 0);

    arm_obj = object_new(machine->cpu_type);
    vc4_obj = object_new(TYPE_VC4_VPU_CPU);
    arm = CPU(arm_obj);
    vc4 = CPU(vc4_obj);

    tcg_exec_initialize_frontend(CPU_GET_CLASS(arm)->tcg_ops);
    tcg_exec_initialize_frontend(CPU_GET_CLASS(vc4)->tcg_ops);

    if (!qdev_realize(DEVICE(arm), NULL, &error_fatal)) {
        g_assert_not_reached();
    }
    if (!qdev_realize(DEVICE(vc4), NULL, &error_fatal)) {
        g_assert_not_reached();
    }

    vc4_hetero_set_pc(arm, 0);
""",
    "collision-machine CPU construction order",
)


# The release fixture intentionally realizes VC4 first, but it too must create
# both objects and register both TCGCPUOps tables before either realization.
release = ROOT / "hw/arm/vc4_arm_release_smoke.c"
replace_once(
    release,
    '#include "hw/core/sysbus.h"\n',
    '#include "hw/core/sysbus.h"\n#include "accel/tcg/cpu-ops.h"\n',
    "TCG CPU operations include in release machine",
)
replace_once(
    release,
    """    s->vc4_cpu = vc4_arm_release_new_vpu();
    s->vc4_cpu->start_powered_off = false;
    if (!qdev_realize(DEVICE(s->vc4_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }

    arm_obj = object_new(ARM_CPU_TYPE_NAME("cortex-a53"));
    s->arm_cpu = CPU(arm_obj);
    s->arm_cpu->start_powered_off = true;
    if (!qdev_realize(DEVICE(s->arm_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }
""",
    """    s->vc4_cpu = vc4_arm_release_new_vpu();
    arm_obj = object_new(ARM_CPU_TYPE_NAME("cortex-a53"));
    s->arm_cpu = CPU(arm_obj);

    tcg_exec_initialize_frontend(CPU_GET_CLASS(s->vc4_cpu)->tcg_ops);
    tcg_exec_initialize_frontend(CPU_GET_CLASS(s->arm_cpu)->tcg_ops);

    s->vc4_cpu->start_powered_off = false;
    if (!qdev_realize(DEVICE(s->vc4_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }

    s->arm_cpu->start_powered_off = true;
    if (!qdev_realize(DEVICE(s->arm_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }
""",
    "release-machine frontend preinitialization",
)
