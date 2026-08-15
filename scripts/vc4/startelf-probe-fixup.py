#!/usr/bin/env python3
"""Harden live VPU register capture in the start.elf probe.

The HMP bridge accepts one command at a time.  Select the CPU reported by
``query-cpus-fast`` through the QMP ``cpu-index`` argument instead of sending a
newline-separated ``cpu 4`` and ``info registers`` command string.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/vc4/raspi3_startelf_probe.py"
MARKER = "cpu_index: int = 0"


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
        """    def hmp(self, command: str) -> str:
        result = self.execute(
            \"human-monitor-command\",
            {\"command-line\": command, \"cpu-index\": 0},
        )
""",
        """    def hmp(self, command: str, cpu_index: int = 0) -> str:
        result = self.execute(
            \"human-monitor-command\",
            {\"command-line\": command, \"cpu-index\": cpu_index},
        )
""",
        "QMP HMP helper",
    )
    text = replace_once(
        text,
        """            report[\"registers\"] = qmp.hmp(\"cpu 4\\ninfo registers\")
""",
        """            live_cpu = report.get(\"live_vpu\", {}).get(\"cpu\", {})
            vpu_index = int(live_cpu.get(\"cpu-index\", 4))
            report[\"registers\"] = qmp.hmp(
                \"info registers\", cpu_index=vpu_index
            )
""",
        "VPU register capture",
    )
    PATH.write_text(text, encoding="utf-8")
    print("Materialized robust VPU register capture for start.elf probing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
