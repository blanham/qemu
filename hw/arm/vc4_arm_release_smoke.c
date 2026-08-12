/*
 * VC4-controlled AArch64 release regression machine
 *
 * This is a deliberately small heterogeneous-TCG test fixture.  A VideoCore
 * IV VPU starts first, programs an ARM entry address through MMIO, and releases
 * a Cortex-A53 that was held powered off.  The device-facing contract is kept
 * separate from the eventual BCM2837 power-management wiring so the mixed-ISA
 * execution and wakeup semantics can be tested in isolation.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "qemu/units.h"
#include "hw/core/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/core/loader.h"
#include "hw/core/sysbus.h"
#include "accel/tcg/cpu-ops.h"
#include "system/cpus.h"
#include "system/memory.h"
#include "target/arm/cpu.h"
#include "target/arm/arm-powerctl.h"

#define TYPE_VC4_ARM_RELEASE_MACHINE \
    MACHINE_TYPE_NAME("vc4-arm-release-smoke")
OBJECT_DECLARE_SIMPLE_TYPE(VC4ArmReleaseMachineState,
                           VC4_ARM_RELEASE_MACHINE)

#define VC4_ARM_RELEASE_BASE UINT64_C(0x10000000)
#define VC4_ARM_RELEASE_SIZE 0x1000

#define RELEASE_ENTRY_LO 0x00
#define RELEASE_ENTRY_HI 0x04
#define RELEASE_CONTROL  0x08
#define RELEASE_STATUS   0x0c
#define RELEASE_COUNT    0x10
#define RELEASE_VC4_PC   0x14
#define RELEASE_VC4_RUN  0x18

#define RELEASE_CONTROL_GO  (1u << 0)
#define RELEASE_STATUS_DONE (1u << 0)

struct VC4ArmReleaseMachineState {
    MachineState parent_obj;

    MemoryRegion release_mr;
    CPUState *arm_cpu;
    CPUState *vc4_cpu;

    uint64_t arm_entry;
    uint32_t control;
    uint32_t status;
    uint32_t release_count;
};

static void vc4_arm_release_cpu(VC4ArmReleaseMachineState *s)
{
    ARMCPU *arm_cpu;
    uint64_t cpuid;
    int ret;

    if (s->status & RELEASE_STATUS_DONE) {
        return;
    }
    if (!s->arm_cpu) {
        error_report("vc4-arm-release-smoke: no ARM CPU to release");
        return;
    }

    /*
     * Use the architectural ARM power-control path rather than clearing only
     * CPUState::halted/stopped.  A start-powered-off ARMCPU remains in
     * PSCI_OFF until arm_set_cpu_on() runs the reset and power transition in
     * the target CPU's execution context.
     */
    arm_cpu = ARM_CPU(s->arm_cpu);
    cpuid = arm_cpu_mp_affinity(arm_cpu);
    ret = arm_set_cpu_on(cpuid, s->arm_entry, 0, 3, true);
    if (ret != QEMU_ARM_POWERCTL_RET_SUCCESS) {
        error_report("vc4-arm-release-smoke: ARM CPU release failed: %d", ret);
        return;
    }

    s->status |= RELEASE_STATUS_DONE;
    s->release_count++;
}

static uint64_t vc4_arm_release_read(void *opaque, hwaddr offset,
                                     unsigned size)
{
    VC4ArmReleaseMachineState *s = opaque;

    switch (offset) {
    case RELEASE_ENTRY_LO:
        return (uint32_t)s->arm_entry;
    case RELEASE_ENTRY_HI:
        return s->arm_entry >> 32;
    case RELEASE_CONTROL:
        return s->control;
    case RELEASE_STATUS:
        return s->status;
    case RELEASE_COUNT:
        return s->release_count;
    case RELEASE_VC4_PC: {
        CPUClass *cc;

        if (!s->vc4_cpu) {
            return UINT32_MAX;
        }
        cc = CPU_GET_CLASS(s->vc4_cpu);
        return cc->get_pc ? cc->get_pc(s->vc4_cpu) : UINT32_MAX;
    }
    case RELEASE_VC4_RUN:
        if (!s->vc4_cpu) {
            return UINT32_MAX;
        }
        return (s->vc4_cpu->halted ? 1u : 0u) |
               (s->vc4_cpu->stopped ? 2u : 0u) |
               (s->vc4_cpu->start_powered_off ? 4u : 0u);
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "vc4-arm-release-smoke: bad read offset 0x%" HWADDR_PRIx
                      "\n", offset);
        return 0;
    }
}

