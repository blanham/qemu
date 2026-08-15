#!/usr/bin/env python3
"""Disable device-tree loading for the first marker-kernel handoff.

The deterministic AArch64 marker payload does not consume firmware arguments.
Requesting an empty ``device_tree`` in CONFIG.TXT prevents a missing production
Pi 3 DTB from becoming an unrelated prerequisite for proving that start.elf
can load and release the first ARM payload.  Later Linux and UEFI volumes use
separate, explicit platform-description fixtures.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/vc4/raspi3_startelf_volume_smoke.py"

OLD = """CONFIG_TXT = (
    \"arm_64bit=1\\n\"
    \"kernel=kernel8.img\\n\"
    \"enable_uart=1\\n\"
    \"disable_commandline_tags=1\\n\"
).encode(\"ascii\")
"""

NEW = """CONFIG_TXT = (
    \"arm_64bit=1\\n\"
    \"kernel=kernel8.img\\n\"
    \"device_tree=\\n\"
    \"enable_uart=1\\n\"
    \"disable_commandline_tags=1\\n\"
).encode(\"ascii\")
"""


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if OLD in text:
        PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    elif NEW not in text:
        raise SystemExit("could not locate marker-kernel CONFIG.TXT fixture")
    print("Materialized device-tree-free marker-kernel configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
