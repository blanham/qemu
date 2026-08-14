#!/usr/bin/env python3
"""Use a host-monotonic kick for non-icount single-threaded TCG."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
MARKER = "Non-icount RR preemption must not depend on guest clock progress."


def replace_once(text: str, old: str, new: str, what: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise SystemExit(f"could not locate {what} in {PATH}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")

    helper_old = """
static QEMUTimer *rr_kick_vcpu_timer;
static CPUState *rr_current_cpu;

static inline int64_t rr_next_kick_time(void)
{
    return qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + TCG_KICK_PERIOD;
}
""".lstrip()
    helper_new = f"""
static QEMUTimer *rr_kick_vcpu_timer;
static CPUState *rr_current_cpu;

/*
 * {MARKER}
 *
 * In deterministic icount mode the virtual clock remains the scheduling
 * authority.  Otherwise this timer is purely a host-side fairness mechanism:
 * using QEMU_CLOCK_REALTIME guarantees that a CPU in a tight translated loop
 * can be preempted even when no guest I/O or monitor traffic wakes the main
 * loop.
 */
static QEMUClockType rr_kick_clock(void)
{{
    return icount_enabled() ? QEMU_CLOCK_VIRTUAL : QEMU_CLOCK_REALTIME;
}}

static inline int64_t rr_next_kick_time(void)
{{
    return qemu_clock_get_ns(rr_kick_clock()) + TCG_KICK_PERIOD;
}}
""".lstrip()
    text = replace_once(
        text, helper_old, helper_new, "round-robin kick clock helper"
    )

    timer_old = (
        "        rr_kick_vcpu_timer = "
        "timer_new_ns(QEMU_CLOCK_VIRTUAL,\n"
        "                                           "
        "rr_kick_thread, NULL);\n"
    )
    timer_new = (
        "        rr_kick_vcpu_timer = "
        "timer_new_ns(rr_kick_clock(),\n"
        "                                           "
        "rr_kick_thread, NULL);\n"
    )
    text = replace_once(text, timer_old, timer_new, "kick timer creation")

    PATH.write_text(text, encoding="utf-8")
    print("Materialized host-monotonic non-icount RR preemption.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
