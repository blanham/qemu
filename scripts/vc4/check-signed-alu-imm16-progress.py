#!/usr/bin/env python3
"""Gate stock firmware progress after fixing signed scalar ALU imm16."""

from __future__ import annotations

import argparse
from pathlib import Path
import re


OLD_TRANSFER = re.compile(
    r"VC4_LOW_POP source=0x0000166c target=0x00000000\b"
)
ANY_ZERO_POP = re.compile(
    r"VC4_LOW_POP source=0x[0-9a-fA-F]{8} target=0x00000000\b"
)
CLUSTER_COUNT = re.compile(r"\bcluster-count=(\d+)\b")
BARRIER_PC = re.compile(
    r"STOCK_BOOTCODE_BARRIER[^\n]*\bpc=0x([0-9a-fA-F]+)\b"
)

OLD_CLUSTER_COUNT = 103


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    args = parser.parse_args()

    text = args.log.read_text(encoding="utf-8", errors="replace")
    if OLD_TRANSFER.search(text):
        raise SystemExit(
            "old VC4 0x166c POP-to-PC zero transfer is still present"
        )
    if ANY_ZERO_POP.search(text):
        matches = sorted(set(ANY_ZERO_POP.findall(text)))
        raise SystemExit(
            "stock firmware still performs a zero-target POP-to-PC: "
            + ", ".join(matches)
        )

    counts = [int(value) for value in CLUSTER_COUNT.findall(text)]
    if not counts:
        raise SystemExit("stock firmware log has no translated-cluster count")
    best = max(counts)
    if best <= OLD_CLUSTER_COUNT:
        raise SystemExit(
            "stock firmware did not advance beyond the old frontier: "
            f"cluster-count={best}, required>{OLD_CLUSTER_COUNT}"
        )

    barrier_pcs = [int(value, 16) for value in BARRIER_PC.findall(text)]
    if any(pc in (0, 0x14) for pc in barrier_pcs):
        raise SystemExit(
            "stock firmware ended at the old low-memory halt barrier: "
            + ", ".join(f"0x{pc:08x}" for pc in barrier_pcs)
        )

    low_pops = sorted(
        set(line for line in text.splitlines() if "VC4_LOW_POP " in line)
    )
    print(
        "VC4 signed ALU imm16 stock-firmware gate passed: "
        f"cluster-count={best} old-frontier={OLD_CLUSTER_COUNT} "
        f"barriers={','.join(f'0x{pc:08x}' for pc in barrier_pcs) or 'none'} "
        f"low-pops={len(low_pops)}"
    )
    for line in low_pops:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
