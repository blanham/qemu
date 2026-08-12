/*
 * Heterogeneous Raspberry Pi 3B bring-up machine
 *
 * This machine joins QEMU's existing BCM2837 ARM-side model to the linked
 * VideoCore IV frontend.  The four Cortex-A53s begin powered off.  The VPU
 * executes from its real GPU-bus address space, drives PM_PROC at 0x7e100110,
 * and releases ARM CPU0 through the architectural ARM power-control path.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/error-report.h"
#include "qemu/units.h"
#include "hw/arm/bcm2836.h"
#include "hw/arm/machines-qom.h"
#include "hw/arm/vc4_raspi3_bootrom.h"
#include "hw/core/boards.h"
#include "hw/core/irq.h"
#include "hw/core/loader.h"
#include "hw/core/qdev-properties-system.h"
#include "hw/core/sysbus.h"
#include "hw/sd/sd.h"
#include "hw/vc4/bcm2835_vc4_intc.h"
#include "accel/tcg/cpu-ops.h"
#include "exec/cpu-common.h"
#include "system/block-backend.h"
#include "system/blockdev.h"
#include "system/memory.h"
#include "target/arm/arm-powerctl.h"
#include "target/arm/cpu.h"

#define TYPE_VC4_RASPI3_HETERO_MACHINE \
    MACHINE_TYPE_NAME("raspi3b-vc4-hetero")
OBJECT_DECLARE_SIMPLE_TYPE(VC4Raspi3HeteroMachineState,
                           VC4_RASPI3_HETERO_MACHINE)

#define RASPI3_BOARD_REVISION 0x00a02082u
#define RASPI3_DEFAULT_VCRAM (64 * MiB)
#define VC4_IC0_OFFSET 0x2000
#define VC4_IC1_OFFSET 0x2800

struct VC4Raspi3HeteroMachineState {
    MachineState parent_obj;

    BCM283XState soc;
    BCM2835VC4IntcState vpu_intc[2];
    MemoryRegion vpu_address_space;
    MemoryRegion vpu_boot_cache;
    CPUState *vpu_cpu;
    BlockBackend *sd_blk;
    qemu_irq arm_power_irq;

    bool arm_cpu0_released;
    uint32_t arm_release_count;
    hwaddr vpu_entry;
};

static CPUState *vc4_raspi3_new_vpu(void)
{
    static const char * const candidates[] = {
        "vpu-vc4-cpu",
        "vpu-vc4-secondary-cpu",
        "vc4-vpu-secondary-cpu",
        "vc4-secondary-vpu-cpu",
    };
    ObjectClass *oc;
    size_t i;

    for (i = 0; i < ARRAY_SIZE(candidates); i++) {
        oc = object_class_by_name(candidates[i]);
        if (oc && object_class_dynamic_cast(oc, TYPE_CPU) &&
            !object_class_is_abstract(oc)) {
            return CPU(object_new(candidates[i]));
        }
    }

    error_report("raspi3b-vc4-hetero: no linked VC4 VPU CPU type found");
    error_report("the AArch64 executable must include the secondary frontend");
    exit(EXIT_FAILURE);
}

static void vc4_raspi3_preinitialize_frontends(CPUState *vpu)
{
    ObjectClass *arm_oc;
    CPUClass *arm_cc;
    CPUClass *vpu_cc = CPU_GET_CLASS(vpu);

    arm_oc = object_class_by_name(ARM_CPU_TYPE_NAME("cortex-a53"));
    if (!arm_oc || !object_class_dynamic_cast(arm_oc, TYPE_CPU)) {
        error_report("raspi3b-vc4-hetero: Cortex-A53 CPU class is unavailable");
        exit(EXIT_FAILURE);
    }
    arm_cc = CPU_CLASS(arm_oc);

    /*
     * The first realized CPU clones the initial TCG context.  Populate both
     * guest frontend global tables before the BCM2837 realizes its first A53.
     */
    tcg_exec_initialize_frontend(vpu_cc->tcg_ops);
    tcg_exec_initialize_frontend(arm_cc->tcg_ops);
}

static void vc4_raspi3_arm_power_on(void *opaque, int n, int level)
{
    VC4Raspi3HeteroMachineState *s = opaque;
    BCM283XBaseState *soc = &s->soc.parent_obj;
    ARMCPU *arm_cpu = &soc->cpu[0].core;
    uint64_t cpuid;
    int ret;

    if (!level || s->arm_cpu0_released) {
        return;
    }

    cpuid = arm_cpu_mp_affinity(arm_cpu);
    ret = arm_set_cpu_on(cpuid, 0, 0, 3, true);
    if (ret != QEMU_ARM_POWERCTL_RET_SUCCESS) {
        error_report("raspi3b-vc4-hetero: ARM CPU0 release failed: %d", ret);
        return;
    }

    s->arm_cpu0_released = true;
    s->arm_release_count++;
}

