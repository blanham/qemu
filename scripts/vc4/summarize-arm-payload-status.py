#!/usr/bin/env python3
"""Extract an actionable frontier from VC4_ARM_PAYLOAD_STATUS.json."""

from __future__ import annotations

import argparse
from collections.abc import Iterator
import json
from pathlib import Path
import re
from typing import Any


ILLEGAL_RE = re.compile(
    r"VideoCore IV:\s+(?:unimplemented opcode|illegal instruction)"
    r"(?:\s+0x(?P<opcode>[0-9a-f]+))?\s+at\s+0x(?P<pc>[0-9a-f]+)",
    re.IGNORECASE,
)
CPU_PC_RE = re.compile(r"\bpc\s*=\s*(?:0x)?([0-9a-f]{8,16})\b", re.IGNORECASE)
INTERESTING_KEYS = {
    "classification",
    "frontier",
    "result",
    "source_commit",
    "source_sha",
    "head_sha",
    "workflow_run_id",
}


def walk(value: Any, path: tuple[str, ...] = ()) -> Iterator[tuple[tuple[str, ...], Any]]:
    yield path, value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, path + (str(key),))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, path + (str(index),))


def scalar_fields(data: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for path, value in walk(data):
        if not path or path[-1].lower() not in INTERESTING_KEYS:
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            fields[".".join(path)] = value
    return fields


def text_corpus(data: Any) -> str:
    values = []
    for _, value in walk(data):
        if isinstance(value, str):
            values.append(value)
    return "\n".join(values)


def summarize(data: Any) -> dict[str, Any]:
    text = text_corpus(data)
    sites: list[dict[str, str | None]] = []
    seen_sites: set[tuple[str | None, str]] = set()

    for match in ILLEGAL_RE.finditer(text):
        opcode = match.group("opcode")
        pc = match.group("pc").lower()
        key = (opcode.lower() if opcode else None, pc)
        if key in seen_sites:
            continue
        seen_sites.add(key)
        sites.append(
            {
                "opcode": f"0x{opcode.lower()}" if opcode else None,
                "pc": f"0x{pc}",
            }
        )

    pcs: list[str] = []
    seen_pcs: set[str] = set()
    for match in CPU_PC_RE.finditer(text):
        pc = f"0x{match.group(1).lower()}"
        if pc not in seen_pcs:
            seen_pcs.add(pc)
            pcs.append(pc)

    fields = scalar_fields(data)
    return {
        "schema": 1,
        "fields": fields,
        "illegal_sites": sites,
        "observed_cpu_pcs": pcs,
        "terminal_illegal_site": sites[-1] if sites else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        nargs="?",
        type=Path,
        default=Path("VC4_ARM_PAYLOAD_STATUS.json"),
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=Path("VC4_ARM_PAYLOAD_FRONTIER.json"),
    )
    args = parser.parse_args()

    data = json.loads(args.input.read_text(encoding="utf-8"))
    summary = summarize(data)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
