/*
 * AArch64 + VideoCore IV heterogeneous TCG smoke machine
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/units.h"
#include "hw/core/boards.h"
#include "hw/arm/machines-qom.h"
#include "hw/core/loader.h"
#include "accel/tcg/cpu-ops.h"
#include "system/memory.h"
#include "target/arm/cpu.h"
#define VC4_SECONDARY_FRONTEND 1
#include "target/vc4/cpu-qom.h"
#undef VC4_SECONDARY_FRONTEND

#define TYPE_VC4_HETERO_MACHINE MACHINE_TYPE_NAME("vc4-hetero-smoke")
#define VC4_HETERO_ARM_PAYLOAD UINT64_C(0x00040000)
#define VC4_HETERO_STATUS_BASE UINT64_C(0x10000000)
#define VC4_HETERO_STATUS_SIZE 0x1000
OBJECT_DECLARE_SIMPLE_TYPE(VC4HeteroMachineState, VC4_HETERO_MACHINE)

struct VC4HeteroMachineState {
    MachineState parent_obj;

    MemoryRegion status_mr;
    CPUState *arm_cpu;
    CPUState *vc4_cpu;
};


static uint64_t vc4_hetero_status_read(void *opaque, hwaddr offset,
                                       unsigned size)
{
    VC4HeteroMachineState *s = opaque;
    uint32_t status = 0;

    (void)size;
    if (offset != 0) {
        return 0;
    }
    if (s->arm_cpu && s->arm_cpu->halted) {
        status |= 1u << 0;
    }
    if (s->vc4_cpu && s->vc4_cpu->halted) {
        status |= 1u << 1;
    }
    return status;
}

static const MemoryRegionOps vc4_hetero_status_ops = {
    .read = vc4_hetero_status_read,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
    .impl = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void vc4_hetero_set_pc(CPUState *cs, vaddr pc)
{
    CPUClass *cc = CPU_GET_CLASS(cs);

    g_assert(cc->set_pc);
    cc->set_pc(cs, pc);
}

static void vc4_hetero_init(MachineState *machine)
{
    /*
     * 0x14010000 is AArch64 "b 0x40000".  Its low halfword is 0x0000,
     * the development VC4 HALT encoding, so both frontends translate the same
     * physical PC and source bytes but must produce different TBs.
     */
    static const uint8_t polyglot[] = {
        0x00, 0x00, 0x01, 0x14,
    };
    static const uint8_t arm_payload[] = {
        0x00, 0x00, 0x82, 0xd2, /* movz x0, #0x1000 */
        0xc1, 0x1b, 0x98, 0x52, /* movz w1, #0xc0de */
        0x21, 0x42, 0xa9, 0x72, /* movk w1, #0x4a11, lsl #16 */
        0x01, 0x00, 0x00, 0xb9, /* str w1, [x0] */
        0x00, 0x00, 0x00, 0x14, /* b . */
    };
    VC4HeteroMachineState *s = VC4_HETERO_MACHINE(machine);
    MemoryRegion *sysmem = get_system_memory();
    Object *arm_obj;
    Object *vc4_obj;
    CPUState *arm;
    CPUState *vc4;

    memory_region_add_subregion(sysmem, 0, machine->ram);
    rom_add_blob_fixed("vc4-hetero-polyglot", polyglot,
                       sizeof(polyglot), 0);
    rom_add_blob_fixed("vc4-hetero-arm-payload", arm_payload,
                       sizeof(arm_payload), VC4_HETERO_ARM_PAYLOAD);

    arm_obj = object_new(machine->cpu_type);
    vc4_obj = object_new(TYPE_VC4_VPU_CPU);
    arm = CPU(arm_obj);
    vc4 = CPU(vc4_obj);
    s->arm_cpu = arm;
    s->vc4_cpu = vc4;

    tcg_exec_initialize_frontend(CPU_GET_CLASS(arm)->tcg_ops);
    tcg_exec_initialize_frontend(CPU_GET_CLASS(vc4)->tcg_ops);

    if (!qdev_realize(DEVICE(arm), NULL, &error_fatal)) {
        g_assert_not_reached();
    }
    if (!qdev_realize(DEVICE(vc4), NULL, &error_fatal)) {
        g_assert_not_reached();
    }

    memory_region_init_io(&s->status_mr, OBJECT(machine),
                          &vc4_hetero_status_ops, s,
                          "vc4-hetero-status", VC4_HETERO_STATUS_SIZE);
    memory_region_add_subregion(sysmem, VC4_HETERO_STATUS_BASE,
                                &s->status_mr);

    vc4_hetero_set_pc(arm, 0);
    vc4_hetero_set_pc(vc4, 0);
}

static void vc4_hetero_machine_class_init(ObjectClass *oc, const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "AArch64 + VideoCore IV heterogeneous TCG smoke machine";
    mc->init = vc4_hetero_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a53");
    mc->default_ram_size = 16 * MiB;
    mc->default_ram_id = "vc4-hetero.ram";
    mc->min_cpus = 2;
    mc->max_cpus = 2;
    mc->default_cpus = 2;
}

static const TypeInfo vc4_hetero_machine_type = {
    .name = TYPE_VC4_HETERO_MACHINE,
    .parent = TYPE_MACHINE,
    .interfaces = aarch64_machine_interfaces,
    .instance_size = sizeof(VC4HeteroMachineState),
    .class_init = vc4_hetero_machine_class_init,
};

static void vc4_hetero_machine_register_types(void)
{
    type_register_static(&vc4_hetero_machine_type);
}

type_init(vc4_hetero_machine_register_types)
