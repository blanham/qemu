#!/usr/bin/env python3
"""Add WD40's explicit AMD 1 TiB HyperTransport-hole machine control.

The transformation is marker-based and idempotent so it can be rerun after
routine upstream rebases. The default ``auto`` value preserves each versioned
PC machine type's existing compatibility behavior; explicit ``on`` and ``off``
values override that policy without changing non-AMD virtual CPUs.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    count = text.count(old)
    if count == 1:
        store(file_path, text.replace(old, new, 1))
        return
    if count == 0 and new in text:
        return
    raise RuntimeError(f"{path}: expected one replacement site, found {count}")


def create_extensible(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")
        if existing == content or existing.startswith(content + "\n"):
            return
        raise RuntimeError(
            f"{path}: existing file is not the WD40 base or an append-only "
            "extension"
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def main() -> None:
    replace_once(
        "include/hw/i386/pc.h",
        """    uint64_t max_ram_below_4g;
    OnOffAuto vmport;
    SmbiosEntryPointType smbios_entry_point_type;""",
        """    uint64_t max_ram_below_4g;
    OnOffAuto vmport;
    OnOffAuto amd_1tb_hole;
    SmbiosEntryPointType smbios_entry_point_type;""",
    )
    replace_once(
        "include/hw/i386/pc.h",
        '''#define PC_MACHINE_VMPORT           "vmport"
#define PC_MACHINE_SMBUS            "smbus"''',
        '''#define PC_MACHINE_VMPORT           "vmport"
#define PC_MACHINE_AMD_1TB_HOLE     "amd-1tb-hole"
#define PC_MACHINE_SMBUS            "smbus"''',
    )

    replace_once(
        "hw/i386/pc.c",
        """#define AMD_HT_SIZE          (AMD_ABOVE_1TB_START - AMD_HT_START)

