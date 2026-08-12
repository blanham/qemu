#!/usr/bin/env python3
"""Expose enough VC4 state to localize release-fixture execution failures."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str, what: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old in text:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
    elif new not in text:
        raise SystemExit(f"could not locate {what} in {path}")


machine = ROOT / "hw/arm/vc4_arm_release_smoke.c"
replace_once(
    machine,
    "#define RELEASE_COUNT    0x10\n",
    (
        "#define RELEASE_COUNT    0x10\n"
        "#define RELEASE_VC4_PC   0x14\n"
        "#define RELEASE_VC4_RUN  0x18\n"
    ),
    "VC4 observation registers",
)
replace_once(
    machine,
    (
        "    case RELEASE_COUNT:\n"
        "        return s->release_count;\n"
        "    default:\n"
    ),
    (
        "    case RELEASE_COUNT:\n"
        "        return s->release_count;\n"
        "    case RELEASE_VC4_PC: {\n"
        "        CPUClass *cc;\n\n"
        "        if (!s->vc4_cpu) {\n"
        "            return UINT32_MAX;\n"
        "        }\n"
        "        cc = CPU_GET_CLASS(s->vc4_cpu);\n"
        "        return cc->get_pc ? cc->get_pc(s->vc4_cpu) : UINT32_MAX;\n"
        "    }\n"
        "    case RELEASE_VC4_RUN:\n"
        "        if (!s->vc4_cpu) {\n"
        "            return UINT32_MAX;\n"
        "        }\n"
        "        return (s->vc4_cpu->halted ? 1u : 0u) |\n"
        "               (s->vc4_cpu->stopped ? 2u : 0u) |\n"
        "               (s->vc4_cpu->start_powered_off ? 4u : 0u);\n"
        "    default:\n"
    ),
    "VC4 observation read cases",
)


smoke = ROOT / "scripts/vc4/arm-release-smoke.py"
replace_once(
    smoke,
    (
        "            \"-accel\", \"tcg,thread=single,one-insn-per-tb=on\",\n"
        "            \"-display\", \"none\",\n"
    ),
    (
        "            \"-accel\", \"tcg,thread=single,one-insn-per-tb=on\",\n"
        "            \"-d\", \"guest_errors\",\n"
        "            \"-display\", \"none\",\n"
    ),
    "guest-error logging",
)
replace_once(
    smoke,
    (
        "            cpus = qmp.execute(\"query-cpus-fast\")\n"
        "            if not isinstance(cpus, list) or len(cpus) != 2:\n"
    ),
    (
        "            image_word0 = parse_qtest_value(qtest.send_line(\"readl 0x0\"))\n"
        "            image_word1 = parse_qtest_value(qtest.send_line(\"readl 0x4\"))\n"
        "            if image_word0 != 0x1000B000:\n"
        "                raise RuntimeError(\n"
        "                    f\"VC4 image was not loaded at reset: \"\n"
        "                    f\"word0=0x{image_word0:08x} word1=0x{image_word1:08x}\"\n"
        "                )\n\n"
        "            cpus = qmp.execute(\"query-cpus-fast\")\n"
        "            if not isinstance(cpus, list) or len(cpus) != 2:\n"
    ),
    "loaded-image assertion",
)
replace_once(
    smoke,
    (
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
    (
        "                count = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x10:x}\")\n"
        "                )\n"
        "                vc4_pc = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x14:x}\")\n"
        "                )\n"
        "                vc4_run = parse_qtest_value(\n"
        "                    qtest.send_line(f\"readl 0x{RELEASE_BASE + 0x18:x}\")\n"
        "                )\n"
        "                image_word0 = parse_qtest_value(qtest.send_line(\"readl 0x0\"))\n"
        "                image_word1 = parse_qtest_value(qtest.send_line(\"readl 0x4\"))\n"
        "                entry = entry_lo | (entry_hi << 32)\n"
        "                raise RuntimeError(\n"
        "                    f\"ARM marker never appeared: got 0x{marker:08x}, \"\n"
        "                    f\"expected 0x{MARKER_VALUE:08x}; \"\n"
        "                    f\"entry=0x{entry:016x} control=0x{control:08x} \"\n"
        "                    f\"status=0x{status:08x} releases={count} \"\n"
        "                    f\"vc4_pc=0x{vc4_pc:08x} vc4_run=0x{vc4_run:08x} \"\n"
        "                    f\"word0=0x{image_word0:08x} word1=0x{image_word1:08x}\"\n"
        "                )\n"
    ),
    "VC4 timeout observation",
)