static void vc4_arm_release_write(void *opaque, hwaddr offset,
                                  uint64_t value, unsigned size)
{
    VC4ArmReleaseMachineState *s = opaque;

    switch (offset) {
    case RELEASE_ENTRY_LO:
        s->arm_entry = deposit64(s->arm_entry, 0, 32, value);
        break;
    case RELEASE_ENTRY_HI:
        s->arm_entry = deposit64(s->arm_entry, 32, 32, value);
        break;
    case RELEASE_CONTROL:
        s->control = value;
        if (s->control & RELEASE_CONTROL_GO) {
            vc4_arm_release_cpu(s);
        }
        break;
    case RELEASE_STATUS:
        /* Write-one-to-clear is useful for later multi-release tests. */
        s->status &= ~value;
        break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "vc4-arm-release-smoke: bad write offset 0x%"
                      HWADDR_PRIx " value 0x%" PRIx64 "\n",
                      offset, value);
        break;
    }
}

static const MemoryRegionOps vc4_arm_release_ops = {
    .read = vc4_arm_release_read,
    .write = vc4_arm_release_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static CPUState *vc4_arm_release_new_vpu(void)
{
    /*
     * The secondary frontend keeps the native VPU model name when possible.
     * The additional candidates make this fixture tolerant of the temporary
     * names used by earlier versions of the development branch.
     */
    static const char * const candidates[] = {
        "vpu-vc4-cpu",
        "vpu-vc4-secondary-cpu",
        "vc4-vpu-secondary-cpu",
        "vc4-secondary-vpu-cpu",
    };
    ObjectClass *oc;
    Object *obj;
    size_t i;

    for (i = 0; i < ARRAY_SIZE(candidates); i++) {
        oc = object_class_by_name(candidates[i]);
        if (oc && object_class_dynamic_cast(oc, TYPE_CPU) &&
            !object_class_is_abstract(oc)) {
            obj = object_new(candidates[i]);
            return CPU(obj);
        }
    }

    error_report("vc4-arm-release-smoke: no linked VC4 VPU CPU type found");
    error_report("the AArch64 executable must include the secondary frontend");
    exit(EXIT_FAILURE);
}

static void vc4_arm_release_init(MachineState *machine)
{
    VC4ArmReleaseMachineState *s = VC4_ARM_RELEASE_MACHINE(machine);
    MemoryRegion *sysmem = get_system_memory();
    Object *arm_obj;
    CPUClass *vcc;
    ssize_t image_size;

    memory_region_add_subregion(sysmem, 0, machine->ram);

    memory_region_init_io(&s->release_mr, OBJECT(machine),
                          &vc4_arm_release_ops, s,
                          "vc4-arm-release", VC4_ARM_RELEASE_SIZE);
    memory_region_add_subregion(sysmem, VC4_ARM_RELEASE_BASE,
                                &s->release_mr);

    s->vc4_cpu = vc4_arm_release_new_vpu();
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

    vcc = CPU_GET_CLASS(s->vc4_cpu);
    g_assert(vcc->set_pc);
    vcc->set_pc(s->vc4_cpu, 0);

    if (!machine->kernel_filename) {
        error_report("vc4-arm-release-smoke requires -kernel IMAGE");
        exit(EXIT_FAILURE);
    }

    image_size = load_image_targphys(machine->kernel_filename, 0,
                                     machine->ram_size, NULL);
    if (image_size < 0) {
        error_report("could not load heterogeneous smoke image '%s'",
                     machine->kernel_filename);
        exit(EXIT_FAILURE);
    }
}

static void vc4_arm_release_machine_class_init(ObjectClass *oc,
                                                const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "VC4 firmware releases a held Cortex-A53 (TCG regression)";
    mc->init = vc4_arm_release_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a53");
    mc->default_ram_size = 16 * MiB;
    mc->default_ram_id = "vc4-arm-release-smoke.ram";
    mc->min_cpus = 2;
    mc->max_cpus = 2;
    mc->default_cpus = 2;
}

static const TypeInfo vc4_arm_release_machine_type = {
    .name = TYPE_VC4_ARM_RELEASE_MACHINE,
    .parent = TYPE_MACHINE,
    .interfaces = aarch64_machine_interfaces,
    .instance_size = sizeof(VC4ArmReleaseMachineState),
    .class_init = vc4_arm_release_machine_class_init,
};

static void vc4_arm_release_machine_register_types(void)
{
    type_register_static(&vc4_arm_release_machine_type);
}

type_init(vc4_arm_release_machine_register_types)
