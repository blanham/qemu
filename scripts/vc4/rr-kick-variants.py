#!/usr/bin/env python3
"""Materialize one guarded single-threaded TCG RR kick experiment.

The script is intentionally not a production-code generator by itself.  The
companion workflow restores the scheduler source before each variant, builds
QEMU, runs the passive five-vCPU fairness regression, and requires stock
``bootcode.bin`` to cross its former 0x544 timer frontier before committing a
winning source file.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
CALLBACK = re.compile(
    r"static void rr_kick_vcpu_thread\(void \*opaque\)\n"
    r"\{\n.*?\n\}",
    re.DOTALL,
)


def replace_callback(text: str, body: str) -> str:
    match = CALLBACK.search(text)
    if match is None:
        raise SystemExit("could not locate rr_kick_vcpu_thread()")
    return text[: match.start()] + body + text[match.end() :]


def active_fallback(text: str) -> str:
    if "static CPUState *rr_kick_cpu;" not in text:
        raise SystemExit(
            "active-fallback requires rr-active-cpu-fixup.py first"
        )
    callback = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *cpu = qatomic_read(&rr_kick_cpu);

    /* A timer racing with handoff still has a safe shared-thread target. */
    if (!cpu) {
        cpu = opaque;
    }
    cpu_exit(cpu);
}"""
    return replace_callback(text, callback)


def thread_peers(text: str) -> str:
    callback = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *owner = opaque;
    CPUState *cpu;

    /*
     * Every vCPU attached to the same single-threaded TCG worker is a valid
     * exit target.  Setting all peer exit requests guarantees that the CPU
     * currently consuming the shared worker observes the kick, regardless of
     * which CPU object created the RR thread and timer.
     */
    CPU_FOREACH(cpu) {
        if (cpu->thread == owner->thread) {
            cpu_exit(cpu);
        }
    }
}"""
    return replace_callback(text, callback)


def active_and_peers(text: str) -> str:
    if "static CPUState *rr_kick_cpu;" not in text:
        raise SystemExit(
            "active-and-peers requires rr-active-cpu-fixup.py first"
        )
    callback = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *active = qatomic_read(&rr_kick_cpu);
    CPUState *owner = opaque;
    CPUState *cpu;

    if (active) {
        cpu_exit(active);
    }
    CPU_FOREACH(cpu) {
        if (cpu != active && cpu->thread == owner->thread) {
            cpu_exit(cpu);
        }
    }
}"""
    return replace_callback(text, callback)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "variant",
        choices=("active-fallback", "thread-peers", "active-and-peers"),
    )
    args = parser.parse_args()

    text = SOURCE.read_text(encoding="utf-8")
    if args.variant == "active-fallback":
        text = active_fallback(text)
    elif args.variant == "thread-peers":
        text = thread_peers(text)
    else:
        text = active_and_peers(text)
    SOURCE.write_text(text, encoding="utf-8")
    print(f"Materialized RR kick experiment: {args.variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
