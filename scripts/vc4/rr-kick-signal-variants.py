#!/usr/bin/env python3
"""Materialize guarded RR variants using QEMU's full CPU-kick path.

``cpu_exit()`` sets the guest exit request, which is sufficient when the
actively executing TCG loop observes it promptly.  ``qemu_cpu_kick()`` also
performs the host-thread wakeup required when the callback races with a shared
single-threaded TCG handoff.  The companion workflow tests these variants
without publishing one unless it preserves passive fairness and advances stock
Raspberry Pi firmware beyond its former delay frontier.

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


def active_kick(text: str) -> str:
    if "static CPUState *rr_kick_cpu;" not in text:
        raise SystemExit("active-kick requires rr-active-cpu-fixup.py first")
    callback = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *cpu = qatomic_read(&rr_kick_cpu);

    (void)opaque;
    if (cpu) {
        qemu_cpu_kick(cpu);
    }
}"""
    return replace_callback(text, callback)


def peer_kick(text: str) -> str:
    callback = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *owner = opaque;
    CPUState *cpu;

    CPU_FOREACH(cpu) {
        if (cpu->thread == owner->thread) {
            qemu_cpu_kick(cpu);
        }
    }
}"""
    return replace_callback(text, callback)


def active_peer_kick(text: str) -> str:
    if "static CPUState *rr_kick_cpu;" not in text:
        raise SystemExit(
            "active-peer-kick requires rr-active-cpu-fixup.py first"
        )
    callback = """static void rr_kick_vcpu_thread(void *opaque)
{
    CPUState *active = qatomic_read(&rr_kick_cpu);
    CPUState *owner = opaque;
    CPUState *cpu;

    if (active) {
        qemu_cpu_kick(active);
    }
    CPU_FOREACH(cpu) {
        if (cpu != active && cpu->thread == owner->thread) {
            qemu_cpu_kick(cpu);
        }
    }
}"""
    return replace_callback(text, callback)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "variant",
        choices=("active-kick", "peer-kick", "active-peer-kick"),
    )
    args = parser.parse_args()

    text = SOURCE.read_text(encoding="utf-8")
    if args.variant == "active-kick":
        text = active_kick(text)
    elif args.variant == "peer-kick":
        text = peer_kick(text)
    else:
        text = active_peer_kick(text)
    SOURCE.write_text(text, encoding="utf-8")
    print(f"Materialized RR host-kick experiment: {args.variant}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
