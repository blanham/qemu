#!/usr/bin/env python3
"""Assert that stock firmware observes the Pi 3B onboard USB connection."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

HPRT0_CONNECTION = 1 << 0
HPRT0_POWER = 1 << 12
BARRIER_RE = re.compile(r"STOCK_BOOTCODE_BARRIER .*?\bpc=0x([0-9a-fA-F]+)\b")


def is_hprt0_read(entry: dict[str, Any]) -> bool:
    return (
        entry.get("block") == "hreg0"
        and entry.get("operation") == "read"
        and str(entry.get("register", "")).strip() == "HPRT0"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frontier", type=Path)
    parser.add_argument("--probe-log", type=Path)
    args = parser.parse_args()

    if not args.frontier.is_file():
        parser.error(f"not a file: {args.frontier}")

    data = json.loads(args.frontier.read_text(encoding="utf-8"))
    accesses = data.get("accesses")
    if not isinstance(accesses, list):
        raise RuntimeError("MMIO frontier has no access list")

    reads = [entry for entry in accesses if is_hprt0_read(entry)]
    if not reads:
        raise RuntimeError("stock firmware performed no HPRT0 reads")

    values = Counter(int(entry["value"]) for entry in reads)
    connected = [
        entry for entry in reads
        if int(entry["value"]) & HPRT0_CONNECTION
    ]
    powered = [
        entry for entry in reads
        if int(entry["value"]) & HPRT0_POWER
    ]

    barrier_pc: int | None = None
    if args.probe_log and args.probe_log.is_file():
        for match in BARRIER_RE.finditer(
            args.probe_log.read_text(encoding="utf-8", errors="replace")
        ):
            barrier_pc = int(match.group(1), 16)

    summary = {
        "hprt0_read_count": len(reads),
        "hprt0_values": {
            f"0x{value:08x}": count
            for value, count in sorted(values.items())
        },
        "powered_read_count": len(powered),
        "connected_read_count": len(connected),
        "first_connected_trace_line": (
            int(connected[0]["line"]) if connected else None
        ),
        "barrier_pc": (
            f"0x{barrier_pc:08x}" if barrier_pc is not None else None
        ),
    }
    print(json.dumps(summary, sort_keys=True))

    if not connected:
        raise RuntimeError(
            "the DWC2 root port stayed disconnected; the onboard hub was "
            "not visible to stock firmware"
        )
    if not powered:
        raise RuntimeError("stock firmware never observed the root port powered")
    if set(values) == {HPRT0_POWER}:
        raise RuntimeError(
            "the old disconnected HPRT0=0x00001000 polling frontier remains"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
