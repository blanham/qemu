#!/usr/bin/env python3
"""Summarize the bootcode DWC2, DBUS, and power-manager MMIO frontier."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


READ_RE = re.compile(
    r"usb_dwc2_(glbreg|hreg0|pcgreg)_read.*?"
    r"0x([0-9a-fA-F]+)\s+(\S+)\s+val 0x([0-9a-fA-F]+)"
)
WRITE_RE = re.compile(
    r"usb_dwc2_(glbreg|hreg0|pcgreg)_write.*?"
    r"0x([0-9a-fA-F]+)\s+(\S+)\s+"
    r"val 0x([0-9a-fA-F]+)\s+old 0x([0-9a-fA-F]+)\s+"
    r"result 0x([0-9a-fA-F]+)"
)
HREG1_READ_RE = re.compile(
    r"usb_dwc2_hreg1_read.*?"
    r"0x([0-9a-fA-F]+)\s+([A-Za-z0-9<>]+)\s*([0-9]+)\s+"
    r"val 0x([0-9a-fA-F]+)"
)
HREG1_WRITE_RE = re.compile(
    r"usb_dwc2_hreg1_write.*?"
    r"0x([0-9a-fA-F]+)\s+([A-Za-z0-9<>]+)\s*([0-9]+)\s+"
    r"val 0x([0-9a-fA-F]+)\s+old 0x([0-9a-fA-F]+)\s+"
    r"result 0x([0-9a-fA-F]+)"
)
DIAGNOSTIC_MARKERS = (
    "bcm2835-dbus:",
    "bcm2835_powermgt_",
    "dwc2_glbreg_write:",
)


def parse(path: Path) -> dict[str, Any]:
    accesses: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()

    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
    ):
        hreg1_write = HREG1_WRITE_RE.search(raw)
        if hreg1_write:
            offset, register, channel, value, old, result = hreg1_write.groups()
            entry = {
                "line": line_number,
                "operation": "write",
                "block": "hreg1",
                "offset": int(offset, 16),
                "register": register.strip(),
                "channel": int(channel),
                "value": int(value, 16),
                "old": int(old, 16),
                "result": int(result, 16),
            }
            accesses.append(entry)
            counts[f"hreg1:write:{entry['register']}"] += 1
            continue

        hreg1_read = HREG1_READ_RE.search(raw)
        if hreg1_read:
            offset, register, channel, value = hreg1_read.groups()
            entry = {
                "line": line_number,
                "operation": "read",
                "block": "hreg1",
                "offset": int(offset, 16),
                "register": register.strip(),
                "channel": int(channel),
                "value": int(value, 16),
            }
            accesses.append(entry)
            counts[f"hreg1:read:{entry['register']}"] += 1
            continue

        write = WRITE_RE.search(raw)
        if write:
            block, offset, register, value, old, result = write.groups()
            entry = {
                "line": line_number,
                "operation": "write",
                "block": block,
                "offset": int(offset, 16),
                "register": register.strip(),
                "value": int(value, 16),
                "old": int(old, 16),
                "result": int(result, 16),
            }
            accesses.append(entry)
            counts[f"{block}:write:{entry['register']}"] += 1
            continue

        read = READ_RE.search(raw)
        if read:
            block, offset, register, value = read.groups()
            entry = {
                "line": line_number,
                "operation": "read",
                "block": block,
                "offset": int(offset, 16),
                "register": register.strip(),
                "value": int(value, 16),
            }
            accesses.append(entry)
            counts[f"{block}:read:{entry['register']}"] += 1
            continue

        if any(marker in raw for marker in DIAGNOSTIC_MARKERS):
            diagnostics.append({"line": line_number, "text": raw.strip()})

    grstctl = [
        entry
        for entry in accesses
        if entry["block"] == "glbreg" and entry["offset"] == 0x10
    ]
    pcg = [entry for entry in accesses if entry["block"] == "pcgreg"]
    host_channels = [entry for entry in accesses if entry["block"] == "hreg1"]
    reset_writes = [
        entry
        for entry in grstctl
        if entry["operation"] == "write"
        and entry["value"] & ((1 << 0) | (1 << 1) | (1 << 4) | (1 << 5))
    ]

    return {
        "schema_version": 2,
        "access_count": len(accesses),
        "counts": dict(sorted(counts.items())),
        "grstctl": grstctl,
        "reset_writes": reset_writes,
        "pcg": pcg,
        "host_channels": host_channels,
        "diagnostics": diagnostics,
        "accesses": accesses,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    if not args.log.is_file():
        parser.error(f"not a file: {args.log}")

    result = parse(args.log)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n")
    print(rendered)

    if not result["grstctl"]:
        print("No GRSTCTL accesses were captured.", flush=True)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
