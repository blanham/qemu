#!/usr/bin/env python3
"""Verify that pinned Raspberry Pi firmware cleared the old float-imm6 traps."""

from __future__ import annotations

import argparse
from pathlib import Path
import re

ILLEGAL_RE = re.compile(
    r"VideoCore IV: unimplemented opcode 0x([0-9a-fA-F]+) "
    r"at 0x([0-9a-fA-F]+)"
)
BARRIER_RE = re.compile(r"^STOCK_BOOTCODE_BARRIER (.+)$", re.MULTILINE)
PC_RE = re.compile(r"\bpc=0x([0-9a-fA-F]+)\b")
KIND_RE = re.compile(r"\bkind=([a-z0-9-]+)\b")
OPCODE_RE = re.compile(r"\bopcode=0x([0-9a-fA-F]+)\b")
LOW_ZERO_RE = re.compile(
    r"VC4_LOW_TARGET source=0x0000166c target=0x00000000\b"
)

OLD_TRAPS = {
    (0xC800, 0x25C4): "fadd r0, r0, #0.5",
    (0xC844, 0x26BC): "fmul r4, r4, #0.5",
    (0xC805, 0x26DA): "fadd r5, r5, #0.5",
}
OLD_COLLAPSE_PCS = {0x00000000, 0x00000014}


def verify_log(text: str) -> str:
    for match in ILLEGAL_RE.finditer(text):
        key = (int(match.group(1), 16), int(match.group(2), 16))
        if key in OLD_TRAPS:
            raise ValueError(
                "official firmware still hit old float-immediate trap: "
                f"opcode=0x{key[0]:04x} pc=0x{key[1]:08x} "
                f"instruction={OLD_TRAPS[key]}"
            )

    if LOW_ZERO_RE.search(text):
        raise ValueError(
            "official firmware reproduced the old POP-to-zero-PC collapse"
        )

    barriers = BARRIER_RE.findall(text)
    if not barriers:
        raise ValueError("official firmware run produced no barrier record")

    barrier = barriers[-1]
    kind_match = KIND_RE.search(barrier)
    pc_match = PC_RE.search(barrier)
    opcode_match = OPCODE_RE.search(barrier)
    kind = kind_match.group(1) if kind_match else "unknown"
    pc = int(pc_match.group(1), 16) if pc_match else None
    opcode = int(opcode_match.group(1), 16) if opcode_match else None

    if pc in OLD_COLLAPSE_PCS:
        raise ValueError(
            "official firmware ended at the old collapsed PC: "
            f"kind={kind} pc=0x{pc:08x}"
        )

    if kind == "illegal-opcode" and pc is not None and opcode is not None:
        if (opcode, pc) in OLD_TRAPS:
            raise ValueError(
                "barrier still identifies the old float-immediate trap"
            )

    return barrier


def self_test() -> None:
    good = """
Official bootcode live-state probe: bytes=52624
STOCK_BOOTCODE_BARRIER kind=stalled-state cpu-index=4 pc=0x00000544
"""
    next_barrier = """
STOCK_BOOTCODE_BARRIER kind=illegal-opcode opcode=0xdead pc=0x00003000
"""
    old_illegal = """
VideoCore IV: unimplemented opcode 0xc800 at 0x25c4
STOCK_BOOTCODE_BARRIER kind=illegal-opcode opcode=0xc800 pc=0x000025c4
"""
    old_collapse = """
STOCK_BOOTCODE_BARRIER kind=stalled-state cpu-index=4 pc=0x00000014
"""

    assert "pc=0x00000544" in verify_log(good)
    assert "opcode=0xdead" in verify_log(next_barrier)

    for rejected in (old_illegal, old_collapse):
        try:
            verify_log(rejected)
        except ValueError:
            pass
        else:
            raise AssertionError("firmware gate accepted a known regression")

    print("VC4 float-imm6 firmware-log gate self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", nargs="?", help="captured stock-firmware log")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        self_test()
        return 0
    if not args.log:
        parser.error("a firmware log is required unless --self-test is used")

    path = Path(args.log)
    text = path.read_text(encoding="utf-8", errors="replace")
    barrier = verify_log(text)
    print(
        "VC4_FLOAT_IMM6_FIRMWARE_GATE old-traps=absent "
        f"old-zero-pc=absent barrier={barrier}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