void pc_memory_init(PCMachineState *pcms,""",
        """#define AMD_HT_SIZE          (AMD_ABOVE_1TB_START - AMD_HT_START)

static bool pc_machine_amd_1tb_hole_enabled(PCMachineState *pcms)
{
    PCMachineClass *pcmc = PC_MACHINE_GET_CLASS(pcms);

    if (pcms->amd_1tb_hole == ON_OFF_AUTO_AUTO) {
        return pcmc->enforce_amd_1tb_hole;
    }
    return pcms->amd_1tb_hole == ON_OFF_AUTO_ON;
}

void pc_memory_init(PCMachineState *pcms,""",
    )
    replace_once(
        "hw/i386/pc.c",
        """    /*
     * The HyperTransport range close to the 1T boundary is unique to AMD
     * hosts with IOMMUs enabled. Restrict the ram-above-4g relocation
     * to above 1T to AMD vCPUs only. @enforce_amd_1tb_hole is only false in
     * older machine types (<= 7.0) for compatibility purposes.
     */
    if (IS_AMD_CPU(&cpu->env) && pcmc->enforce_amd_1tb_hole) {""",
        """    /*
     * The HyperTransport range close to the 1 TiB boundary is unique to AMD
     * systems with IOMMUs. Keep the relocation AMD-vCPU-specific. In auto
     * mode, preserve the selected versioned machine type's compatibility
     * policy; explicit on/off values override that policy.
     */
    if (IS_AMD_CPU(&cpu->env) &&
        pc_machine_amd_1tb_hole_enabled(pcms)) {""",
    )
    replace_once(
        "hw/i386/pc.c",
        """static void pc_machine_set_vmport(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    PCMachineState *pcms = PC_MACHINE(obj);

    visit_type_OnOffAuto(v, name, &pcms->vmport, errp);
}

static bool pc_machine_get_fd_bootchk(Object *obj, Error **errp)""",
        """static void pc_machine_set_vmport(Object *obj, Visitor *v, const char *name,
                                  void *opaque, Error **errp)
{
    PCMachineState *pcms = PC_MACHINE(obj);

    visit_type_OnOffAuto(v, name, &pcms->vmport, errp);
}

static void pc_machine_get_amd_1tb_hole(Object *obj, Visitor *v,
                                        const char *name, void *opaque,
                                        Error **errp)
{
    PCMachineState *pcms = PC_MACHINE(obj);
    OnOffAuto amd_1tb_hole = pcms->amd_1tb_hole;

    visit_type_OnOffAuto(v, name, &amd_1tb_hole, errp);
}

static void pc_machine_set_amd_1tb_hole(Object *obj, Visitor *v,
                                        const char *name, void *opaque,
                                        Error **errp)
{
    PCMachineState *pcms = PC_MACHINE(obj);

    visit_type_OnOffAuto(v, name, &pcms->amd_1tb_hole, errp);
}

static bool pc_machine_get_fd_bootchk(Object *obj, Error **errp)""",
    )
    replace_once(
        "hw/i386/pc.c",
        """#endif /* CONFIG_VMPORT */
    pcms->max_ram_below_4g = 0; /* use default */""",
        """#endif /* CONFIG_VMPORT */
    pcms->amd_1tb_hole = ON_OFF_AUTO_AUTO;
    pcms->max_ram_below_4g = 0; /* use default */""",
    )
    replace_once(
        "hw/i386/pc.c",
        """    object_class_property_set_description(oc, PC_MACHINE_VMPORT,
        "Enable vmport (pc & q35)");

    object_class_property_add_bool(oc, PC_MACHINE_SMBUS,""",
        """    object_class_property_set_description(oc, PC_MACHINE_VMPORT,
        "Enable vmport (pc & q35)");

    object_class_property_add(oc, PC_MACHINE_AMD_1TB_HOLE, "OnOffAuto",
        pc_machine_get_amd_1tb_hole, pc_machine_set_amd_1tb_hole,
        NULL, NULL);
    object_class_property_set_description(oc, PC_MACHINE_AMD_1TB_HOLE,
        "Control the AMD HyperTransport reservation near 1 TiB");

    object_class_property_add_bool(oc, PC_MACHINE_SMBUS,""",
    )

    replace_once(
        "docs/system/target-i386.rst",
        """   i386/amd-memory-encryption
   i386/tdx

OS requirements""",
        """   i386/amd-memory-encryption
   i386/tdx
   i386/wd40-qol

OS requirements""",
    )
    create_extensible(
        "docs/system/i386/wd40-qol.rst",
        """WD40 x86 quality-of-life controls
===================================

AMD 1 TiB HyperTransport reservation
------------------------------------

AMD systems with an IOMMU reserve guest physical addresses immediately below
1 TiB for HyperTransport and interrupt-controller functions. For AMD virtual
CPUs, QEMU normally moves RAM and the 64-bit PCI aperture above that range when
the configured address space would overlap it.

The WD40 PC and Q35 machines expose this policy as the ``amd-1tb-hole`` machine
property:

``auto``
  Preserve the selected versioned machine type's compatibility behavior. This
  is the default. Machine versions 7.1 and newer enable the reservation, while
  versions 7.0 and older retain their historical disabled behavior.

``on``
  Force the reservation and, when required, move RAM above 1 TiB. This is useful
  when an older versioned machine must model the modern safe layout.

``off``
  Suppress the reservation and relocation. This is useful for operating-system
  development and retro configurations that deliberately need the contiguous
  pre-1-TiB guest-physical layout.

The property does not change the layout of Intel virtual CPUs. Migration peers
must use the same explicit value whenever ``on`` or ``off`` is selected.

Examples::

  qemu-system-x86_64 -machine q35,amd-1tb-hole=off -cpu EPYC ...
  qemu-system-x86_64 -machine pc-q35-7.0,amd-1tb-hole=on -cpu EPYC ...
""",
    )


if __name__ == "__main__":
    main()
