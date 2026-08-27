#!/usr/bin/env python3
"""Correct the ppce500 reset-state translation fixture after live probing."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHECKER = ROOT / "scripts/ci/check-wd40-address-translation-service.py"

OLD = '''    TargetCase(
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        target="ppc",
        target_bits=32,
        big_endian=True,
        address=0x10000,
        translated=False,
    ),
'''

NEW = '''    TargetCase(
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        target="ppc",
        target_bits=32,
        big_endian=True,
        address=0x10000,
        translated=True,
        physical_address=0x10000,
    ),
'''


def main() -> None:
    text = CHECKER.read_text(encoding="utf-8")
    old_count = text.count(OLD)
    new_count = text.count(NEW)

    if old_count == 0 and new_count == 1:
        return
    if old_count == 1 and new_count == 0:
        CHECKER.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
        return

    raise RuntimeError(
        "address-translation checker has an ambiguous ppce500 fixture: "
        f"old={old_count}, new={new_count}"
    )


if __name__ == "__main__":
    main()
