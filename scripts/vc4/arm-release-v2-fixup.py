#!/usr/bin/env python3
"""Harden and correctly link the VC4-controlled ARM-release fixture."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    """Replace one generated fragment while remaining safe on reruns."""
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


# QEMU translation units must include qemu/osdep.h before target headers.
# The embedded VC4 wrappers intentionally include target/arm/cpu.h to expose
# the primary frontend's CPUArchState type, but doing so before osdep leaves
# fundamental C/QEMU types and QEMU_BUILD_BUG_ON undefined.
for name in ("cpu", "gdbstub", "op_helper", "translate"):
    wrapper = ROOT / f"target/arm/vc4-secondary/{name}.c"
    replace_once(
        wrapper,
        (
            "#define VC4_SECONDARY_FRONTEND 1\n"
            '#include "target/arm/cpu.h"\n'
        ),
        (
            '#include "qemu/osdep.h"\n'
            "#define VC4_SECONDARY_FRONTEND 1\n"
            '#include "target/arm/cpu.h"\n'
        ),
        "secondary frontend include prologue",
    )

# The secondary wrappers include primary-target headers and target-width TCG
# APIs, so they must not live in either ARM target-neutral source set.
# QEMU shares arm_common_system_ss and arm_system_ss between ARM32 and AArch64
# and poisons TARGET_AARCH64, TARGET_LONG_BITS, and target endianness macros
# while compiling them.  Keep the Kconfig gate, but move the wrappers into
# arm_ss, which is compiled once per concrete target executable.
replace_once(
    ROOT / "target/arm/meson.build",
    "arm_common_system_ss.add(when: 'CONFIG_VC4_HETERO_SMOKE', if_true: files(\n",
    "arm_ss.add(when: 'CONFIG_VC4_HETERO_SMOKE', if_true: files(\n",
    "secondary VC4 frontend Meson entry",
)

source = ROOT / "hw/arm/vc4_arm_release_smoke.c"
text = source.read_text(encoding="utf-8")
needle = '#include "qemu/error-report.h"\n'
addition = needle + '#include "qemu/log.h"\n'
if '#include "qemu/log.h"' not in text:
    if needle not in text:
        raise SystemExit("could not locate qemu/error-report.h include")
    source.write_text(text.replace(needle, addition, 1), encoding="utf-8")


# QEMU 11 filters the machine list through target-specific QOM interfaces.
# A class derived directly from TYPE_MACHINE is registered successfully but
# remains invisible to qemu-system-aarch64 unless it implements the AArch64
# machine interface.  Both generated regression machines are AArch64-only.
def expose_to_aarch64(path: str) -> None:
    machine = ROOT / path
    text = machine.read_text(encoding="utf-8")

    include = '#include "hw/arm/machines-qom.h"\n'
    if include not in text:
        marker = '#include "hw/core/boards.h"\n'
        if marker not in text:
            raise SystemExit(f"could not locate boards include in {machine}")
        text = text.replace(marker, marker + include, 1)

    interface = "    .interfaces = aarch64_machine_interfaces,\n"
    if interface not in text:
        marker = "    .parent = TYPE_MACHINE,\n"
        if marker not in text:
            raise SystemExit(f"could not locate machine TypeInfo in {machine}")
        text = text.replace(marker, marker + interface, 1)

    machine.write_text(text, encoding="utf-8")


for machine_path in (
    "hw/arm/vc4_hetero.c",
    "hw/arm/vc4_arm_release_smoke.c",
):
    expose_to_aarch64(machine_path)


# Turn the original same-PC polyglot into an observable execution-isolation
# regression.  At physical PC 0, the low halfword is still VC4 HALT while the
# complete AArch64 word branches to a payload at 0x40000.  The payload writes a
# marker and loops.  A tiny read-only MMIO window reports each CPU's halted bit.
# Together these observations catch either direction of a cross-frontend TB
# collision without relying on verbose execution logs.
def harden_collision_smoke() -> None:
    machine = ROOT / "hw/arm/vc4_hetero.c"
    text = machine.read_text(encoding="utf-8")

    old = (
        '#define TYPE_VC4_HETERO_MACHINE MACHINE_TYPE_NAME("vc4-hetero-smoke")\n'
        "OBJECT_DECLARE_SIMPLE_TYPE(VC4HeteroMachineState, VC4_HETERO_MACHINE)\n"
    )
    new = (
        '#define TYPE_VC4_HETERO_MACHINE MACHINE_TYPE_NAME("vc4-hetero-smoke")\n'
        "#define VC4_HETERO_ARM_PAYLOAD UINT64_C(0x00040000)\n"
        "#define VC4_HETERO_STATUS_BASE UINT64_C(0x10000000)\n"
        "#define VC4_HETERO_STATUS_SIZE 0x1000\n"
        "OBJECT_DECLARE_SIMPLE_TYPE(VC4HeteroMachineState, VC4_HETERO_MACHINE)\n"
    )
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("could not locate vc4-hetero constants")

    old = (
        "struct VC4HeteroMachineState {\n"
        "    MachineState parent_obj;\n"
        "};\n"
    )
    new = (
        "struct VC4HeteroMachineState {\n"
        "    MachineState parent_obj;\n"
        "\n"
        "    MemoryRegion status_mr;\n"
        "    CPUState *arm_cpu;\n"
        "    CPUState *vc4_cpu;\n"
        "};\n"
    )
    if old in text:
        text = text.replace(old, new, 1)
    elif new not in text:
        raise SystemExit("could not locate vc4-hetero machine state")

    status_code = r"""
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

