#!/usr/bin/env python3
"""Publish the CPU selected by the shared RR thread before each TCG slice.

Single-threaded TCG rotates one host thread among multiple ``CPUState`` objects.
The thread-local ``current_cpu`` must therefore follow that rotation instead of
remaining the CPU which originally created the thread.  Mixed ARM/VC4
execution makes stale ownership visible in clock accounting, MMIO helpers, and
preemption targeting.

The companion workflow treats this as a guarded candidate: it is committed
only after passive five-vCPU fairness and unmodified stock firmware both pass.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
MARKER = "The shared RR thread changes guest CPU identity every slice"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("RR current_cpu tracking is already materialized.")
        return 0

    match = re.search(
        r"^(?P<indent>[ \t]*)(?P<statement>[^\n;]*"
        r"tcg_cpus_exec\(cpu\)[^\n;]*;)[ \t]*$",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise SystemExit("could not locate tcg_cpus_exec(cpu) statement")
    indentation = match.group("indent")
    statement = match.group("statement").lstrip()
    replacement = f"""{indentation}/*
{indentation} * The shared RR thread changes guest CPU identity every slice.
{indentation} * Keep thread-local CPU context aligned with the object whose
{indentation} * translated code, MMIO helpers, and accounting are now active.
{indentation} */
{indentation}current_cpu = cpu;
{indentation}{statement}"""
    text = text[: match.start()] + replacement + text[match.end() :]
    PATH.write_text(text, encoding="utf-8")
    print("Materialized RR current_cpu tracking.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
