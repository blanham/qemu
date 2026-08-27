#!/usr/bin/env python3
"""Validate WD40 bounded virtual and physical guest-memory reads."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MAX_READ = 1024 * 1024
EXPECTED_DATA = "41424344"


@dataclass(frozen=True)
class TargetCase:
    binary: str
    arguments: tuple[str, ...]
    target: str
    address: int
    virtual: bool
    test_unmapped: bool = False


TARGETS = (
    TargetCase(
        binary="qemu-system-x86_64",
        arguments=(
            "-machine", "q35,accel=tcg",
            "-cpu", "max",
            "-smp", "2",
            "-m", "128M",
        ),
        target="x86_64",
        address=0x10000,
        virtual=True,
        test_unmapped=True,
    ),
    TargetCase(
        binary="qemu-system-aarch64",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-cpu", "max",
            "-smp", "2",
            "-m", "128M",
        ),
        target="aarch64",
        address=0x40010000,
        virtual=True,
    ),
    TargetCase(
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
        target="m68k",
        address=0x10000,
        virtual=True,
    ),
    TargetCase(
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        target="ppc",
        address=0x10000,
        virtual=False,
    ),
)


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def need(path: str, *markers: str) -> None:
    text = source(path)
    missing = [marker for marker in markers if marker not in text]
    if missing:
        raise SystemExit(f"{path}: missing {missing!r}")


def exactly_once(path: str, marker: str) -> None:
    count = source(path).count(marker)
    if count != 1:
        raise SystemExit(f"{path}: expected one {marker!r}, found {count}")


def qapi_block() -> str:
    text = source("qapi/machine.json")
    start_marker = "##\n# @WD40MemorySpace:\n"
    end_marker = "##\n# @memsave:\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit("qapi/machine.json: could not isolate memory-read block")
    return text[start:end]


def implementation_block() -> str:
    text = source("system/physmem-qmp-cmds.c")
    start_marker = "#define WD40_MEMORY_READ_MAX"
    end_marker = "void qmp_memsave(uint64_t addr"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: could not isolate memory-read block"
        )
    return text[start:end]


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/machine.json: WD40 memory documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_static() -> None:
    need(
        "qapi/machine.json",
        "'enum': 'WD40MemorySpace'",
        "'struct': 'WD40MemoryRead'",
        "'command': 'x-wd40-read-memory'",
        "'features': [ 'unstable' ]",
        "'*cpu-index': 'int'",
    )
    need(
        "system/physmem-qmp-cmds.c",
        '#include "system/address-spaces.h"',
        '#include "system/hw_accel.h"',
        '#include "system/memory.h"',
        "#define WD40_MEMORY_READ_MAX (1024U * 1024U)",
        "qmp_x_wd40_read_memory",
        "cpu_synchronize_state(cpu)",
        "cpu_memory_rw_debug(cpu, address, buffer",
        "address_space_read(&address_space_memory, address",
        "transaction != MEMTX_OK",
        "address > UINT64_MAX - (size - 1)",
        "migration_guest_ram_loading()",
        "cpu-index is only valid for virtual memory",
        "wd40_memory_bytes_to_hex",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Bounded guest-memory reads",
        "x-wd40-read-memory",
        "debugger translation path",
        "memory-transaction",
        "side-effect-free RAM snapshots",
        "one MiB",
    )

    exactly_once("qapi/machine.json", "'enum': 'WD40MemorySpace'")
    exactly_once("qapi/machine.json", "'struct': 'WD40MemoryRead'")
    exactly_once("qapi/machine.json", "'command': 'x-wd40-read-memory'")
    exactly_once(
        "system/physmem-qmp-cmds.c",
        "qmp_x_wd40_read_memory",
    )
    exactly_once(
        "docs/devel/wd40-monitor-v2.rst",
        "Bounded guest-memory reads",
    )

    implementation = implementation_block()
    forbidden = (
        "physical_memory_read(",
        "qmp_memsave(",
        "qmp_pmemsave(",
        "human_monitor_command",
        "cpu_dump_state",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: memory service uses a legacy "
            f"or textual path: {present!r}"
        )

    bounds = implementation.find(
        "if (size == 0 || size > WD40_MEMORY_READ_MAX)"
    )
    allocation = implementation.find("buffer = g_malloc((gsize)size)")
    if bounds < 0 or allocation < 0 or bounds > allocation:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: allocation precedes size bounds"
        )

    synchronize = implementation.find("cpu_synchronize_state(cpu)")
    virtual_read = implementation.find("cpu_memory_rw_debug(cpu, address")
    if synchronize < 0 or virtual_read < 0 or synchronize > virtual_read:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: virtual read is not synchronized"
        )

    physical_read = implementation.find(
        "address_space_read(&address_space_memory, address"
    )
    transaction_check = implementation.find("transaction != MEMTX_OK")
    if (
        physical_read < 0
        or transaction_check < 0
        or physical_read > transaction_check
    ):
        raise SystemExit(
            "system/physmem-qmp-cmds.c: physical transaction errors "
            "are not preserved"
        )

    validate_qapi_doc_width()


def loader_arguments(address: int) -> tuple[str, ...]:
    result: list[str] = []
    for offset, value in enumerate((0x41, 0x42, 0x43, 0x44)):
        result.extend(
            (
                "-device",
                (
                    f"loader,id=wd40-byte{offset},"
                    f"addr=0x{address + offset:x},"
                    f"data=0x{value:x},data-len=1"
                ),
            )
        )
    return tuple(result)


def run_qmp(
    binary: Path,
    arguments: tuple[str, ...],
    messages: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    command = [
        str(binary),
        *arguments,
        "-display", "none",
        "-serial", "none",
        "-monitor", "none",
        "-nodefaults",
        "-S",
        "-qmp", "stdio",
    ]
    run = subprocess.run(
        command,
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=90,
        check=False,
    )

    replies: dict[str, dict[str, Any]] = {}
    for line in run.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        identifier = item.get("id")
        if isinstance(identifier, str):
            replies[identifier] = item

    required = {
        message["id"]
        for message in messages
        if "id" in message and message["id"] != "quit"
    }
    missing = required.difference(replies)
    if missing:
        raise SystemExit(
            f"{binary.name}: missing QMP replies {sorted(missing)!r}; "
            f"rc={run.returncode}; command={command!r}; "
            f"stdout={run.stdout!r}; stderr={run.stderr!r}"
        )
    return replies


def require_return(
    replies: dict[str, dict[str, Any]], identifier: str
) -> dict[str, Any]:
    reply = replies[identifier]
    result = reply.get("return")
    if not isinstance(result, dict):
        raise SystemExit(f"{identifier}: malformed memory reply: {reply!r}")
    return result


def require_error(
    replies: dict[str, dict[str, Any]],
    identifier: str,
    *fragments: str,
) -> None:
    error = replies[identifier].get("error")
    description = error.get("desc") if isinstance(error, dict) else None
    missing = (
        list(fragments)
        if not isinstance(description, str)
        else [fragment for fragment in fragments if fragment not in description]
    )
    if not isinstance(error, dict) or missing:
        raise SystemExit(
            f"{identifier}: expected error containing {fragments!r}: "
            f"{replies[identifier]!r}"
        )


def validate_result(
    result: dict[str, Any],
    *,
    space: str,
    address: int,
    cpu_index: int | None,
) -> None:
    expected: dict[str, Any] = {
        "space": space,
        "address": address,
        "bytes": 4,
        "data": EXPECTED_DATA,
    }
    if cpu_index is not None:
        expected["cpu-index"] = cpu_index

    if result != expected:
        raise SystemExit(f"memory result mismatch: {result!r} != {expected!r}")


def validate_target(build: Path, case: TargetCase) -> None:
    print(
        f"WD40 memory read: starting {case.target} runtime checks",
        flush=True,
    )
    binary = build / case.binary
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")

    arguments = (*case.arguments, *loader_arguments(case.address))
    messages: list[dict[str, Any]] = [
        {"execute": "qmp_capabilities", "id": "cap"},
        {
            "execute": "x-wd40-read-memory",
            "arguments": {
                "space": "physical",
                "address": case.address,
                "size": 4,
            },
            "id": "physical",
        },
        {
            "execute": "x-wd40-read-memory",
            "arguments": {
                "space": "physical",
                "address": case.address,
                "size": 4,
                "cpu-index": 0,
            },
            "id": "physical-cpu",
        },
        {
            "execute": "x-wd40-read-memory",
            "arguments": {
                "space": "physical",
                "address": case.address,
                "size": 0,
            },
            "id": "zero",
        },
        {
            "execute": "x-wd40-read-memory",
            "arguments": {
                "space": "physical",
                "address": case.address,
                "size": MAX_READ + 1,
            },
            "id": "oversize",
        },
        {
            "execute": "x-wd40-read-memory",
            "arguments": {
                "space": "physical",
                "address": (1 << 64) - 1,
                "size": 2,
            },
            "id": "overflow",
        },
    ]

    if case.virtual:
        messages.extend(
            (
                {
                    "execute": "x-wd40-read-memory",
                    "arguments": {
                        "space": "virtual",
                        "address": case.address,
                        "size": 4,
                    },
                    "id": "virtual-default",
                },
                {
                    "execute": "x-wd40-read-memory",
                    "arguments": {
                        "space": "virtual",
                        "address": case.address,
                        "size": 4,
                        "cpu-index": 0,
                    },
                    "id": "virtual-cpu0",
                },
                {
                    "execute": "x-wd40-read-memory",
                    "arguments": {
                        "space": "virtual",
                        "address": case.address,
                        "size": 4,
                        "cpu-index": 9999,
                    },
                    "id": "virtual-invalid-cpu",
                },
            )
        )

    if case.test_unmapped:
        messages.append(
            {
                "execute": "x-wd40-read-memory",
                "arguments": {
                    "space": "physical",
                    "address": 0xFFFFFFFFFFFFF000,
                    "size": 4,
                },
                "id": "unmapped",
            }
        )

    messages.append({"execute": "quit", "id": "quit"})
    replies = run_qmp(binary.resolve(), arguments, messages)

    validate_result(
        require_return(replies, "physical"),
        space="physical",
        address=case.address,
        cpu_index=None,
    )
    require_error(replies, "physical-cpu", "only valid for virtual")
    require_error(replies, "zero", "between 1 and")
    require_error(replies, "oversize", "between 1 and")
    require_error(replies, "overflow", "wraps past UINT64_MAX")

    if case.virtual:
        validate_result(
            require_return(replies, "virtual-default"),
            space="virtual",
            address=case.address,
            cpu_index=0,
        )
        validate_result(
            require_return(replies, "virtual-cpu0"),
            space="virtual",
            address=case.address,
            cpu_index=0,
        )
        require_error(
            replies,
            "virtual-invalid-cpu",
            "cpu-index",
            "CPU number",
        )

    if case.test_unmapped:
        require_error(replies, "unmapped", "Physical memory read failed")

    print(f"WD40 memory read: {case.target} runtime checks passed")


def select_targets(names: list[str]) -> tuple[TargetCase, ...]:
    if not names:
        return TARGETS

    available = {case.target: case for case in TARGETS}
    unknown = sorted(set(names).difference(available))
    if unknown:
        raise SystemExit(
            f"unknown target(s) {unknown!r}; choose from "
            f"{sorted(available)!r}"
        )
    return tuple(available[name] for name in names)


def validate_runtime(build: Path, target_names: list[str]) -> None:
    for case in select_targets(target_names):
        validate_target(build, case)


validate_static()
if len(sys.argv) >= 2:
    validate_runtime(Path(sys.argv[1]).resolve(), sys.argv[2:])
