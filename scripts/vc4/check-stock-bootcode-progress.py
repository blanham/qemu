#!/usr/bin/env python3
"""Require machine-readable stock bootcode forward-progress evidence.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

PREFIX = "STOCK_BOOTCODE_PROGRESS "


def load_progress(path: Path) -> dict[str, Any]:
    latest: dict[str, Any] | None = None
    for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw_line.strip()
        if not line.startswith(PREFIX):
            continue
        value = json.loads(line[len(PREFIX):])
        if not isinstance(value, dict):
            raise ValueError("progress payload is not a JSON object")
        latest = value

    if latest is None:
        raise ValueError(f"{path} contains no {PREFIX.strip()} record")
    return latest


def validate_progress(progress: dict[str, Any]) -> None:
    if progress.get("schema_version") != 1:
        raise ValueError(
            f"unsupported progress schema: {progress.get('schema_version')!r}"
        )
    sample_count = progress.get("sample_count")
    if not isinstance(sample_count, int) or sample_count < 2:
        raise ValueError(f"insufficient trace samples: {sample_count!r}")
    if progress.get("former_delay_frontier_crossed") is not True:
        raise ValueError(
            "stock bootcode did not prove progress across the former delay "
            f"frontier: {json.dumps(progress, sort_keys=True)}"
        )

    left = progress.get("left_former_delay_pc") is True
    generations = progress.get("delay_generation_count")
    timer_steps = progress.get("timer_forward_step_count")
    reentry = (
        isinstance(generations, int)
        and generations >= 2
        and isinstance(timer_steps, int)
        and timer_steps > 0
    )
    if not left and not reentry:
        raise ValueError(
            "progress result lacks either a non-frontier PC or a completed "
            f"delay generation: {json.dumps(progress, sort_keys=True)}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path, help="stock bootcode probe log")
    args = parser.parse_args()

    try:
        progress = load_progress(args.log)
        validate_progress(progress)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error(str(exc))

    print(
        "stock bootcode crossed the former delay frontier: "
        f"reason={progress['reason']} "
        f"samples={progress['sample_count']} "
        f"pc-transitions={progress['pc_transitions']} "
        f"delay-generations={progress['delay_generation_count']} "
        f"timer-forward-steps={progress['timer_forward_step_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
