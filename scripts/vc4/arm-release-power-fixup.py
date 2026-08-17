#!/usr/bin/env python3
"""Route the heterogeneous release fixture through ARM power control."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


# ARM power-control helpers historically assumed that every CPU in CPU_FOREACH
# was an ARM CPU.  A heterogeneous process also contains the VC4 CPU, so skip
# unrelated QOM types before applying the checked ARM cast.
powerctl = ROOT / "target/arm/arm-powerctl.c"
replace_once(
    powerctl,
    (
        "    CPU_FOREACH(cpu) {\n"
        "        ARMCPU *armcpu = ARM_CPU(cpu);\n\n"
        "        if (arm_cpu_mp_affinity(armcpu) == id) {\n"
    ),
    (
        "    CPU_FOREACH(cpu) {\n"
        "        ARMCPU *armcpu;\n\n"
        "        if (!object_dynamic_cast(OBJECT(cpu), TYPE_ARM_CPU)) {\n"
        "            continue;\n"
        "        }\n"
        "        armcpu = ARM_CPU(cpu);\n\n"
        "        if (arm_cpu_mp_affinity(armcpu) == id) {\n"
    ),
    "heterogeneous-safe ARM CPU lookup",
)


machine = ROOT / "hw/arm/vc4_arm_release_smoke.c"
text = machine.read_text(encoding="utf-8")
include = '#include "target/arm/arm-powerctl.h"\n'
if include not in text:
    marker = '#include "target/arm/cpu.h"\n'
    if marker not in text:
        raise SystemExit("could not locate ARM CPU include in release machine")
    text = text.replace(marker, marker + include, 1)

start_marker = "static void vc4_arm_release_cpu(VC4ArmReleaseMachineState *s)\n{"
end_marker = "\nstatic uint64_t vc4_arm_release_read"
start = text.find(start_marker)
end = text.find(end_marker, start)
if start < 0 or end < 0:
    raise SystemExit("could not locate VC4-to-ARM release callback")

release_code = r'''static void vc4_arm_release_cpu(VC4ArmReleaseMachineState *s)
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
'''
text = text[:start] + release_code + text[end:]
machine.write_text(text, encoding="utf-8")


# Preserve the critical MMIO-side state when execution fails.  This tells us
# whether VC4 failed to issue the transaction or ARM accepted it but did not
# reach the payload.
smoke = ROOT / "scripts/vc4/arm-release-smoke.py"
replace_once(
    smoke,
    (
        "            if marker != MARKER_VALUE:\n"
        "                raise RuntimeError(\n"
        "                    f\"ARM marker never appeared: got 0x{marker:08x}, \"\n"
        "                    f\"expected 0x{MARKER_VALUE:08x}\"\n"
        "                )\n"
    ),
    (
        "            if marker != MARKER_VALUE:\n"
        "                entry_lo = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x00:x}\")\n"
        "                )\n"
        "                entry_hi = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x04:x}\")\n"
        "                )\n"
        "                control = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x08:x}\")\n"
        "                )\n"
        "                status = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x0c:x}\")\n"
        "                )\n"
        "                count = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x10:x}\")\n"
        "                )\n"
        "                entry = entry_lo | (entry_hi << 32)\n"
        "                raise RuntimeError(\n"
        "                    f\"ARM marker never appeared: got 0x{marker:08x}, \"\n"
        "                    f\"expected 0x{MARKER_VALUE:08x}; \"\n"
        "                    f\"entry=0x{entry:016x} control=0x{control:08x} \"\n"
        "                    f\"status=0x{status:08x} releases={count}\"\n"
        "                )\n"
    ),
    "release-timeout diagnostics",
)
