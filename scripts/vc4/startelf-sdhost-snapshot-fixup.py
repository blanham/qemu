#!/usr/bin/env python3
"""Capture Pi 3 SDHOST and boot-control MMIO at the start.elf frontier.

The snapshot is taken after the VM is stopped, so it cannot perturb the live
firmware path.  It distinguishes a first stage which never issued the
second-stage read from one which transferred data but failed during relocation
or execution.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/vc4/raspi3_startelf_probe.py"
MARKER = 'report["sdhost_registers"]'


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
        """            report[\"registers\"] = qmp.hmp(
                \"info registers\", cpu_index=vpu_index
            )
            try:
""",
        """            report[\"registers\"] = qmp.hmp(
                \"info registers\", cpu_index=vpu_index
            )
            # ARM physical aliases of the shared VideoCore peripherals.
            report[\"system_timer\"] = qmp.hmp(
                \"xp /2wx 0x3f003004\"
            )
            report[\"sdhost_registers\"] = qmp.hmp(
                \"xp /24wx 0x3f202000\"
            )
            report[\"power_registers\"] = qmp.hmp(
                \"xp /16wx 0x3f100100\"
            )
            try:
""",
        "post-stop VPU register capture",
    )
    PATH.write_text(text, encoding="utf-8")
    print("Materialized SDHOST and boot-control snapshot capture.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
