#!/usr/bin/env python3
"""Harden and correctly link the VC4-controlled ARM-release fixture."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# QEMU translation units must include qemu/osdep.h before target headers.
# The embedded VC4 wrappers intentionally include target/arm/cpu.h to expose
# the primary frontend's CPUArchState type, but doing so before osdep leaves
# fundamental C/QEMU types and QEMU_BUILD_BUG_ON undefined.
for name in ("cpu", "gdbstub", "op_helper", "translate"):
    wrapper = ROOT / f"target/arm/vc4-secondary/{name}.c"
    text = wrapper.read_text(encoding="utf-8")
    old = (
        "#define VC4_SECONDARY_FRONTEND 1\n"
        '#include "target/arm/cpu.h"\n'
    )
    new = (
        '#include "qemu/osdep.h"\n'
        "#define VC4_SECONDARY_FRONTEND 1\n"
        '#include "target/arm/cpu.h"\n'
    )
    if old in text:
        wrapper.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate include prologue in {wrapper}")

# The secondary wrappers include primary-target headers and target-width TCG
# APIs, so they must not live in either ARM target-neutral source set.
# QEMU shares arm_common_system_ss and arm_system_ss between ARM32 and AArch64
# and poisons TARGET_AARCH64, TARGET_LONG_BITS, and target endianness macros
# while compiling them.  Keep the Kconfig gate, but move the wrappers into
# arm_ss, which is compiled once per concrete target executable.
target_meson = ROOT / "target/arm/meson.build"
text = target_meson.read_text(encoding="utf-8")
old = "arm_common_system_ss.add(when: 'CONFIG_VC4_HETERO_SMOKE', if_true: files(\n"
new = "arm_ss.add(when: 'CONFIG_VC4_HETERO_SMOKE', if_true: files(\n"
if old in text:
    target_meson.write_text(text.replace(old, new, 1), encoding="utf-8")
elif new not in text:
    raise SystemExit("could not locate secondary VC4 frontend Meson entry")

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

# hw source sets are applied against the device/Kconfig dictionary.  The
# TARGET_AARCH64 preprocessor define is therefore not a valid SourceSet key
# here and silently omitted the machine from qemu-system-aarch64.  Reuse the
# existing heterogeneous-frontend Kconfig gate, which is enabled only for the
# AArch64 development target and also pulls in the embedded VC4 frontend.
meson = ROOT / "hw/arm/meson.build"
text = meson.read_text(encoding="utf-8")
old = (
    "# Heterogeneous TCG regression: VC4 releases a powered-off Cortex-A53.\n"
    "arm_common_ss.add(when: 'TARGET_AARCH64',\n"
)
new = (
    "# Heterogeneous TCG regression: VC4 releases a powered-off Cortex-A53.\n"
    "arm_common_ss.add(when: 'CONFIG_VC4_HETERO_SMOKE',\n"
)
if old in text:
    meson.write_text(text.replace(old, new, 1), encoding="utf-8")
elif new not in text:
    raise SystemExit("could not locate VC4 ARM-release Meson entry")
