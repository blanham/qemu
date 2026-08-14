#!/usr/bin/env python3
"""Make the single-threaded TCG RR kick target the actively executing CPU.

A realtime timer callback may execute outside the vCPU thread and must not rely
on thread-local ``current_cpu`` or on the CPU object which happened to create
the sole RR thread.  Publish the active CPU explicitly around each execution
slice and have the timer callback kick that object.

The transform is idempotent and rejects unknown scheduler source shapes.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
MARKER = "RR kick target follows the active execution slice"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("RR active-CPU kick target is already materialized.")
        return 0

    declaration = "static QEMUTimer *rr_kick_vcpu_timer;\n"
    if declaration not in text:
        raise SystemExit("could not locate RR kick timer declaration")
    text = text.replace(
        declaration,
        declaration
        + "\n/* RR kick target follows the active execution slice. */\n"
        + "static CPUState *rr_kick_cpu;\n",
        1,
    )

    callback = re.compile(
        r"static void rr_kick_vcpu_thread\(void \*opaque\)\n"
        r"\{\n.*?\n\}",
        re.DOTALL,
    )
    match = callback.search(text)
    if match is None:
        raise SystemExit("could not locate RR kick callback")
    replacement = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *cpu = qatomic_read(&rr_kick_cpu);

    /*
     * The timer may run outside the vCPU thread.  Its opaque argument is the
     * CPU which created the shared RR thread, not necessarily the CPU which
     * currently owns that thread.
     */
    (void)opaque;
    if (cpu) {
        cpu_exit(cpu);
    }
}"""
    text = text[: match.start()] + replacement + text[match.end() :]

    timer_calls = (
        "timer_mod(rr_kick_vcpu_timer,",
        "timer_mod_ns(rr_kick_vcpu_timer,",
    )
    timer_pos = min(
        (text.find(call) for call in timer_calls if text.find(call) >= 0),
        default=-1,
    )
    if timer_pos < 0:
        raise SystemExit("could not locate RR timer arming call")
    line_start = text.rfind("\n", 0, timer_pos) + 1
    indentation = text[line_start:timer_pos]
    if indentation.strip():
        raise SystemExit("unexpected text before RR timer arming call")
    text = (
        text[:line_start]
        + indentation
        + "qatomic_set(&rr_kick_cpu, cpu);\n"
        + text[line_start:]
    )

    exec_pos = text.find("tcg_cpus_exec(cpu)", line_start)
    if exec_pos < 0:
        raise SystemExit("could not locate RR CPU execution call")
    timer_del = "timer_del(rr_kick_vcpu_timer);"
    del_pos = text.find(timer_del, exec_pos)
    if del_pos >= 0:
        line_end = text.find("\n", del_pos)
        if line_end < 0:
            line_end = len(text)
        del_line_start = text.rfind("\n", 0, del_pos) + 1
        del_indent = text[del_line_start:del_pos]
        text = (
            text[: line_end + 1]
            + del_indent
            + "qatomic_set(&rr_kick_cpu, NULL);\n"
            + text[line_end + 1 :]
        )
    else:
        statement_end = text.find(";", exec_pos)
        if statement_end < 0:
            raise SystemExit("could not delimit RR CPU execution statement")
        line_end = text.find("\n", statement_end)
        if line_end < 0:
            line_end = len(text)
        exec_line_start = text.rfind("\n", 0, exec_pos) + 1
        exec_indent = text[exec_line_start:exec_pos]
        text = (
            text[: line_end + 1]
            + exec_indent
            + "qatomic_set(&rr_kick_cpu, NULL);\n"
            + text[line_end + 1 :]
        )

    PATH.write_text(text, encoding="utf-8")
    print("Materialized active-CPU targeting for the RR kick timer.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
