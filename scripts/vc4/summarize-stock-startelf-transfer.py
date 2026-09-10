#!/usr/bin/env python3
"""Summarize the stock VideoCore bootloader's START.ELF SDHOST transfer."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Iterator

ELF_MAGIC_LE = 0x464C457F
SDHOST_BASE = 0x7E202000
SDCMD = SDHOST_BASE + 0x00
SDHSTS = SDHOST_BASE + 0x20
SDDATA = SDHOST_BASE + 0x40
SYSTEM_TIMER_CLO = 0x7E003004
ARM_CONTROL_START = 0x7E00B000
ARM_CONTROL_END = 0x7E00C000
PM_START = 0x7E100000
PM_END = 0x7E102000
TRANSFER_STORE_PC = 0x7690
TRANSFER_DATA_PC = 0x768A
TRANSFER_STATUS_PC = 0x7670
TRANSFER_TIMER_PCS = {0x766A, 0x767C}


@dataclass
class Access:
    index: int
    cpu_index: int | None
    pc: int
    symbol: str
    vaddr: int
    hwaddr: int
    bits: int
    access_type: str
    value: int

    def compact(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "cpu_index": self.cpu_index,
            "pc": f"0x{self.pc:08x}",
            "vaddr": f"0x{self.vaddr:08x}",
            "hwaddr": f"0x{self.hwaddr:08x}",
            "bits": self.bits,
            "type": self.access_type,
            "value": f"0x{self.value:08x}",
        }


@dataclass
class StoreRun:
    first: Access
    last: Access
    count: int = 1
    samples: list[Access] = field(default_factory=list)

    def append(self, access: Access) -> None:
        self.last = access
        self.count += 1
        if len(self.samples) < 16:
            self.samples.append(access)


def parse_int(text: str) -> int:
    return int(text, 0)


def iter_accesses(path: Path) -> Iterator[Access]:
    """Read both the stock seven-column and cpu-indexed eight-column formats."""
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        reader = csv.reader(stream)
        access_index = 0
        for fields in reader:
            if len(fields) == 8:
                cpu, pc, symbol, vaddr, hwaddr, bits, access_type, value = fields
                try:
                    cpu_index: int | None = int(cpu, 0)
                except ValueError:
                    continue
            elif len(fields) == 7:
                pc, symbol, vaddr, hwaddr, bits, access_type, value = fields
                cpu_index = None
            else:
                continue
            try:
                access = Access(
                    index=access_index,
                    cpu_index=cpu_index,
                    pc=parse_int(pc),
                    symbol=symbol,
                    vaddr=parse_int(vaddr),
                    hwaddr=parse_int(hwaddr),
                    bits=int(bits, 0),
                    access_type=access_type,
                    value=parse_int(value),
                )
            except ValueError:
                continue
            access_index += 1
            yield access


def load_temporal(path: Path | None) -> dict[str, Any]:
    if path is None or not path.is_file():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("temporal result is not a JSON object")
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--start-elf", type=Path, required=True)
    parser.add_argument("--temporal", type=Path)
    parser.add_argument("--command-args", type=Path)
    parser.add_argument("--json", type=Path, required=True)
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--source-sha", default="")
    parser.add_argument("--run-id", default="")
    args = parser.parse_args()

    for path in (args.trace, args.start_elf):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    start_elf_bytes = args.start_elf.read_bytes()
    expected_bytes = len(start_elf_bytes)
    expected_blocks = (expected_bytes + 511) // 512
    expected_transfer_bytes = expected_blocks * 512
    expected_words = expected_transfer_bytes // 4
    prefix_word_count = min(16, expected_words)
    expected_prefix = list(struct.unpack_from(
        f"<{prefix_word_count}I", start_elf_bytes, 0
    )) if prefix_word_count else []

    total = 0
    max_pc = 0
    current_run: StoreRun | None = None
    runs: list[StoreRun] = []
    cmd12: list[Access] = []
    cmd18: list[Access] = []
    arm_writes: list[Access] = []
    pm_writes: list[Access] = []
    tail: list[Access] = []

    def finish_run() -> None:
        nonlocal current_run
        if current_run is not None:
            runs.append(current_run)
            current_run = None

    for access in iter_accesses(args.trace):
        total += 1
        max_pc = max(max_pc, access.pc)
        tail.append(access)
        if len(tail) > 64:
            del tail[0]

        if access.access_type == "store" and access.vaddr == SDCMD:
            command = access.value & 0x3F
            if access.value & 0x8000 and command == 12:
                cmd12.append(access)
            if access.value & 0x8000 and command == 18:
                cmd18.append(access)
        if access.access_type == "store" and ARM_CONTROL_START <= access.vaddr < ARM_CONTROL_END:
            if len(arm_writes) < 64:
                arm_writes.append(access)
        if access.access_type == "store" and PM_START <= access.vaddr < PM_END:
            if len(pm_writes) < 64:
                pm_writes.append(access)

        is_transfer_store = (
            access.pc == TRANSFER_STORE_PC
            and access.access_type == "store"
            and access.bits == 32
        )
        if not is_transfer_store:
            continue
        if current_run is None or access.vaddr != current_run.last.vaddr + 4:
            finish_run()
            current_run = StoreRun(access, access, samples=[access])
        else:
            current_run.append(access)
    finish_run()

    elf_runs = [run for run in runs if run.first.value == ELF_MAGIC_LE]
    startelf = max(elf_runs, key=lambda run: run.count) if elf_runs else None
    observed_words = startelf.count if startelf else 0
    observed_bytes = observed_words * 4
    complete = observed_words >= expected_words
    exact_length = observed_words == expected_words
    progress = min(observed_words / expected_words, 1.0) if expected_words else 0.0

    observed_prefix = [access.value for access in startelf.samples] if startelf else []
    prefix_words_compared = min(len(observed_prefix), len(expected_prefix))
    prefix_match = (
        startelf is not None
        and prefix_words_compared > 0
        and observed_prefix[:prefix_words_compared]
            == expected_prefix[:prefix_words_compared]
    )

    command_args: list[str] = []
    if args.command_args and args.command_args.is_file():
        command_args = args.command_args.read_text(encoding="utf-8").splitlines()
    normal_tcg = (
        "-accel" in command_args
        and command_args[command_args.index("-accel") + 1] == "tcg,thread=single"
        and "-icount" not in command_args
        and not any("qtest" in item.lower() for item in command_args)
    ) if command_args else None

    temporal = load_temporal(args.temporal)
    signature_seen = bool(temporal.get("signature_seen"))

    transfer_status_values: set[int] = set()
    transfer_timer_first: int | None = None
    transfer_timer_last: int | None = None
    transfer_data_reads = 0
    post_transfer_access_rows = 0
    post_transfer_max_pc = 0
    post_transfer_samples: list[Access] = []
    if startelf is not None:
        for access in iter_accesses(args.trace):
            if access.index < startelf.first.index:
                continue
            if access.index <= startelf.last.index:
                if access.pc == TRANSFER_STATUS_PC and access.vaddr == SDHSTS:
                    transfer_status_values.add(access.value)
                if access.pc in TRANSFER_TIMER_PCS and access.vaddr == SYSTEM_TIMER_CLO:
                    if transfer_timer_first is None:
                        transfer_timer_first = access.value
                    transfer_timer_last = access.value
                if access.pc == TRANSFER_DATA_PC and access.vaddr == SDDATA:
                    transfer_data_reads += 1
                continue
            post_transfer_access_rows += 1
            post_transfer_max_pc = max(post_transfer_max_pc, access.pc)
            if len(post_transfer_samples) < 64:
                post_transfer_samples.append(access)

    cmd18_before = [
        access for access in cmd18
        if startelf is not None and access.index < startelf.first.index
    ]
    startelf_cmd18 = cmd18_before[-1] if cmd18_before else None
    cmd12_after = [
        access for access in cmd12
        if startelf is not None and access.index > startelf.last.index
    ]

    if signature_seen:
        classification = "stock-firmware-aarch64-handoff-clear"
        next_contract = "run stock Linux from the same unchanged firmware volume"
    elif complete and cmd12_after:
        classification = "stock-startelf-transfer-complete-stop-seen-no-arm-witness"
        next_contract = "trace start.elf execution and the first post-load firmware contract"
    elif complete:
        classification = "stock-startelf-transfer-complete-no-stop-observed"
        next_contract = "extend briefly past the exact file-length read and verify CMD12/completion handling"
    elif startelf:
        classification = "stock-startelf-transfer-progressing"
        next_contract = "extend the bounded run through the remaining START.ELF blocks; do not alter SDHOST"
    else:
        classification = "stock-startelf-transfer-not-observed"
        next_contract = "inspect the last successful SD command before changing a device model"

    status: dict[str, Any] = {
        "schema_version": 3,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "classification": classification,
        "next_contract": next_contract,
        "normal_tcg": normal_tcg,
        "total_access_rows": total,
        "max_observed_pc": f"0x{max_pc:08x}",
        "start_elf": {
            "sha256": hashlib.sha256(start_elf_bytes).hexdigest(),
            "file_bytes": expected_bytes,
            "expected_blocks": expected_blocks,
            "expected_transfer_bytes": expected_transfer_bytes,
            "expected_words": expected_words,
            "observed": startelf is not None,
            "first_access_index": startelf.first.index if startelf else None,
            "last_access_index": startelf.last.index if startelf else None,
            "first_destination": f"0x{startelf.first.vaddr:08x}" if startelf else None,
            "last_destination": f"0x{startelf.last.vaddr:08x}" if startelf else None,
            "first_word": f"0x{startelf.first.value:08x}" if startelf else None,
            "observed_words": observed_words,
            "observed_bytes": observed_bytes,
            "progress_fraction": progress,
            "complete": complete,
            "exact_length": exact_length,
            "prefix_words_compared": prefix_words_compared,
            "prefix_match": prefix_match,
            "samples": [sample.compact() for sample in (startelf.samples if startelf else [])],
            "post_transfer_access_rows": post_transfer_access_rows,
            "post_transfer_max_pc": (
                f"0x{post_transfer_max_pc:08x}" if post_transfer_access_rows else None
            ),
            "post_transfer_samples": [
                access.compact() for access in post_transfer_samples
            ],
        },
        "sdhost": {
            "cmd18_count": len(cmd18),
            "cmd12_count": len(cmd12),
            "startelf_cmd18": startelf_cmd18.compact() if startelf_cmd18 else None,
            "cmd12_after_startelf_count": len(cmd12_after),
            "cmd12_after_startelf_samples": [
                access.compact() for access in cmd12_after[:16]
            ],
            "startelf_data_read_count": transfer_data_reads,
            "startelf_status_values": [
                f"0x{value:08x}" for value in sorted(transfer_status_values)
            ],
            "cmd18_samples": [access.compact() for access in cmd18[-16:]],
            "cmd12_samples": [access.compact() for access in cmd12[-16:]],
        },
        "timer": {
            "first": f"0x{transfer_timer_first:08x}" if transfer_timer_first is not None else None,
            "last": f"0x{transfer_timer_last:08x}" if transfer_timer_last is not None else None,
            "delta_us": (
                (transfer_timer_last - transfer_timer_first) & 0xFFFFFFFF
                if transfer_timer_first is not None and transfer_timer_last is not None
                else None
            ),
        },
        "arm_control_writes": [access.compact() for access in arm_writes],
        "power_writes": [access.compact() for access in pm_writes],
        "temporal_control": {
            "signature_seen": signature_seen,
            "system_timer_delta_us": temporal.get("system_timer_delta_us"),
            "final_pc": temporal.get("final_pc"),
            "unique_pcs": temporal.get("unique_pcs"),
            "classification_hint": temporal.get("classification_hint"),
        },
        "trace_tail": [access.compact() for access in tail],
    }

    args.json.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# VC4 stock START.ELF transfer frontier",
        "",
        f"Classification: **`{classification}`**",
        "",
        f"- Unchanged START.ELF size: `{expected_bytes}` bytes / `{expected_blocks}` blocks",
        f"- Observed transfer: `{observed_words}` / `{expected_words}` words ({progress:.2%})",
        f"- Captured prefix matches START.ELF: `{prefix_match}` ({prefix_words_compared} words)",
        f"- Exact transfer complete: `{complete}`",
        f"- START.ELF CMD18 observed: `{startelf_cmd18 is not None}`",
        f"- CMD12 after this transfer: `{len(cmd12_after)}`",
        f"- START.ELF SDHSTS values: `{', '.join(f'0x{x:08x}' for x in sorted(transfer_status_values))}`",
        f"- Accesses after transfer: `{post_transfer_access_rows}`",
        f"- ARM witness reached: `{signature_seen}`",
        "",
        "The loop at PCs `0x766a`–`0x7690` is the stock bootloader's bounded",
        "128-word-per-block SDHOST reader. PC `0x544` is only a timer-delay helper.",
        "Neither address is a valid target for a PC-specific emulator workaround.",
        "",
        "## Next contract",
        "",
        next_contract,
        "",
    ]
    args.markdown.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(status, indent=2, sort_keys=True))

    if normal_tcg is False:
        raise SystemExit("trace did not preserve the normal single-threaded TCG contract")
    if not startelf:
        raise SystemExit("START.ELF transfer was not observed")
    if not prefix_match:
        raise SystemExit("captured ELF-magic transfer does not match pinned START.ELF")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
