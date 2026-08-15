#!/usr/bin/env python3
"""Materialize exact instruction encodings in the VC4 timer fixture."""

from pathlib import Path

PATH = Path(__file__).with_name("raspi3-systimer-delay-smoke.py")
TIMER_OLD = "    program += smoke.vc4_memory_offset(False, 2, 1, 4)\n"
TIMER_NEW = "    program += smoke.half(0x2112)  # ld r2, [r1, 4]\n"
ARM_OLD = "        a64_movz(0, ARM_MARKER_ADDR, sf=True),\n"
ARM_NEW = (
    "        a64_movz(0, ARM_MARKER_ADDR & 0xFFFF, sf=True),\n"
    "        a64_movk(0, ARM_MARKER_ADDR >> 16, shift=16, sf=True),\n"
)


def materialize(text: str, old: str, new: str, description: str) -> str:
    if new in text:
        if old in text:
            raise RuntimeError(f"fixture contains both {description} encodings")
        return text
    if text.count(old) != 1:
        raise RuntimeError(f"could not locate the synthetic {description}")
    return text.replace(old, new)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    text = materialize(text, TIMER_OLD, TIMER_NEW, "timer load")
    text = materialize(text, ARM_OLD, ARM_NEW, "ARM marker address")
    PATH.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())