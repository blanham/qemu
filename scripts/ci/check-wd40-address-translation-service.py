#!/usr/bin/env python3
"""Validate WD40 typed CPU virtual-to-physical address translation."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TargetCase:
    binary: str
    arguments: tuple[str, ...]
    target: str
    target_bits: int
    big_endian: bool
    address: int
    translated: bool
    physical_address: int | None = None
    second_cpu: bool = False


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
        target_bits=64,
        big_endian=False,
        address=0x10000,
        translated=True,
        physical_address=0x10000,
        second_cpu=True,
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
        target_bits=64,
        big_endian=False,
        address=0x41000000,
        translated=True,
        physical_address=0x41000000,
        second_cpu=True,
    ),
    TargetCase(
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
        target="m68k",
        target_bits=32,
        big_endian=True,
        address=0x10000,
        translated=True,
        physical_address=0x10000,
    ),
    TargetCase(
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        target="ppc",
        target_bits=32,
        big_endian=True,
        address=0x10000,
        translated=True,
        physical_address=0x10000,
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
    start_marker = "##\n# @WD40MemoryTransactionAttributes:\n"
    end_marker = "##\n# @memsave:\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "qapi/machine.json: could not isolate address-translation block"
        )
    return text[start:end]


def implementation_block() -> str:
    text = source("system/physmem-qmp-cmds.c")
    start_marker = "WD40AddressTranslation *\n"
    end_marker = "void qmp_memsave(uint64_t addr"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: could not isolate "
            "address-translation block"
        )
    return text[start:end]


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/machine.json: WD40 translation documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_static() -> None:
    need(
        "qapi/machine.json",
        "'struct': 'WD40MemoryTransactionAttributes'",
        "'struct': 'WD40AddressTranslation'",
        "'command': 'x-wd40-translate-address'",
        "'*physical-address': 'uint64'",
        "'*address-space-index': 'int'",
        "'*page-bits': 'uint8'",
        "'*page-size': 'uint64'",
        "'*attributes': 'WD40MemoryTransactionAttributes'",
        "'features': [ 'unstable' ]",
    )
    need(
        "system/physmem-qmp-cmds.c",
        '#include "qemu/target-info.h"',
        "qmp_x_wd40_translate_address",
        "migration_guest_ram_loading()",
        "cpu_synchronize_state(cpu)",
        "cpu_translate_for_debug(cpu, address, &translation)",
        "translation.lg_page_size >= 64",
        "cpu_asidx_from_attrs(cpu,",
        "result->translated = translated",
        "if (!translated)",
        "result->attributes->security_space = translation.attrs.space",
        "result->attributes->address_type = translation.attrs.address_type",
        "target_long_bits()",
        "target_big_endian()",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Typed virtual-to-physical translation",
        "x-wd40-translate-address",
        "ordinary translation miss",
        "address-space index",
        "does not prove",
        "x-wd40-read-memory",
    )

    exactly_once(
        "qapi/machine.json",
        "'struct': 'WD40MemoryTransactionAttributes'",
    )
    exactly_once(
        "qapi/machine.json",
        "'struct': 'WD40AddressTranslation'",
    )
    exactly_once(
        "qapi/machine.json",
        "'command': 'x-wd40-translate-address'",
    )
    exactly_once(
        "system/physmem-qmp-cmds.c",
        "qmp_x_wd40_translate_address",
    )
    exactly_once(
        "docs/devel/wd40-monitor-v2.rst",
        "Typed virtual-to-physical translation",
    )

    implementation = implementation_block()
    forbidden = (
        "get_phys_addr_debug",
        "cpu_memory_rw_debug",
        "address_space_read",
        "physical_memory_read",
        "human_monitor_command",
        "cpu_dump_state",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: translation service bypasses "
            f"the common debug hook or uses text: {present!r}"
        )

    synchronize = implementation.find("cpu_synchronize_state(cpu)")
    translate = implementation.find(
        "cpu_translate_for_debug(cpu, address, &translation)"
    )
    if synchronize < 0 or translate < 0 or synchronize > translate:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: translation is not synchronized"
        )

    miss = implementation.find("if (!translated)")
    miss_return = implementation.find("return result;", miss)
    physical = implementation.find("result->has_physical_address = true")
    if (
        miss < 0
        or miss_return < 0
        or physical < 0
        or not (translate < miss < miss_return < physical)
    ):
        raise SystemExit(
            "system/physmem-qmp-cmds.c: ordinary misses are not returned "
            "before successful-translation fields"
        )

    page_guard = implementation.find("translation.lg_page_size >= 64")
    page_shift = implementation.find(
        "UINT64_C(1) << translation.lg_page_size"
    )
    if page_guard < 0 or page_shift < 0 or page_guard > page_shift:
        raise SystemExit(
            "system/physmem-qmp-cmds.c: page-size shift lacks bounds check"
        )

    validate_qapi_doc_width()


def loader_arguments(address: int) -> tuple[str, ...]:
    result: list[str] = []
    for offset, value in enumerate((0x41, 0x42, 0x43, 0x44)):
        result.extend(
            (
                "-device",
                (
                    f"loader,id=wd40-translation-byte{offset},"
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
        raise SystemExit(
            f"{identifier}: malformed address-translation reply: {reply!r}"
        )
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


def validate_attributes(attributes: Any, identifier: str) -> None:
    if not isinstance(attributes, dict):
        raise SystemExit(f"{identifier}: attributes are not an object")

    boolean_fields = {
        "unspecified",
        "secure",
        "user",
        "memory",
        "debug",
        "address-type",
    }
    integer_fields = {"security-space", "requester-id", "pid"}
    expected_fields = boolean_fields | integer_fields
    if set(attributes) != expected_fields:
        raise SystemExit(
            f"{identifier}: unexpected attribute fields {attributes!r}"
        )
    for field in boolean_fields:
        if type(attributes[field]) is not bool:
            raise SystemExit(
                f"{identifier}: attribute {field!r} is not boolean"
            )
    for field in integer_fields:
        if type(attributes[field]) is not int:
            raise SystemExit(
                f"{identifier}: attribute {field!r} is not integer"
            )

    if not 0 <= attributes["security-space"] <= 3:
        raise SystemExit(f"{identifier}: invalid security-space value")
    if not 0 <= attributes["requester-id"] <= 0xFFFF:
        raise SystemExit(f"{identifier}: invalid requester-id value")
    if not 0 <= attributes["pid"] <= 0xFF:
        raise SystemExit(f"{identifier}: invalid pid value")


def validate_translation(
    result: dict[str, Any],
    case: TargetCase,
    *,
    cpu_index: int,
    identifier: str,
) -> None:
    mandatory = {
        "cpu-index",
        "target",
        "target-bits",
        "target-big-endian",
        "qom-type",
        "virtual-address",
        "translated",
    }
    successful = {
        "physical-address",
        "address-space-index",
        "page-bits",
        "page-size",
        "attributes",
    }
    expected_fields = mandatory | (successful if case.translated else set())
    if set(result) != expected_fields:
        raise SystemExit(
            f"{identifier}: unexpected translation fields: {result!r}"
        )

    expected_mandatory = {
        "cpu-index": cpu_index,
        "target": case.target,
        "target-bits": case.target_bits,
        "target-big-endian": case.big_endian,
        "virtual-address": case.address,
        "translated": case.translated,
    }
    for field, expected in expected_mandatory.items():
        if result.get(field) != expected:
            raise SystemExit(
                f"{identifier}: {field}={result.get(field)!r}, "
                f"expected {expected!r}; result={result!r}"
            )
    if not isinstance(result.get("qom-type"), str) or not result["qom-type"]:
        raise SystemExit(f"{identifier}: missing concrete CPU QOM type")

    if not case.translated:
        return

    if result["physical-address"] != case.physical_address:
        raise SystemExit(
            f"{identifier}: physical address "
            f"0x{result['physical-address']:x}, expected "
            f"0x{case.physical_address:x}"
        )
    if type(result["address-space-index"]) is not int:
        raise SystemExit(f"{identifier}: address-space-index is not integer")
    if result["address-space-index"] < 0:
        raise SystemExit(f"{identifier}: negative address-space-index")

    page_bits = result["page-bits"]
    page_size = result["page-size"]
    if type(page_bits) is not int or not 0 < page_bits < 64:
        raise SystemExit(f"{identifier}: invalid page-bits {page_bits!r}")
    if page_size != 1 << page_bits:
        raise SystemExit(
            f"{identifier}: page-size {page_size!r} does not match "
            f"page-bits {page_bits!r}"
        )
    mask = page_size - 1
    if (
        result["physical-address"] & mask
        != result["virtual-address"] & mask
    ):
        raise SystemExit(
            f"{identifier}: virtual and physical page offsets differ"
        )

    validate_attributes(result["attributes"], identifier)


def validate_target(build: Path, case: TargetCase) -> None:
    print(
        f"WD40 address translation: starting {case.target} runtime checks",
        flush=True,
    )
    binary = build / case.binary
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")

    messages: list[dict[str, Any]] = [
        {"execute": "qmp_capabilities", "id": "cap"},
        {
            "execute": "x-wd40-translate-address",
            "arguments": {"address": case.address},
            "id": "default",
        },
        {
            "execute": "x-wd40-translate-address",
            "arguments": {
                "address": case.address,
                "cpu-index": 0,
            },
            "id": "cpu0",
        },
        {
            "execute": "x-wd40-translate-address",
            "arguments": {
                "address": case.address,
                "cpu-index": 9999,
            },
            "id": "invalid-cpu",
        },
    ]
    if case.second_cpu:
        messages.append(
            {
                "execute": "x-wd40-translate-address",
                "arguments": {
                    "address": case.address,
                    "cpu-index": 1,
                },
                "id": "cpu1",
            }
        )
    messages.append({"execute": "quit", "id": "quit"})

    arguments = (*case.arguments, *loader_arguments(case.address))
    replies = run_qmp(binary.resolve(), arguments, messages)

    default = require_return(replies, "default")
    cpu0 = require_return(replies, "cpu0")
    validate_translation(
        default, case, cpu_index=0, identifier=f"{case.target}-default"
    )
    validate_translation(
        cpu0, case, cpu_index=0, identifier=f"{case.target}-cpu0"
    )
    if default != cpu0:
        raise SystemExit(
            f"{case.target}: default CPU and explicit CPU 0 differ: "
            f"{default!r} != {cpu0!r}"
        )

    require_error(replies, "invalid-cpu", "cpu-index", "CPU number")

    if case.second_cpu:
        validate_translation(
            require_return(replies, "cpu1"),
            case,
            cpu_index=1,
            identifier=f"{case.target}-cpu1",
        )

    print(
        f"WD40 address translation: {case.target} runtime checks passed"
    )


def select_targets(names: list[str]) -> list[TargetCase]:
    by_name = {case.target: case for case in TARGETS}
    unknown = sorted(set(names).difference(by_name))
    if unknown:
        raise SystemExit(f"unknown address-translation targets: {unknown!r}")
    return [by_name[name] for name in names]


def main() -> None:
    validate_static()
    if len(sys.argv) == 1:
        print("WD40 address translation: static contract validated")
        return
    if len(sys.argv) == 2:
        raise SystemExit(
            "usage: check-wd40-address-translation-service.py "
            "BUILD_DIR TARGET [TARGET ...]"
        )

    build = Path(sys.argv[1]).resolve()
    if not build.is_dir():
        raise SystemExit(f"missing build directory: {build}")
    for case in select_targets(sys.argv[2:]):
        validate_target(build, case)


if __name__ == "__main__":
    main()
