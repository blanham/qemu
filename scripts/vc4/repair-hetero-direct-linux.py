#!/usr/bin/env python3
"""Repair the heterogeneous direct-Linux materializer for modern QEMU."""

from pathlib import Path


PATH = Path("scripts/vc4/hetero-direct-linux-integrate.py")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    text = replace_once(
        text,
        """        '#define VC4_IC1_OFFSET 0x2800\\n',
        '#define VC4_IC1_OFFSET 0x2800\\n'
        '#define VC4_SMPBOOT_ADDR 0x300\\n'
""",
        """        '#define VC4_IC1_OFFSET 0x2800\\n',
        '#define VC4_IC1_OFFSET 0x2800\\n'
        '#define VC4_RASPI3_LEGACY_MACHINE_ID 3138\\n'
        '#define VC4_SMPBOOT_ADDR 0x300\\n'
""",
        "direct-boot machine ID definition",
    )
    text = replace_once(
        text,
        """        s->binfo.board_id = MACH_TYPE_BCM2708;
""",
        """        s->binfo.board_id = VC4_RASPI3_LEGACY_MACHINE_ID;
""",
        "direct-boot board ID use",
    )
    if "MACH_TYPE_BCM2708" in text:
        raise RuntimeError("obsolete MACH_TYPE_BCM2708 reference remains")
    PATH.write_text(text, encoding="utf-8")
    print("repaired heterogeneous direct-Linux machine ID")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
