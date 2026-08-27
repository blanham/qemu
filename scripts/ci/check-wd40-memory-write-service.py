#!/usr/bin/env python3
"""Validate WD40 bounded virtual and physical guest-memory writes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MAX_WRITE = 1024 * 1024
INITIAL_DATA = "41424344"
PHYSICAL_DATA = "deadbeef"
VIRTUAL_DATA = "10203040"
CPU0_DATA = "55667788"
CPU1_DATA = "a1b2c3d4"


@dataclass(frozen=True)
class TargetCase:
    binary: str
    arguments: tuple[str, ...]
    target: str
    address: int
    virtual: bool
    second_cpu: bool = False
    test_unmapped: bool = False
    test_oversize: bool = False


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
        second_cpu=True,
        test_unmapped=True,
        test_oversize=True,
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
        address=0x41000000,
        virtual=True,
        second_cpu=True,
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
    start_marker = "##\n# @WD40MemoryWrite:\n"
    end_marker = "##\n# @WD40MemoryTransactionAttributes:\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "qapi/machine.json: could not isolate memory-write block"
        )
    return text[start:end]


def implementation_block() -> str:
    text = source("system/physmem-qmp-cmds.c")
    start_marker = "#define WD40_MEMORY_WRITE_MAX"
    end_marker = "void qmp_memsave(uint64_t addr"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: could not isolate "
            "memory-write block"
        )
    return text[start:end]


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/machine.json: WD40 write documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_static() -> None:
    need(
        "qapi/machine.json",
        "Guest address space used by a WD40 memory access.",
        "@physical: access the system physical address space directly",
        "'struct': 'WD40MemoryWrite'",
        "'command': 'x-wd40-write-memory'",
        "'returns': 'WD40MemoryWrite'",
        "'features': [ 'unstable' ]",
        "'data': 'str'",
        "'*cpu-index': 'int'",
    )
    need(
        "system/physmem-qmp-cmds.c",
        "#define WD40_MEMORY_WRITE_MAX (1024U * 1024U)",
        "wd40_memory_hex_digit",
        "wd40_memory_hex_to_bytes",
        "qmp_x_wd40_write_memory",
        "strlen(data)",
        "hex_length == 0",
        "hex_length & 1",
        "hex_length / 2 > WD40_MEMORY_WRITE_MAX",
        "cpu_synchronize_state(cpu)",
        "cpu_memory_rw_debug(cpu, address, buffer, size, true)",
        "address_space_write(&address_space_memory, address",
        "transaction != MEMTX_OK",
        "address > UINT64_MAX - (uint64_t)(size - 1)",
        "migration_guest_ram_loading()",
        "cpu-index is only valid for virtual memory",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Bounded guest-memory writes",
        "x-wd40-write-memory",
        "non-hexadecimal",
        "memory-transaction failures",
        "virtual debug writes can modify ROM",
        "read-modify-write",
    )

    exactly_once("qapi/machine.json", "'struct': 'WD40MemoryWrite'")
    exactly_once("qapi/machine.json", "'command': 'x-wd40-write-memory'")
    exactly_once(
        "system/physmem-qmp-cmds.c",
        "qmp_x_wd40_write_memory",
    )
    exactly_once(
        "docs/devel/wd40-monitor-v2.rst",
        "Bounded guest-memory writes",
    )

    implementation = implementation_block()
    forbidden = (
        "physical_memory_write(",
        "qmp_memsave(",
        "qmp_pmemsave(",
        "human_monitor_command",
        "cpu_dump_state",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: memory write uses a legacy "
            f"or textual path: {present!r}"
        )

    empty = implementation.find("if (hex_length == 0)")
    odd = implementation.find("if (hex_length & 1)")
    maximum = implementation.find(
        "if (hex_length / 2 > WD40_MEMORY_WRITE_MAX)"
    )
    allocation = implementation.find("bytes = g_malloc(hex_length / 2)")
    if (
        empty < 0
        or odd < 0
        or maximum < 0
        or allocation < 0
        or not (empty < odd < maximum < allocation)
    ):
        raise SystemExit(
            "system/physmem-qmp-cmds.c: hex validation must precede "
            "the write buffer allocation"
        )

    decode = implementation.find(
        "buffer = wd40_memory_hex_to_bytes(data, &size, errp)"
    )
    wrap = implementation.find(
        "if (address > UINT64_MAX - (uint64_t)(size - 1))"
    )
    migration = implementation.find("if (migration_guest_ram_loading())")
    virtual_write = implementation.find(
        "cpu_memory_rw_debug(cpu, address, buffer, size, true)"
    )
    physical_write = implementation.find(
        "address_space_write(&address_space_memory, address"
    )
    if (
        decode < 0
        or wrap < 0
        or migration < 0
        or virtual_write < 0
        or physical_write < 0
        or not (decode < wrap < migration < virtual_write)
        or not (migration < physical_write)
    ):
        raise SystemExit(
            "system/physmem-qmp-cmds.c: write validation order is unsafe"
        )

    synchronize = implementation.find("cpu_synchronize_state(cpu)")
    if synchronize < 0 or synchronize > virtual_write:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: virtual write is not synchronized"
        )

    transaction_check = implementation.find("transaction != MEMTX_OK")
    if transaction_check < 0 or physical_write > transaction_check:
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
                    f"loader,id=wd40-write-byte{offset},"
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
        timeout=120,
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
        else [
            fragment
            for fragment in fragments
            if fragment not in description
        ]
    )
    if not isinstance(error, dict) or missing:
        raise SystemExit(
            f"{identifier}: expected error containing {fragments!r}: "
            f"{replies[identifier]!r}"
        )


def validate_write(
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
    }
    if cpu_index is not None:
        expected["cpu-index"] = cpu_index
    if result != expected:
        raise SystemExit(f"memory write mismatch: {result!r} != {expected!r}")


def validate_read(
    result: dict[str, Any],
    *,
    space: str,
    address: int,
    data: str,
    cpu_index: int | None,
) -> None:
    expected: dict[str, Any] = {
        "space": space,
        "address": address,
        "bytes": 4,
        "data": data,
    }
    if cpu_index is not None:
        expected["cpu-index"] = cpu_index
    if result != expected:
        raise SystemExit(f"memory read mismatch: {result!r} != {expected!r}")


def read_message(
    identifier: str,
    *,
    space: str,
    address: int,
    cpu_index: int | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "space": space,
        "address": address,
        "size": 4,
    }
    if cpu_index is not None:
        arguments["cpu-index"] = cpu_index
    return {
        "execute": "x-wd40-read-memory",
        "arguments": arguments,
        "id": identifier,
    }


def write_message(
    identifier: str,
    *,
    space: str,
    address: int,
    data: str,
    cpu_index: int | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "space": space,
        "address": address,
        "data": data,
    }
    if cpu_index is not None:
        arguments["cpu-index"] = cpu_index
    return {
        "execute": "x-wd40-write-memory",
        "arguments": arguments,
        "id": identifier,
    }


def validate_target(build: Path, case: TargetCase) -> None:
    print(
        f"WD40 memory write: starting {case.target} runtime checks",
        flush=True,
    )
    binary = build / case.binary
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")

    arguments = (*case.arguments, *loader_arguments(case.address))
    messages: list[dict[str, Any]] = [
        {"execute": "qmp_capabilities", "id": "cap"},
        read_message(
            "initial",
            space="physical",
            address=case.address,
        ),
        write_message(
            "physical-write",
            space="physical",
            address=case.address,
            data=PHYSICAL_DATA,
        ),
        read_message(
            "physical-readback",
            space="physical",
            address=case.address,
        ),
        write_message(
            "physical-cpu",
            space="physical",
            address=case.address,
            data="00",
            cpu_index=0,
        ),
        write_message(
            "empty",
            space="physical",
            address=case.address,
            data="",
        ),
        write_message(
            "odd",
            space="physical",
            address=case.address,
            data="0",
        ),
        write_message(
            "invalid-hex",
            space="physical",
            address=case.address,
            data="0g",
        ),
        write_message(
            "overflow",
            space="physical",
            address=(1 << 64) - 1,
            data="0000",
        ),
    ]

    if case.test_oversize:
        messages.append(
            write_message(
                "oversize",
                space="physical",
                address=case.address,
                data="00" * (MAX_WRITE + 1),
            )
        )

    if case.virtual:
        messages.extend(
            (
                write_message(
                    "virtual-default-write",
                    space="virtual",
                    address=case.address,
                    data=VIRTUAL_DATA,
                ),
                read_message(
                    "virtual-default-readback",
                    space="virtual",
                    address=case.address,
                ),
                write_message(
                    "virtual-cpu0-write",
                    space="virtual",
                    address=case.address,
                    data=CPU0_DATA,
                    cpu_index=0,
                ),
                read_message(
                    "virtual-cpu0-physical-readback",
                    space="physical",
                    address=case.address,
                ),
                write_message(
                    "virtual-invalid-cpu",
                    space="virtual",
                    address=case.address,
                    data="00",
                    cpu_index=9999,
                ),
            )
        )

    if case.second_cpu:
        messages.extend(
            (
                write_message(
                    "virtual-cpu1-write",
                    space="virtual",
                    address=case.address,
                    data=CPU1_DATA,
                    cpu_index=1,
                ),
                read_message(
                    "virtual-cpu1-physical-readback",
                    space="physical",
                    address=case.address,
                ),
            )
        )

    if case.test_unmapped:
        messages.append(
            write_message(
                "unmapped",
                space="physical",
                address=0xFFFFFFFFFFFFF000,
                data="00000000",
            )
        )

    messages.append({"execute": "quit", "id": "quit"})
    replies = run_qmp(binary.resolve(), arguments, messages)

    validate_read(
        require_return(replies, "initial"),
        space="physical",
        address=case.address,
        data=INITIAL_DATA,
        cpu_index=None,
    )
    validate_write(
        require_return(replies, "physical-write"),
        space="physical",
        address=case.address,
        cpu_index=None,
    )
    validate_read(
        require_return(replies, "physical-readback"),
        space="physical",
        address=case.address,
        data=PHYSICAL_DATA,
        cpu_index=None,
    )
    require_error(replies, "physical-cpu", "only valid for virtual")
    require_error(replies, "empty", "at least one byte")
    require_error(replies, "odd", "even number")
    require_error(replies, "invalid-hex", "non-hexadecimal", "offset 1")
    require_error(replies, "overflow", "wraps past UINT64_MAX")

    if case.test_oversize:
        require_error(replies, "oversize", "at most", "1048576")

    if case.virtual:
        validate_write(
            require_return(replies, "virtual-default-write"),
            space="virtual",
            address=case.address,
            cpu_index=0,
        )
        validate_read(
            require_return(replies, "virtual-default-readback"),
            space="virtual",
            address=case.address,
            data=VIRTUAL_DATA,
            cpu_index=0,
        )
        validate_write(
            require_return(replies, "virtual-cpu0-write"),
            space="virtual",
            address=case.address,
            cpu_index=0,
        )
        validate_read(
            require_return(replies, "virtual-cpu0-physical-readback"),
            space="physical",
            address=case.address,
            data=CPU0_DATA,
            cpu_index=None,
        )
        require_error(
            replies,
            "virtual-invalid-cpu",
            "cpu-index",
            "CPU number",
        )

    if case.second_cpu:
        validate_write(
            require_return(replies, "virtual-cpu1-write"),
            space="virtual",
            address=case.address,
            cpu_index=1,
        )
        validate_read(
            require_return(replies, "virtual-cpu1-physical-readback"),
            space="physical",
            address=case.address,
            data=CPU1_DATA,
            cpu_index=None,
        )

    if case.test_unmapped:
        require_error(replies, "unmapped", "Physical memory write failed")

    print(f"WD40 memory write: {case.target} runtime checks passed")


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
