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
            sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->vpu_intc[i]), 0), 1);
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
