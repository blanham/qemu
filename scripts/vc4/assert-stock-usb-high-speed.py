#!/usr/bin/env python3
"""Assert that stock firmware reaches high-speed DWC2 host channels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

HPRT0_CONNECTION = 1 << 0
HPRT0_SPEED_MASK = 3 << 17
HPRT0_SPEED_HIGH = 0 << 17
HCCHAR_CHENA = 1 << 31
BARRIER_RE = re.compile(
    r"STOCK_BOOTCODE_BARRIER .*?\bpc=0x([0-9a-fA-F]+)\b"
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("frontier", type=Path)
    parser.add_argument("--probe-log", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    if not args.frontier.is_file():
        parser.error(f"not a file: {args.frontier}")

    data = json.loads(args.frontier.read_text(encoding="utf-8"))
    accesses = data.get("accesses")
    if not isinstance(accesses, list):
        raise RuntimeError("MMIO frontier has no access list")

    hprt0_reads = [
        entry for entry in accesses
        if entry.get("block") == "hreg0"
        and entry.get("operation") == "read"
        and str(entry.get("register", "")).strip() == "HPRT0"
    ]
    connected = [
        entry for entry in hprt0_reads
        if int(entry.get("value", 0)) & HPRT0_CONNECTION
    ]
    high_speed = [
        entry for entry in connected
        if (int(entry.get("value", 0)) & HPRT0_SPEED_MASK)
        == HPRT0_SPEED_HIGH
    ]
    channel_enables = [
        entry for entry in accesses
        if entry.get("block") == "hreg1"
        and entry.get("operation") == "write"
        and str(entry.get("register", "")).strip() == "HCCHAR"
        and int(entry.get("value", 0)) & HCCHAR_CHENA
    ]

    barrier_pc: int | None = None
    if args.probe_log and args.probe_log.is_file():
        for match in BARRIER_RE.finditer(
            args.probe_log.read_text(encoding="utf-8", errors="replace")
        ):
            barrier_pc = int(match.group(1), 16)

    summary: dict[str, Any] = {
        "schema_version": 1,
        "hprt0_read_count": len(hprt0_reads),
        "connected_read_count": len(connected),
        "high_speed_connected_read_count": len(high_speed),
        "connected_hprt0_values": sorted({
            f"0x{int(entry['value']):08x}" for entry in connected
        }),
        "host_channel_enable_count": len(channel_enables),
        "enabled_channels": sorted({
            int(entry.get("channel", -1)) for entry in channel_enables
        }),
        "barrier_pc": (
            f"0x{barrier_pc:08x}" if barrier_pc is not None else None
        ),
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")

    if not high_speed:
        raise RuntimeError(
            "stock firmware never observed the onboard hub at high speed"
        )
    if not channel_enables:
        raise RuntimeError(
            "stock firmware still did not initialize any DWC2 host channel"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
