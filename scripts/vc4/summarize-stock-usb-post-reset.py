#!/usr/bin/env python3
"""Classify stock bootcode progress after the Pi 3B USB root-port reset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


PROGRESS_RE = re.compile(r"^STOCK_BOOTCODE_PROGRESS (\{.*\})$", re.MULTILINE)
BARRIER_RE = re.compile(
    r"^STOCK_BOOTCODE_BARRIER .*?\bpc=0x([0-9a-fA-F]+)\b",
    re.MULTILINE,
)
HPRT0_CONNECTION = 1 << 0
HPRT0_ENABLE = 1 << 2
HPRT0_POWER = 1 << 12


def hprt0_reads(accesses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        entry
        for entry in accesses
        if entry.get("block") == "hreg0"
        and entry.get("operation") == "read"
        and str(entry.get("register", "")).strip() == "HPRT0"
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe_log", type=Path)
    parser.add_argument("frontier", type=Path)
    parser.add_argument("--require-delay-crossed", action="store_true")
    args = parser.parse_args()

    for path in (args.probe_log, args.frontier):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    probe_text = args.probe_log.read_text(encoding="utf-8", errors="replace")
    progress_matches = list(PROGRESS_RE.finditer(probe_text))
    if not progress_matches:
        raise RuntimeError("probe log contains no STOCK_BOOTCODE_PROGRESS record")
    progress = json.loads(progress_matches[-1].group(1))

    barrier_matches = list(BARRIER_RE.finditer(probe_text))
    barrier_pc = (
        int(barrier_matches[-1].group(1), 16) if barrier_matches else None
    )

    frontier = json.loads(args.frontier.read_text(encoding="utf-8"))
    accesses = frontier.get("accesses")
    if not isinstance(accesses, list):
        raise RuntimeError("MMIO frontier has no access list")
    typed_accesses = [entry for entry in accesses if isinstance(entry, dict)]

    root_reads = hprt0_reads(typed_accesses)
    if not root_reads:
        raise RuntimeError("stock firmware performed no HPRT0 reads")
    root_values = [int(entry["value"]) for entry in root_reads]
    final_hprt0 = root_values[-1]

    host_channels = frontier.get("host_channels")
    if not isinstance(host_channels, list):
        raise RuntimeError("MMIO frontier has no host-channel summary")

    delay_crossed = bool(progress.get("former_delay_frontier_crossed"))
    if host_channels:
        next_frontier = "host-channel-transaction"
    elif delay_crossed:
        next_frontier = "pre-host-channel-initialization"
    else:
        next_frontier = "post-reset-delay"

    summary = {
        "schema_version": 1,
        "barrier_pc": (
            f"0x{barrier_pc:08x}" if barrier_pc is not None else None
        ),
        "delay_frontier_crossed": delay_crossed,
        "delay_reason": progress.get("reason"),
        "delay_generation_count": progress.get("delay_generation_count"),
        "delay_generations": progress.get("delay_generations"),
        "pc_transitions": progress.get("pc_transitions"),
        "hprt0_read_count": len(root_reads),
        "final_hprt0": f"0x{final_hprt0:08x}",
        "root_connected": bool(final_hprt0 & HPRT0_CONNECTION),
        "root_enabled": bool(final_hprt0 & HPRT0_ENABLE),
        "root_powered": bool(final_hprt0 & HPRT0_POWER),
        "host_channel_count": len(host_channels),
        "next_frontier": next_frontier,
    }
    print(json.dumps(summary, sort_keys=True))

    if not summary["root_connected"]:
        raise RuntimeError("the Pi 3B onboard USB connection disappeared")
    if not summary["root_enabled"]:
        raise RuntimeError("the DWC2 root port did not complete reset and enable")
    if not summary["root_powered"]:
        raise RuntimeError("the DWC2 root port is not powered")
    if args.require_delay_crossed and not delay_crossed:
        raise RuntimeError(
            "sampling did not prove that stock firmware returned from the "
            "post-reset delay helper"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
