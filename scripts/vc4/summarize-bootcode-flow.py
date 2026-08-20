#!/usr/bin/env python3
"""Summarize bounded VC4 bootcode retry-flow records.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
import re

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


def record_payloads(line: str) -> Iterator[str]:
    """Yield every record embedded in one raw or flattened diagnostic line."""
    starts = [match.start() for match in re.finditer(re.escape(PREFIX), line)]
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(line)
        payload = line[start + len(PREFIX):end]
        # The live-state probe joins its diagnostic tail with `` | ``.  Trim
        # any trailing non-record diagnostics from the final embedded record.
        payload = payload.split(" | ", 1)[0].strip()
        if payload:
            yield payload


def parse_payload(payload: str) -> Record:
    fields = dict(FIELD_RE.findall(payload))
    required = ("event", "seq", "generation", "caller", "pc", "len", "insn")
    if any(field not in fields for field in required):
        raise ValueError(f"malformed boot-flow record: {payload}")

    registers: dict[str, int] = {}
    for name, value in fields.items():
        if name in {"sr", "sp", "lr"} or re.fullmatch(r"r\d+", name):
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


def parse_records(line: str) -> list[Record]:
    return [parse_payload(payload) for payload in record_payloads(line)]


def normalize_records(records: Iterable[Record]) -> list[Record]:
    """Deduplicate repeated diagnostic tails while rejecting contradictions."""
    by_sequence: dict[int, Record] = {}
    for record in records:
        previous = by_sequence.get(record.seq)
        if previous is not None and previous != record:
            raise ValueError(
                f"conflicting boot-flow records for sequence {record.seq}"
            )
        by_sequence[record.seq] = record
    return [by_sequence[sequence] for sequence in sorted(by_sequence)]


def compact(values: Iterable[object]) -> list[object]:
    result: list[object] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def format_record(record: Record) -> str:
    fields = [
        f"event={record.event}",
        f"seq={record.seq}",
        f"generation={record.generation}",
        f"caller=0x{record.caller:08x}",
        f"pc=0x{record.pc:08x}",
        f"insn={record.insn}",
        f"len={record.length}",
    ]
    fields.extend(
        f"{name}=0x{value:08x}"
        for name, value in record.registers.items()
    )
    return PREFIX + " ".join(fields)


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
        pcs = [int(pc) for pc in compact(record.pc for record in steps)]
        instruction_pairs = [
            tuple(value)
            for value in compact(
                (record.pc, record.insn, record.length)
                for record in steps
            )
        ]
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


def selftest() -> None:
    sample = (
        "prefix VC4_BOOT_FLOW event=delay-enter seq=0 generation=1 "
        "caller=0x00000596 pc=0x00000540 insn=1234:0000:0000 len=2 "
        "sr=0x00000006 r0=0x00000001 lr=0x00000596"
        " | VC4_BOOT_FLOW event=step seq=1 generation=1 "
        "caller=0x00000596 pc=0x00000596 insn=5678:0000:0000 len=2 "
        "sr=0x00000006 r0=0x00000002 lr=0x00000596 | trailing"
    )
    records = parse_records(sample)
    assert len(records) == 2
    assert [record.seq for record in records] == [0, 1]
    assert all(record.caller == 0x596 for record in records)
    assert records[1].pc == 0x596
    assert normalize_records(records + records) == records
    assert format_record(records[0]).startswith(PREFIX + "event=delay-enter")
    print("bootcode flow summarizer self-test passed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", nargs="?", type=Path)
    parser.add_argument(
        "--require-all-callers",
        action="store_true",
        help="fail unless each known delay caller appears",
    )
    parser.add_argument(
        "--raw-output",
        type=Path,
        help="write normalized records one per line",
    )
    parser.add_argument("--selftest", action="store_true")
    args = parser.parse_args()

    if args.selftest:
        selftest()
        if args.log is None:
            return 0
    if args.log is None:
        parser.error("a log path is required unless --selftest is used alone")

    parsed: list[Record] = []
    try:
        for line in args.log.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines():
            parsed.extend(parse_records(line))
        records = normalize_records(parsed)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if not records:
        parser.error("no VC4_BOOT_FLOW records found")

    sequences = [record.seq for record in records]
    expected = list(range(sequences[0], sequences[-1] + 1))
    if sequences != expected:
        parser.error(
            "boot-flow sequence contains gaps: "
            f"first={sequences[0]} last={sequences[-1]} "
            f"records={len(sequences)}"
        )

    callers = {record.caller for record in records}
    if args.require_all_callers:
        missing = [caller for caller in TARGET_CALLERS if caller not in callers]
        if missing:
            parser.error(
                "missing target callers: "
                + ", ".join(f"0x{caller:x}" for caller in missing)
            )

    if args.raw_output is not None:
        try:
            args.raw_output.parent.mkdir(parents=True, exist_ok=True)
            args.raw_output.write_text(
                "\n".join(format_record(record) for record in records) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            parser.error(f"could not write {args.raw_output}: {exc}")

    summaries = summarize(records)
    for line in summaries:
        print(line)
    print(
        "VC4_BOOT_FLOW_COVERAGE "
        f"records={len(records)} groups={len(summaries)} "
        f"first-seq={sequences[0]} last-seq={sequences[-1]} "
        f"callers={','.join(f'0x{caller:08x}' for caller in sorted(callers))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
