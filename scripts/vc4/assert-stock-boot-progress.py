#!/usr/bin/env python3
"""Assert that a stock bootcode probe crossed the former USB settle delay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PROGRESS_PREFIX = "STOCK_BOOTCODE_PROGRESS "
BARRIER_PREFIX = "STOCK_BOOTCODE_BARRIER "


def load_last_prefixed_json(path: Path, prefix: str) -> dict[str, Any]:
    result: dict[str, Any] | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.startswith(prefix):
            continue
        payload = json.loads(raw[len(prefix):])
        if not isinstance(payload, dict):
            raise RuntimeError(f"{prefix.strip()} payload is not an object")
        result = payload
    if result is None:
        raise RuntimeError(f"{path} contains no {prefix.strip()} record")
    return result


def load_last_barrier(path: Path) -> str | None:
    barrier: str | None = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if raw.startswith(BARRIER_PREFIX):
            barrier = raw[len(BARRIER_PREFIX):]
    return barrier


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probe_log", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    if not args.probe_log.is_file():
        parser.error(f"not a file: {args.probe_log}")

    progress = load_last_prefixed_json(args.probe_log, PROGRESS_PREFIX)
    crossed = progress.get("former_delay_frontier_crossed") is True
    summary = {
        "schema_version": 1,
        "former_delay_frontier_crossed": crossed,
        "reason": progress.get("reason"),
        "delay_generation_count": progress.get("delay_generation_count"),
        "delay_reentered": progress.get("delay_reentered"),
        "left_former_delay_pc": progress.get("left_former_delay_pc"),
        "pc_transitions": progress.get("pc_transitions"),
        "timer_forward_step_count": progress.get("timer_forward_step_count"),
        "barrier": load_last_barrier(args.probe_log),
    }

    rendered = json.dumps(summary, indent=2, sort_keys=True)
    print(rendered)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(rendered + "\n", encoding="utf-8")

    if not crossed:
        raise RuntimeError(
            "stock firmware did not cross the former 0x544 USB settle delay"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
