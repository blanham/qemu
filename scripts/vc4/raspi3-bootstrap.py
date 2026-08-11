#!/usr/bin/env python3
"""Materialize a VC4-first Raspberry Pi 3 bring-up machine.

The resulting machine intentionally has no ARM CPUs yet.  It maps the
BCM2835/6/7 peripheral block and the VideoCore GPU bus so VPU firmware can be
executed up to the point where it would release the Cortex-A53 cluster.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(relpath: str, old: str, new: str) -> None:
    path = ROOT / relpath
    text = path.read_text()
    if new in text:
        return
    if old not in text:
        raise RuntimeError(f"anchor not found in {relpath}: {old!r}")
    path.write_text(text.replace(old, new, 1))


# The platform constants are used by target-neutral peripheral devices.  Split
# them from the ARM machine/boot declarations before compiling those devices in
# qemu-system-vc4.
arm_header = ROOT / "include/hw/arm/raspi_platform.h"
text = arm_header.read_text()
constants_at = text.index("#define MSYNC_OFFSET")
last_endif = text.rfind("#endif")
constants = text[constants_at:last_endif].rstrip()

common_header = ROOT / "include/hw/raspi/raspi_platform.h"
common_header.parent.mkdir(parents=True, exist_ok=True)
common_header.write_text(
    "/*\n"
    " * Target-neutral BCM283x / Raspberry Pi platform definitions\n"
    " *\n"
    " * SPDX-License-Identifier: GPL-2.0-or-later\n"
    " */\n\n"
    "#ifndef HW_RASPI_RASPI_PLATFORM_H\n"
    "#define HW_RASPI_RASPI_PLATFORM_H\n\n"
    + constants
    + "\n\n#endif /* HW_RASPI_RASPI_PLATFORM_H */\n"
)

arm_header.write_text(
    text[:constants_at]
    + '#include "hw/raspi/raspi_platform.h"\n\n'
    + "#endif /* HW_ARM_RASPI_PLATFORM_H */\n"
)

replace_once(
    "hw/arm/bcm2835_peripherals.c",
    '#include "hw/arm/raspi_platform.h"',
    '#include "hw/raspi/raspi_platform.h"',
)
replace_once(
    "hw/misc/bcm2835_property.c",
    '#include "hw/arm/raspi_platform.h"',
    '#include "hw/raspi/raspi_platform.h"',
)

# RASPI's peripheral selection is useful to both the ARM board and the VC4
# bring-up board.  The ARM board sources remain in the ARM architecture source
# set, so widening this dependency does not pull ARM CPU code into vc4-softmmu.
replace_once(
    "hw/arm/Kconfig",
    "config RASPI\n    bool\n    default y\n    depends on TCG && ARM",
    "config RASPI\n    bool\n    default y\n    depends on TCG && (ARM || VC4)",
)

replace_once(
    "hw/vc4/Kconfig",
    "config VC4_VPU\n    bool\n    default y\n    depends on VC4\n",
    "config VC4_VPU\n"
    "    bool\n"
    "    default y\n"
    "    depends on VC4\n\n"
    "config VC4_RASPI3\n"
    "    bool\n"
    "    default y\n"
    "    depends on TCG && VC4\n"
    "    select RASPI\n",
)

replace_once(
    "hw/vc4/meson.build",
    "vc4_ss.add(when: 'CONFIG_VC4_VPU', if_true: files('vc4_vpu.c'))\n",
    "vc4_ss.add(when: 'CONFIG_VC4_VPU', if_true: files('vc4_vpu.c'))\n"
    "vc4_ss.add(when: 'CONFIG_VC4_RASPI3', if_true: files(\n"
    "  'raspi3_vpu.c',\n"
    "  '../arm/bcm2835_peripherals.c',\n"
    "))\n",
)

machine = ROOT / "hw/vc4/raspi3_vpu.c"
machine.write_text(r'''/*
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
#include "system/memory.h"
#include "system/qtest.h"
#include "target/vc4/cpu.h"

#define TYPE_VC4_RASPI3_MACHINE MACHINE_TYPE_NAME("raspi3b-vc4")
OBJECT_DECLARE_SIMPLE_TYPE(VC4Raspi3MachineState, VC4_RASPI3_MACHINE)

#define RASPI3_BOARD_REVISION 0x00a02082u
#define RASPI3_DEFAULT_VCRAM  (64 * MiB)

struct VC4Raspi3MachineState {
    MachineState parent_obj;

    BCM2835PeripheralState peripherals;
};

static void vc4_raspi3_machine_initfn(Object *obj)
{
    VC4Raspi3MachineState *s = VC4_RASPI3_MACHINE(obj);

    object_initialize_child(obj, "peripherals", &s->peripherals,
                            TYPE_BCM2835_PERIPHERALS);
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

# Leave only the actual implementation in the successful commit.
for relpath in (
    "scripts/vc4/raspi3-bootstrap.py",
    ".github/workflows/vc4-raspi3-bootstrap.yml",
):
    path = ROOT / relpath
    if path.exists():
        path.unlink()
