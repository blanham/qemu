#!/usr/bin/env python3
"""Repair the generated display timing smoke's virtual-clock stepping.

QTest's ``clock_step N`` may stop at an earlier pending timer deadline rather
than reaching the full requested delta.  A Raspberry Pi machine has several
independent timers, so a single 20 ms step is not a reliable witness for the
pixel-valve's 16.67 ms VFP-start timer.  Advance repeatedly to an absolute
virtual-time target instead.
"""

from __future__ import annotations

from pathlib import Path


SMOKE = Path("scripts/vc4/display-timing-smoke.py")
CALL = 'qtest.command(f"clock_step {FRAME_STEP_NS}")'
REPLACEMENT = "advance_clock(qtest, FRAME_STEP_NS)"
ANCHOR = "def exercise_hdmi(qtest: Any) -> None:\n"
HELPER = '''def advance_clock(qtest: Any, nanoseconds: int) -> None:
    """Advance by at least *nanoseconds*, servicing earlier deadlines."""

    if nanoseconds <= 0:
        raise ValueError("nanoseconds must be positive")

    # Obtain a stable absolute baseline.  qemu_clock_advance_virtual_time()
    # may stop at any earlier timer, so use each returned absolute time to
    # calculate the remaining distance to our target.
    values = qtest.command("clock_step 1")
    if len(values) != 1:
        raise RuntimeError(f"unexpected clock_step reply: {values!r}")
    now = int(values[0], 0)
    target = now + nanoseconds

    for _ in range(100000):
        if now >= target:
            return
        values = qtest.command(f"clock_step {target - now}")
        if len(values) != 1:
            raise RuntimeError(f"unexpected clock_step reply: {values!r}")
        advanced = int(values[0], 0)
        if advanced <= now:
            raise RuntimeError(
                f"virtual clock did not advance: old={now} new={advanced}"
            )
        now = advanced

    raise RuntimeError(
        f"virtual clock did not reach target {target} after 100000 deadlines"
    )


'''


def main() -> int:
    text = SMOKE.read_text()

    if HELPER not in text:
        if text.count(ANCHOR) != 1:
            raise SystemExit("display timing smoke helper anchor changed")
        text = text.replace(ANCHOR, HELPER + ANCHOR, 1)

    count = text.count(CALL)
    if count == 0 and REPLACEMENT not in text:
        raise SystemExit("display timing smoke has no frame-step calls")
    text = text.replace(CALL, REPLACEMENT)

    if CALL in text:
        raise SystemExit("not all frame-step calls were repaired")
    if text.count(REPLACEMENT) < 4:
        raise SystemExit(
            "expected at least four deadline-aware frame advances, found "
            f"{text.count(REPLACEMENT)}"
        )

    SMOKE.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
