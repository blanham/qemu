#!/usr/bin/env python3
"""Repair start.elf probes for the current heterogeneous Pi 3 machine."""

from __future__ import annotations

from pathlib import Path


MACHINE = "raspi3b-vc4-hetero"


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 1 and new_count == 0:
        path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return
    if old_count == 0 and new_count == 1:
        return
    raise SystemExit(
        f"unexpected {label} state in {path}: "
        f"old={old_count} new={new_count}"
    )


def repair_probe() -> None:
    path = Path("scripts/vc4/raspi3-startelf-probe.py")
    replace_once(
        path,
        '''            "-M",
            "raspi3b-vc4",
            "-m",
            "1G",
''',
        f'''            "-M",
            "{MACHINE}",
            "-m",
            "1G",
            "-smp",
            "5",
''',
        "start.elf probe machine",
    )


def repair_live_probe() -> None:
    path = Path("scripts/vc4/raspi3-startelf-live-probe.py")
    replace_once(
        path,
        '''            "-M", "raspi3b-vc4",
            "-m", "1G",
''',
        f'''            "-M", "{MACHINE}",
            "-m", "1G",
            "-smp", "5",
''',
        "live start.elf probe machine",
    )


def repair_arm_trace() -> None:
    path = Path("scripts/vc4/raspi3-startelf-arm-trace.py")
    replace_once(
        path,
        '''            "-M",
            "raspi3b-vc4",
            "-m",
            "1G",
''',
        f'''            "-M",
            "{MACHINE}",
            "-m",
            "1G",
            "-smp",
            "5",
''',
        "start.elf ARM trace machine",
    )


def main() -> int:
    repair_probe()
    repair_live_probe()
    repair_arm_trace()
    print("repaired VC4 start.elf probes for raspi3b-vc4-hetero")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
