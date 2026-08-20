#!/usr/bin/env python3
"""Add a direct ARM boot control path to raspi3b-vc4-hetero.

The stock path remains VC4-first.  Direct mode is an explicit bring-up control:
ARM Linux is loaded through QEMU's normal ARM boot machinery while the linked
VPU remains realized but powered off.  This isolates ARM, mailbox, property,
and framebuffer behavior from the still-unresolved stock-firmware handoff.
"""

from __future__ import annotations

from pathlib import Path


MACHINE = Path("hw/arm/vc4_raspi3_hetero.c")
PROBE = Path("scripts/vc4/raspi3-linux-probe.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def update_machine() -> None:
    text = MACHINE.read_text(encoding="utf-8")

    text = replace_once(
        text,
        '#include "hw/arm/bcm2836.h"\n',
        '#include "hw/arm/bcm2836.h"\n#include "hw/arm/boot.h"\n',
        "ARM boot include",
    )
    text = replace_once(
        text,
        '#define VC4_IC1_OFFSET 0x2800\n',
        '#define VC4_IC1_OFFSET 0x2800\n'
        '#define VC4_SMPBOOT_ADDR 0x300\n'
        '#define VC4_SPINTABLE_ADDR 0xd8\n',
        "direct-boot constants",
    )
    text = replace_once(
        text,
        '    qemu_irq arm_power_irq;\n\n'
        '    bool arm_cpu0_released;\n',
        '    qemu_irq arm_power_irq;\n'
        '    struct arm_boot_info binfo;\n\n'
        '    bool direct_arm_kernel;\n'
        '    bool arm_cpu0_released;\n',
        "machine direct-boot state",
    )

    helper_anchor = 'static void vc4_raspi3_machine_initfn(Object *obj)\n'
    helper_code = r'''static void vc4_raspi3_write_smpboot64(
    ARMCPU *cpu, const struct arm_boot_info *info)
{
    AddressSpace *as = arm_boot_address_space(cpu, info);
    static const ARMInsnFixup smpboot[] = {
        { 0xd2801b05 }, /* mov x5, #0xd8 */
        { 0xd53800a6 }, /* mrs x6, mpidr_el1 */
        { 0x924004c6 }, /* and x6, x6, #3 */
        { 0xd503205f }, /* spin: wfe */
        { 0xf86678a4 }, /* ldr x4, [x5, x6, lsl #3] */
        { 0xb4ffffc4 }, /* cbz x4, spin */
        { 0xd2800000 }, /* mov x0, #0 */
        { 0xd2800001 }, /* mov x1, #0 */
        { 0xd2800002 }, /* mov x2, #0 */
        { 0xd2800003 }, /* mov x3, #0 */
        { 0xd61f0080 }, /* br x4 */
        { 0, FIXUP_TERMINATOR },
    };
    static const uint32_t fixupcontext[FIXUP_MAX] = { 0 };
    static const uint64_t spintables[BCM283X_NCPUS] = { 0 };

    arm_write_bootloader("vc4_raspi3_smpboot", as,
                         info->smp_loader_start,
                         smpboot, fixupcontext);
    rom_add_blob_fixed_as("vc4_raspi3_spintables",
                          spintables, sizeof(spintables),
                          VC4_SPINTABLE_ADDR, as);
}

static void vc4_raspi3_reset_secondary(
    ARMCPU *cpu, const struct arm_boot_info *info)
{
    cpu_set_pc(CPU(cpu), info->smp_loader_start);
}

static bool vc4_raspi3_get_direct_arm_kernel(Object *obj, Error **errp)
{
    return VC4_RASPI3_HETERO_MACHINE(obj)->direct_arm_kernel;
}

static void vc4_raspi3_set_direct_arm_kernel(Object *obj, bool value,
                                              Error **errp)
{
    VC4_RASPI3_HETERO_MACHINE(obj)->direct_arm_kernel = value;
}

'''
    text = replace_once(
        text, helper_anchor, helper_code + helper_anchor,
        "direct-boot helpers",
    )

    text = replace_once(
        text,
        '    /* All four A53s remain in PSCI_OFF until firmware releases CPU0. */\n'
        '    object_property_set_int(OBJECT(soc), "enabled-cpus", 0, &error_abort);\n',
        '    /* Stock firmware owns ARM release; direct control starts all A53s. */\n'
        '    object_property_set_int(OBJECT(soc), "enabled-cpus",\n'
        '                            s->direct_arm_kernel ? BCM283X_NCPUS : 0,\n'
        '                            &error_abort);\n',
        "enabled ARM CPU count",
    )

    direct_setup_anchor = r'''    /*
     * The VPU sees the 4 GiB GPU bus, but its first-stage ROM loads
'''
    direct_setup = r'''    if (s->direct_arm_kernel) {
        s->binfo.board_id = MACH_TYPE_BCM2708;
        s->binfo.ram_size = machine->ram_size - vcram_size;
        s->binfo.smp_loader_start = VC4_SMPBOOT_ADDR;
        s->binfo.write_secondary_boot = vc4_raspi3_write_smpboot64;
        s->binfo.secondary_cpu_reset_hook = vc4_raspi3_reset_secondary;

        /*
         * The VPU is deliberately not realized yet: arm_load_kernel()
         * historically iterates the global CPU list as ARM CPUs.  Keeping
         * the powered-off VPU out of that list preserves the generic ARM
         * loader contract without weakening its type assumptions globally.
         */
        arm_load_kernel(&soc->cpu[0].core, machine, &s->binfo);
        s->arm_cpu0_released = true;
    }

'''
    text = replace_once(
        text, direct_setup_anchor, direct_setup + direct_setup_anchor,
        "direct ARM loader setup",
    )
    text = replace_once(
        text,
        '    s->vpu_cpu->start_powered_off = false;\n',
        '    s->vpu_cpu->start_powered_off = s->direct_arm_kernel;\n',
        "direct VPU power state",
    )
    text = replace_once(
        text,
        '    qdev_connect_gpio_out_named(\n'
        '        DEVICE(&ps->powermgt), BCM2835_POWERMGT_ARM_POWER_ON, 0,\n'
        '        s->arm_power_irq);\n\n'
        '    if (!image) {\n',
        '    qdev_connect_gpio_out_named(\n'
        '        DEVICE(&ps->powermgt), BCM2835_POWERMGT_ARM_POWER_ON, 0,\n'
        '        s->arm_power_irq);\n\n'
        '    if (s->direct_arm_kernel) {\n'
        '        return;\n'
        '    }\n\n'
        '    if (!image) {\n',
        "skip VC4 firmware in direct mode",
    )
    text = replace_once(
        text,
        '    mc->desc = "Raspberry Pi 3B with VC4-first heterogeneous boot";\n',
        '    mc->desc = "Raspberry Pi 3B with VC4-first heterogeneous boot";\n'
        '    object_class_property_add_bool(\n'
        '        oc, "direct-arm-kernel",\n'
        '        vc4_raspi3_get_direct_arm_kernel,\n'
        '        vc4_raspi3_set_direct_arm_kernel);\n'
        '    object_class_property_set_description(\n'
        '        oc, "direct-arm-kernel",\n'
        '        "Load -kernel/-dtb/-initrd on ARM while the VPU is powered off");\n',
        "direct mode machine property",
    )

    required = (
        '"direct-arm-kernel"',
        'arm_load_kernel(&soc->cpu[0].core',
        'start_powered_off = s->direct_arm_kernel',
        'VC4_SMPBOOT_ADDR',
    )
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise RuntimeError(f"machine integration incomplete: {missing}")
    MACHINE.write_text(text, encoding="utf-8")


def update_probe() -> None:
    text = PROBE.read_text(encoding="utf-8")
    old = (
        '    if args.mode in {"bare-direct", "linux-direct"}:\n'
        '        command.extend(["-M", "raspi3b", "-m", "1G", "-smp", "4"])\n'
    )
    new = (
        '    if args.mode in {"bare-direct", "linux-direct"}:\n'
        '        command.extend([\n'
        '            "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",\n'
        '            "-m", "1G", "-smp", "5",\n'
        '        ])\n'
    )
    text = replace_once(text, old, new, "heterogeneous direct probe")
    if 'raspi3b-vc4-hetero,direct-arm-kernel=on' not in text:
        raise RuntimeError("probe did not select the heterogeneous machine")
    PROBE.write_text(text, encoding="utf-8")


def main() -> int:
    update_machine()
    update_probe()
    print("VC4 heterogeneous direct ARM boot integration materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
