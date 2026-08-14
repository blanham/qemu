#!/usr/bin/env python3
"""Add the first ARM-kernel handoff marker to the start.elf probe.

The firmware volume's ``KERNEL8.IMG`` writes ``0x4a11c0de`` to shared RAM at
``0x1000`` and then loops.  Recording that word from the same SDRAM snapshot
used for start.elf signatures creates a strict, host-loader-free acceptance
condition for the first VideoCore-to-Cortex-A53 kernel handoff.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/vc4/raspi3_startelf_probe.py"
MARKER = "ARM_MARKER_ADDRESS = 0x1000"


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if old in text:
        return text.replace(old, new, 1)
    if new in text:
        return text
    raise SystemExit(f"could not locate {what}")


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """from typing import Any


class QMPError""",
        """from typing import Any

ARM_MARKER_ADDRESS = 0x1000
ARM_MARKER_VALUE = 0x4A11C0DE


class QMPError""",
        "ARM marker constants",
    )
    text = replace_once(
        text,
        """                report[\"ram_bytes\"] = len(memory)
                report[\"start_elf_matches\"] = locate_windows(memory, windows)
""",
        """                report[\"ram_bytes\"] = len(memory)
                report[\"start_elf_matches\"] = locate_windows(memory, windows)
                report[\"arm_marker_address\"] = ARM_MARKER_ADDRESS
                if len(memory) >= ARM_MARKER_ADDRESS + 4:
                    marker_bytes = memory[
                        ARM_MARKER_ADDRESS : ARM_MARKER_ADDRESS + 4
                    ]
                    report[\"arm_marker\"] = int.from_bytes(
                        marker_bytes, \"little\"
                    )
                else:
                    report[\"arm_marker\"] = None
""",
        "SDRAM marker capture",
    )
    text = replace_once(
        text,
        """    matches = report.get(\"start_elf_matches\", [])
    success = len(matches) >= 2
""",
        """    matches = report.get(\"start_elf_matches\", [])
    marker = report.get(\"arm_marker\")
    if marker == ARM_MARKER_VALUE:
        print(
            \"STARTELF_KERNEL_HANDOFF \"
            f\"marker=0x{marker:08x} machine={report['machine']}\"
        )
    success = len(matches) >= 2
""",
        "kernel-handoff status output",
    )
    PATH.write_text(text, encoding="utf-8")
    print("Materialized ARM kernel marker capture in the start.elf probe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