static BlockBackend *vc4_raspi3_create_sdcard(BCM283XBaseState *soc)
{
    DriveInfo *di = drive_get(IF_SD, 0, 0);
    BlockBackend *blk = di ? blk_by_legacy_dinfo(di) : NULL;
    BusState *bus = qdev_get_child_bus(DEVICE(soc), "sd-bus");
    DeviceState *carddev;

    if (!bus) {
        error_report("raspi3b-vc4-hetero: no SD bus found in BCM2837");
        exit(EXIT_FAILURE);
    }

    carddev = qdev_new(TYPE_SD_CARD);
    qdev_prop_set_drive_err(carddev, "drive", blk, &error_fatal);
    qdev_realize_and_unref(carddev, bus, &error_fatal);
    return blk;
}

static void vc4_raspi3_machine_initfn(Object *obj)
{
    VC4Raspi3HeteroMachineState *s =
        VC4_RASPI3_HETERO_MACHINE(obj);

    object_initialize_child(obj, "soc", &s->soc, TYPE_BCM2837);
    object_initialize_child(obj, "vpu-intc0", &s->vpu_intc[0],
                            TYPE_BCM2835_VC4_INTC);
    object_initialize_child(obj, "vpu-intc1", &s->vpu_intc[1],
                            TYPE_BCM2835_VC4_INTC);
}