"""
    marker = "static void vc4_hetero_set_pc(CPUState *cs, vaddr pc)\n"
    if status_code not in text:
        if marker not in text:
            raise SystemExit("could not locate vc4-hetero set_pc helper")
        text = text.replace(marker, status_code + marker, 1)

    start_marker = "static void vc4_hetero_init(MachineState *machine)\n{"
    end_marker = "\nstatic void vc4_hetero_machine_class_init"
    start = text.find(start_marker)
    end = text.find(end_marker, start)
    if start < 0 or end < 0:
        raise SystemExit("could not locate vc4-hetero machine initializer")

    init_code = r"""static void vc4_hetero_init(MachineState *machine)
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
    CPUState *arm;
    CPUState *vc4;

    memory_region_add_subregion(sysmem, 0, machine->ram);
    rom_add_blob_fixed("vc4-hetero-polyglot", polyglot,
                       sizeof(polyglot), 0);
    rom_add_blob_fixed("vc4-hetero-arm-payload", arm_payload,
                       sizeof(arm_payload), VC4_HETERO_ARM_PAYLOAD);

    arm = cpu_create(machine->cpu_type);
    vc4 = cpu_create(TYPE_VC4_VPU_CPU);
    s->arm_cpu = arm;
    s->vc4_cpu = vc4;

    memory_region_init_io(&s->status_mr, OBJECT(machine),
                          &vc4_hetero_status_ops, s,
                          "vc4-hetero-status", VC4_HETERO_STATUS_SIZE);
    memory_region_add_subregion(sysmem, VC4_HETERO_STATUS_BASE,
                                &s->status_mr);

    vc4_hetero_set_pc(arm, 0);
    vc4_hetero_set_pc(vc4, 0);
}
"""
    text = text[:start] + init_code + text[end:]
    machine.write_text(text, encoding="utf-8")


harden_collision_smoke()

# hw source sets are applied against the device/Kconfig dictionary.  The
# TARGET_AARCH64 preprocessor define is therefore not a valid SourceSet key
# here and silently omitted the machine from qemu-system-aarch64.  Reuse the
# existing heterogeneous-frontend Kconfig gate, which is enabled only for the
# AArch64 development target and also pulls in the embedded VC4 frontend.
replace_once(
    ROOT / "hw/arm/meson.build",
    (
        "# Heterogeneous TCG regression: VC4 releases a powered-off Cortex-A53.\n"
        "arm_common_ss.add(when: 'TARGET_AARCH64',\n"
    ),
    (
        "# Heterogeneous TCG regression: VC4 releases a powered-off Cortex-A53.\n"
        "arm_common_ss.add(when: 'CONFIG_VC4_HETERO_SMOKE',\n"
    ),
    "VC4 ARM-release Meson entry",
)
