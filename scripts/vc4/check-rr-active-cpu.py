#!/usr/bin/env python3
"""Validate active-CPU targeting in the single-threaded TCG RR scheduler.

The stock VC4 integration line already carries the production scheduler fix.
This checker keeps the validation lane useful without rematerializing an older,
competing ``rr_kick_cpu`` implementation or mutating source during CI.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")

    require(
        "static CPUState *rr_current_cpu;" in text,
        "RR scheduler does not publish the current CPU",
    )

    kick_start = text.find("static void rr_kick_next_cpu(void)")
    kick_end = text.find("\n}\n\nstatic void rr_kick_thread", kick_start)
    require(kick_start >= 0 and kick_end > kick_start, "could not delimit RR kick helper")
    kick = text[kick_start:kick_end]
    for fragment in (
        "qatomic_read(&rr_current_cpu)",
        "cpu_exit(cpu);",
        "smp_mb();",
        "cpu != qatomic_read(&rr_current_cpu)",
    ):
        require(fragment in kick, f"RR kick helper is missing invariant: {fragment}")

    publish = text.find("qatomic_set_mb(&rr_current_cpu, cpu);")
    exit_check = text.find(
        "qatomic_load_acquire(&cpu->exit_request)",
        publish,
    )
    execute = text.find("tcg_cpu_exec(cpu)", exit_check)
    clear = text.find("qatomic_set(&rr_current_cpu, NULL);", execute)
    require(
        0 <= publish < exit_check < execute < clear,
        "RR current-CPU publication does not bracket the execution slice",
    )

    require(
        "return icount_enabled() ? QEMU_CLOCK_VIRTUAL : QEMU_CLOCK_REALTIME;"
        in text,
        "non-icount RR preemption is not driven by host realtime",
    )
    require(
        "timer_new_ns(rr_kick_clock()," in text,
        "RR kick timer does not use the selected scheduling clock",
    )

    print("RR active-CPU publication, preemption, and ordering invariants hold.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