static void vc4_raspi3_hetero_init(MachineState *machine)
{
    VC4Raspi3HeteroMachineState *s =
        VC4_RASPI3_HETERO_MACHINE(machine);
    BCM283XBaseState *soc = &s->soc.parent_obj;
    BCM2835PeripheralState *peripherals = &s->soc.peripherals;
    BCMSocPeripheralBaseState *ps =
        BCM_SOC_PERIPHERALS_BASE(peripherals);
    MemoryRegion *sysmem = get_system_memory();
    CPUClass *vpu_cc;
    const char *image = machine->kernel_filename;
    uint64_t vcram_size = RASPI3_DEFAULT_VCRAM;
    ssize_t image_size;
    unsigned i;

    if (machine->ram_size != 1 * GiB) {
        error_report("raspi3b-vc4-hetero requires exactly 1 GiB of RAM");
        exit(EXIT_FAILURE);
    }

    /*
     * Construct the VPU object first so both TCG frontends can be initialized
     * before the SoC realizes any of its four Cortex-A53s.
     */
    s->vpu_cpu = vc4_raspi3_new_vpu();
    vc4_raspi3_preinitialize_frontends(s->vpu_cpu);

    memory_region_add_subregion_overlap(sysmem, 0, machine->ram, 0);

    object_property_add_const_link(OBJECT(soc), "ram", OBJECT(machine->ram));
    object_property_set_int(OBJECT(soc), "board-rev",
                            RASPI3_BOARD_REVISION, &error_abort);
    object_property_set_str(OBJECT(soc), "command-line",
                            machine->kernel_cmdline, &error_abort);
    object_property_set_int(OBJECT(soc), "vcram-size",
                            vcram_size, &error_abort);
    object_property_set_int(OBJECT(soc), "vcram-base",
                            machine->ram_size - vcram_size, &error_abort);

    /* All four A53s remain in PSCI_OFF until firmware releases CPU0. */
    object_property_set_int(OBJECT(soc), "enabled-cpus", 0, &error_abort);
    qdev_realize(DEVICE(soc), NULL, &error_fatal);

    s->sd_blk = vc4_raspi3_create_sdcard(soc);

    for (i = 0; i < ARRAY_SIZE(s->vpu_intc); i++) {
        hwaddr offset = i ? VC4_IC1_OFFSET : VC4_IC0_OFFSET;

        if (!sysbus_realize(SYS_BUS_DEVICE(&s->vpu_intc[i]),
                            &error_fatal)) {
            g_assert_not_reached();
        }
        memory_region_add_subregion_overlap(
            &ps->peri_mr, offset,
            sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->vpu_intc[i]), 0), 1);
    }

    /*
     * Mirror the existing raw GPU interrupt fabric into the VPU's interrupt
     * controller without disturbing the ARM interrupt controller.
     */
    for (i = 0; i < BCM2835_VC4_INTC_NUM_IRQS; i++) {
        qdev_connect_gpio_out_named(
            DEVICE(&ps->ic), BCM2835_IC_GPU_IRQ_OUT, i,
            qdev_get_gpio_in_named(DEVICE(&s->vpu_intc[0]),
                                   BCM2835_VC4_INTC_GPU_IRQ, i));
    }

    /*
     * The VPU sees the 4 GiB GPU bus, but its first-stage ROM loads
     * bootcode.bin into a local 128 KiB L2-backed area at VPU address zero.
     * Overlaying a private RAM region preserves that separation: ARM address
     * zero remains ordinary SDRAM while VPU address zero contains bootcode.
     */
    memory_region_init(&s->vpu_address_space, OBJECT(s->vpu_cpu),
                       "vc4-vpu-address-space", UINT64_C(1) << 32);
    memory_region_add_subregion(&s->vpu_address_space, 0, &ps->gpu_bus_mr);
    memory_region_init_ram(&s->vpu_boot_cache, OBJECT(s->vpu_cpu),
                           "vc4-vpu-boot-cache",
                           VC4_RASPI3_BOOT_CACHE_SIZE, &error_fatal);
    memory_region_add_subregion_overlap(&s->vpu_address_space, 0,
                                        &s->vpu_boot_cache, 1);

    cpu_address_space_init(s->vpu_cpu, 0, "vc4-vpu",
                           &s->vpu_address_space);
    s->vpu_cpu->start_powered_off = false;
    if (!qdev_realize(DEVICE(s->vpu_cpu), NULL, &error_fatal)) {
        g_assert_not_reached();
    }
    s->arm_power_irq = qemu_allocate_irq(vc4_raspi3_arm_power_on, s, 0);
    qdev_connect_gpio_out_named(
        DEVICE(&ps->powermgt), BCM2835_POWERMGT_ARM_POWER_ON, 0,
        s->arm_power_irq);

    if (!image) {
        image = machine->firmware;
    }

    if (image) {
        s->vpu_entry = machine->ram_size - vcram_size;
        image_size = load_image_targphys_as(
            image, s->vpu_entry, vcram_size, s->vpu_cpu->as, NULL);
        if (image_size < 0) {
            error_report("could not load VideoCore IV firmware '%s'", image);
            exit(EXIT_FAILURE);
        }
    } else {
        VC4Raspi3BootInfo boot_info;
        uint8_t *boot_cache =
            memory_region_get_ram_ptr(&s->vpu_boot_cache);

        if (!vc4_raspi3_bootrom_load(s->sd_blk, boot_cache,
                                     VC4_RASPI3_BOOT_CACHE_SIZE,
                                     &boot_info, &error_fatal)) {
            g_assert_not_reached();
        }
        s->vpu_entry = 0;
        info_report("raspi3b-vc4-hetero: loaded bootcode.bin (%u bytes) "
                    "from FAT%u boot partition at LBA %" PRIu64,
                    boot_info.file_size, boot_info.fat32 ? 32 : 16,
                    boot_info.partition_lba);
    }

    vpu_cc = CPU_GET_CLASS(s->vpu_cpu);
    g_assert(vpu_cc->set_pc);
    vpu_cc->set_pc(s->vpu_cpu, s->vpu_entry);
}

static void vc4_raspi3_hetero_machine_class_init(ObjectClass *oc,
                                                 const void *data)
{
    MachineClass *mc = MACHINE_CLASS(oc);

    mc->desc = "Raspberry Pi 3B with VC4-first heterogeneous boot";
    mc->init = vc4_raspi3_hetero_init;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a53");
    mc->default_ram_size = 1 * GiB;
    mc->default_ram_id = "raspi3b-vc4-hetero.ram";
    mc->default_cpus = 5;
    mc->min_cpus = 5;
    mc->max_cpus = 5;
    mc->block_default_type = IF_SD;
    mc->auto_create_sdcard = true;
    mc->no_parallel = 1;
    mc->no_floppy = 1;
    mc->no_cdrom = 1;
}

static const TypeInfo vc4_raspi3_hetero_machine_type = {
    .name = TYPE_VC4_RASPI3_HETERO_MACHINE,
    .parent = TYPE_MACHINE,
    .interfaces = aarch64_machine_interfaces,
    .instance_size = sizeof(VC4Raspi3HeteroMachineState),
    .instance_init = vc4_raspi3_machine_initfn,
    .class_init = vc4_raspi3_hetero_machine_class_init,
};

static void vc4_raspi3_hetero_machine_register_types(void)
{
    type_register_static(&vc4_raspi3_hetero_machine_type);
}

type_init(vc4_raspi3_hetero_machine_register_types)
