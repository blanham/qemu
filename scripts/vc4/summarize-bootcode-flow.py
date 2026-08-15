#!/usr/bin/env python3
"""Summarize bounded VC4 bootcode retry-flow records.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable

PREFIX = "VC4_BOOT_FLOW "
FIELD_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9-]*)=([^ ]+)")
TARGET_CALLERS = (0x596, 0x5A2, 0x5B0)


@dataclass(frozen=True)
class Record:
    event: str
    seq: int
    generation: int
    caller: int
    pc: int
    length: int
    insn: str
    registers: dict[str, int]


def parse_int(value: str) -> int:
    return int(value, 0)


def parse_record(line: str) -> Record | None:
    offset = line.find(PREFIX)
    if offset < 0:
        return None
    fields = dict(FIELD_RE.findall(line[offset + len(PREFIX):]))
    required = ("event", "seq", "generation", "caller", "pc", "len", "insn")
    if any(field not in fields for field in required):
        raise ValueError(f"malformed boot-flow record: {line.rstrip()}")

    registers: dict[str, int] = {}
    for name, value in fields.items():
        if name == "sr" or name == "sp" or name == "lr" or re.fullmatch(
            r"r\d+", name
        ):
            registers[name] = parse_int(value)

    return Record(
        event=fields["event"],
        seq=parse_int(fields["seq"]),
        generation=parse_int(fields["generation"]),
        caller=parse_int(fields["caller"]),
        pc=parse_int(fields["pc"]),
        length=parse_int(fields["len"]),
        insn=fields["insn"],
        registers=registers,
    )


def compact(values: Iterable[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def format_pcs(pcs: list[int], limit: int = 128) -> str:
    rendered = [f"0x{pc:08x}" for pc in pcs[:limit]]
    if len(pcs) > limit:
        rendered.append(f"...(+{len(pcs) - limit})")
    return "->".join(rendered)


def mmio_values(records: list[Record]) -> list[str]:
    values: set[tuple[str, int]] = set()
    for record in records:
        for register, value in record.registers.items():
            if 0x7C000000 <= value < 0x80000000:
                values.add((register, value))
    return [
        f"{register}=0x{value:08x}"
        for register, value in sorted(values, key=lambda item: (item[1], item[0]))
    ]


def summarize(records: list[Record]) -> list[str]:
    groups: dict[tuple[int, int], list[Record]] = defaultdict(list)
    for record in records:
        groups[(record.caller, record.generation)].append(record)

    lines: list[str] = []
    for key in sorted(groups):
        caller, generation = key
        group = sorted(groups[key], key=lambda record: record.seq)
        events = Counter(record.event for record in group)
        steps = [record for record in group if record.event == "step"]
        pcs = compact(record.pc for record in steps)
        instruction_pairs = compact(
            (record.pc, record.insn, record.length)
            for record in steps
        )
        insns = ",".join(
            f"0x{pc:08x}:{insn}/{length}"
            for pc, insn, length in instruction_pairs[:64]
        )
        if len(instruction_pairs) > 64:
            insns += f",...(+{len(instruction_pairs) - 64})"
        mmio = mmio_values(group)
        lines.append(
            "VC4_BOOT_FLOW_SUMMARY "
            f"caller=0x{caller:08x} generation={generation} "
            f"records={len(group)} "
            f"events=delay-enter:{events['delay-enter']},"
            f"delay-exit:{events['delay-exit']},step:{events['step']} "
            f"pcs={format_pcs(pcs) or 'none'} "
            f"insns={insns or 'none'} "
            f"mmio={','.join(mmio) or 'none'}"
        )
    return lines


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument(
        "--require-all-callers",
        action="store_true",
        help="fail unless each known delay caller appears",
    )
    args = parser.parse_args()

    records: list[Record] = []
    try:
        for line in args.log.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            record = parse_record(line)
            if record is not None:
                records.append(record)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if not records:
        parser.error("no VC4_BOOT_FLOW records found")

    sequences = [record.seq for record in records]
    if sequences != sorted(sequences) or len(sequences) != len(set(sequences)):
        parser.error("boot-flow sequence numbers are not unique and ordered")

    callers = {record.caller for record in records}
    if args.require_all_callers:
        missing = [caller for caller in TARGET_CALLERS if caller not in callers]
        if missing:
            parser.error(
                "missing target callers: "
                + ", ".join(f"0x{caller:x}" for caller in missing)
            )

    summaries = summarize(records)
    for line in summaries:
        print(line)
    print(
        "VC4_BOOT_FLOW_COVERAGE "
        f"records={len(records)} groups={len(summaries)} "
        f"callers={','.join(f'0x{caller:08x}' for caller in sorted(callers))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
