#!/usr/bin/env python3
"""Reduce QEMU VC4 in_asm logs around known low bootcode frontiers."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any


DEFAULT_TARGETS = (0x98, 0x544, 0xF6C, 0x27D8)
ADDRESS_RE = re.compile(r"0x([0-9a-fA-F]+):")


def parse_blocks(text: str) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    current: list[str] = []

    def finish() -> None:
        if not current:
            return
        addresses = [
            int(match.group(1), 16)
            for line in current
            for match in [ADDRESS_RE.search(line)]
            if match
        ]
        if addresses:
            blocks.append({
                "start": min(addresses),
                "end": max(addresses),
                "addresses": addresses,
                "text": current.copy(),
            })
        current.clear()

    for line in text.splitlines():
        if line.startswith("IN:") or line.startswith("----------------"):
            finish()
        if current or line.startswith("IN:") or ADDRESS_RE.search(line):
            current.append(line)
    finish()
    return blocks


def target_context(blocks: list[dict[str, Any]], target: int) -> dict[str, Any]:
    exact = [block for block in blocks if target in block["addresses"]]
    containing = [
        block for block in blocks
        if block["start"] <= target <= block["end"]
    ]
    ordered = sorted(blocks, key=lambda block: block["start"])
    previous = None
    following = None
    for block in ordered:
        if block["end"] < target:
            previous = block
        elif block["start"] > target:
            following = block
            break
    selected = exact or containing
    return {
        "target": f"0x{target:08x}",
        "exact": bool(exact),
        "blocks": [block["text"] for block in selected[:8]],
        "previous": previous["text"] if previous else None,
        "following": following["text"] if following else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("logs", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument(
        "--target",
        action="append",
        type=lambda value: int(value, 0),
        dest="targets",
    )
    args = parser.parse_args()
    targets = tuple(args.targets or DEFAULT_TARGETS)

    reports = []
    global_starts: Counter[int] = Counter()
    for path in args.logs:
        text = path.read_text(encoding="utf-8", errors="replace")
        blocks = parse_blocks(text)
        global_starts.update(block["start"] for block in blocks)
        reports.append({
            "path": str(path),
            "block_count": len(blocks),
            "targets": [target_context(blocks, target) for target in targets],
        })

    result = {
        "schema_version": 1,
        "targets": [f"0x{target:08x}" for target in targets],
        "logs": reports,
        "most_common_translated_starts": [
            {"pc": f"0x{pc:08x}", "logs": count}
            for pc, count in global_starts.most_common(100)
        ],
    }
    args.json.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# VC4 low bootcode PC map",
        "",
        "The blocks below come from QEMU's VC4 disassembler while executing "
        "the pinned stock `bootcode.bin`. They identify code, not merely "
        "sampled PCs.",
        "",
    ]
    for report in reports:
        lines.extend((f"## `{report['path']}`", ""))
        for context in report["targets"]:
            lines.extend((f"### {context['target']}", ""))
            blocks = context["blocks"]
            if not blocks:
                lines.append("Target was not translated in this run.")
                lines.append("")
                continue
            for block in blocks:
                lines.extend(("```text", *block, "```", ""))
    args.markdown.write_text("\n".join(lines), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
