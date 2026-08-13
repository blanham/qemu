#!/usr/bin/env python3
"""Materialize the exact compact load in the VC4 timer-loop fixture."""

from pathlib import Path

PATH = Path(__file__).with_name("raspi3-systimer-delay-smoke.py")
OLD = "    program += smoke.vc4_memory_offset(False, 2, 1, 4)\n"
NEW = "    program += smoke.half(0x2112)  # ld r2, [r1, 4]\n"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if NEW in text:
        if OLD in text:
            raise RuntimeError("timer fixture contains both load encodings")
        return 0
    if text.count(OLD) != 1:
        raise RuntimeError("could not locate the synthetic timer load")

    PATH.write_text(text.replace(OLD, NEW), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
