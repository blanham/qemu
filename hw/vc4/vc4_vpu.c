/*
 * Minimal VideoCore IV VPU development machine
 *
 * This is intentionally a CPU bring-up machine.  It supplies flat RAM and
 * raw firmware loading so the VPU frontend can be tested before the BCM283x
 * boot complex is restructured around the real VideoCore-first boot flow.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/error-report.h"
#include "qemu/units.h"
#include "qapi/error.h"
#include "hw/core/boards.h"
#include "hw/core/loader.h"
#include "system/memory.h"
#include "system/qtest.h"
#include "target/vc4/cpu.h"

static void vc4_vpu_init(MachineState *machine)
{
    MemoryRegion *sysmem = get_system_memory();
    VC4CPU *cpu;
    const char *image = machine->kernel_filename;
    ssize_t image_size;

    memory_region_add_subregion(sysmem, 0, machine->ram);

    cpu = VC4_CPU(cpu_create(machine->cpu_type));
    if (!cpu) {
        error_report("Unable to create VideoCore IV VPU CPU");
        exit(EXIT_FAILURE);
    }

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

static void vc4_vpu_machine_init(MachineClass *mc)
{
    mc->desc = "VideoCore IV VPU CPU development machine";
    mc->init = vc4_vpu_init;
    mc->default_cpu_type = TYPE_VC4_VPU_CPU;
    mc->default_ram_size = 128 * MiB;
    mc->default_ram_id = "vc4-vpu.ram";
    mc->min_cpus = 1;
    mc->max_cpus = 1;
    mc->default_cpus = 1;
}

DEFINE_MACHINE("vc4-vpu", vc4_vpu_machine_init)
