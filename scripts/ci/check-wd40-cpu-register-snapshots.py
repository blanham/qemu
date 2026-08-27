#!/usr/bin/env python3
"""Validate WD40 cross-architecture CPU register snapshots."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TargetCase:
    binary: str
    arguments: tuple[str, ...]
    target: str
    bits: int
    big_endian: bool
    required_registers: frozenset[str]
    cpus: int = 1


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
        bits=64,
        big_endian=False,
        required_registers=frozenset(("rax", "rip", "eflags")),
        cpus=2,
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
        bits=64,
        big_endian=False,
        required_registers=frozenset(("x0", "pc", "cpsr")),
        cpus=2,
    ),
    TargetCase(
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
        target="m68k",
        bits=32,
        big_endian=True,
        required_registers=frozenset(("d0", "sp", "pc")),
    ),
    TargetCase(
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        target="ppc",
        bits=32,
        big_endian=True,
        required_registers=frozenset(("r0", "pc", "msr")),
    ),
)


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def need(path: str, *markers: str) -> None:
    data = source(path)
    missing = [marker for marker in markers if marker not in data]
    if missing:
        raise SystemExit(f"{path}: missing {missing!r}")


def exactly_once(path: str, marker: str) -> None:
    count = source(path).count(marker)
    if count != 1:
        raise SystemExit(f"{path}: expected one {marker!r}, found {count}")


def qapi_block() -> str:
    data = source("qapi/misc.json")
    start_marker = "##\n# @WD40CPURegister:\n"
    end_marker = "##\n# @LogCategoryInfo:\n"
    start = data.find(start_marker)
    end = data.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit("qapi/misc.json: could not isolate register block")
    return data[start:end]


def implementation_block() -> str:
    data = source("monitor/qmp-cmds.c")
    start_marker = "typedef struct WD40RegisterDescriptor {\n"
    end_marker = "static LogCategoryInfoList *qmp_log_category_info_list(void)\n"
    start = data.find(start_marker)
    end = data.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit("monitor/qmp-cmds.c: could not isolate register block")
    return data[start:end]


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/misc.json: WD40 register documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_resolution_precedence(implementation: str) -> None:
    core_loop = implementation.find(
        "for (i = 0; i < cpu->cc->gdb_num_core_regs; i++)"
    )
    supplemental_loop = implementation.find(
        "for (i = 0; i < gdb_descriptors->len; i++)"
    )
    if core_loop < 0 or supplemental_loop < 0:
        raise SystemExit(
            "monitor/qmp-cmds.c: incomplete GDB descriptor collection"
        )
    if core_loop >= supplemental_loop:
        raise SystemExit(
            "monitor/qmp-cmds.c: descriptor collection does not mirror "
            "GDB core-first resolution"
        )

    duplicate_guard = re.search(
        r"if \(wd40_register_descriptor_present\(descriptors,\n"
        r"\s+descriptor\.number\)\) \{(?P<body>.*?)\n\s+\}",
        implementation,
        re.DOTALL,
    )
    if duplicate_guard is None:
        raise SystemExit(
            "monitor/qmp-cmds.c: missing overlapping-register guard"
        )

    body = duplicate_guard.group("body")
    if "continue;" not in body:
        raise SystemExit(
            "monitor/qmp-cmds.c: overlapping descriptors do not retain "
            "first-resolution precedence"
        )
    if "error_setg" in body or "goto fail" in body:
        raise SystemExit(
            "monitor/qmp-cmds.c: overlapping descriptors remain fatal"
        )


def validate_static() -> None:
    need(
        "qapi/misc.json",
        "'struct': 'WD40CPURegister'",
        "'struct': 'WD40CPURegisterSnapshot'",
        "'command': 'x-wd40-query-cpu-registers'",
        "'target-big-endian': 'bool'",
        "'registers': [ 'WD40CPURegister' ]",
        "'features': [ 'unstable' ]",
        "When ranges overlap, the descriptor",
        "selected by GDB lookup is retained: legacy core first, then",
        "supplemental registration order.",
    )
    need(
        "monitor/qmp-cmds.c",
        '#include "exec/gdbstub.h"',
        '#include "qemu/target-info.h"',
        '#include "system/hw_accel.h"',
        "gdb_get_register_list(cpu)",
        "gdb_read_register(cpu, value, descriptor->number)",
        "cpu_synchronize_state(cpu)",
        "target_name()",
        "target_long_bits()",
        "target_big_endian()",
        'g_strdup_printf("gdb-reg-%d", descriptor->number)',
        "gdb_read_register() checks the legacy core range first.",
        "Supplemental feature ranges are checked in registration order.",
        "Retain the descriptor for the callback GDB resolves first.",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Cross-architecture CPU register snapshots",
        "x-wd40-query-cpu-registers",
        "GDB register registry and callbacks",
        "descriptor selection follows ``gdb_read_register()``",
        "legacy core range wins first",
        "supplemental features in",
        "registration order",
        "does not pause a running machine",
        "without scraping ``info registers``",
    )
    exactly_once("qapi/misc.json", "'struct': 'WD40CPURegister'")
    exactly_once("qapi/misc.json", "'struct': 'WD40CPURegisterSnapshot'")
    exactly_once("qapi/misc.json", "'command': 'x-wd40-query-cpu-registers'")
    exactly_once(
        "monitor/qmp-cmds.c",
        "qmp_x_wd40_query_cpu_registers",
    )
    exactly_once(
        "docs/devel/wd40-monitor-v2.rst",
        "Cross-architecture CPU register snapshots",
    )

    implementation = implementation_block()
    forbidden = (
        "cpu_dump_state",
        "human_monitor_command",
        '"info registers"',
        "GDB register number %d is duplicated",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "monitor/qmp-cmds.c: register service has forbidden behavior: "
            f"{present!r}"
        )
    validate_resolution_precedence(implementation)
    validate_qapi_doc_width()


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
        raise SystemExit(f"{identifier}: malformed snapshot reply: {reply!r}")
    return result


def require_error(
    replies: dict[str, dict[str, Any]], identifier: str, fragment: str
) -> None:
    error = replies[identifier].get("error")
    if not isinstance(error, dict) or fragment not in error.get("desc", ""):
        raise SystemExit(
            f"{identifier}: expected error containing {fragment!r}: "
            f"{replies[identifier]!r}"
        )


def validate_register(register: Any, *, context: str) -> tuple[int, str, bool]:
    if not isinstance(register, dict):
        raise SystemExit(f"{context}: register is not an object: {register!r}")

    number = register.get("number")
    name = register.get("name")
    described = register.get("described")
    available = register.get("available")
    byte_count = register.get("bytes")
    feature = register.get("feature")
    value = register.get("value")

    if not isinstance(number, int) or number < 0:
        raise SystemExit(f"{context}: invalid register number: {register!r}")
    if not isinstance(name, str) or not name:
        raise SystemExit(f"{context}: invalid register name: {register!r}")
    if not isinstance(described, bool) or not isinstance(available, bool):
        raise SystemExit(f"{context}: invalid register booleans: {register!r}")
    if not isinstance(byte_count, int) or byte_count < 0:
        raise SystemExit(f"{context}: invalid register byte count: {register!r}")
    if feature is not None and (not isinstance(feature, str) or not feature):
        raise SystemExit(f"{context}: invalid register feature: {register!r}")

    if available:
        if byte_count == 0 or not isinstance(value, str):
            raise SystemExit(f"{context}: available register lacks value: {register!r}")
        if len(value) != byte_count * 2 or not re.fullmatch(r"[0-9a-f]+", value):
            raise SystemExit(f"{context}: malformed register hex: {register!r}")
    elif byte_count != 0 or value is not None:
        raise SystemExit(f"{context}: unavailable register has data: {register!r}")

    if described and name.startswith("gdb-reg-"):
        raise SystemExit(f"{context}: synthetic name marked described: {register!r}")
    return number, name, available


def validate_snapshot(
    snapshot: dict[str, Any],
    case: TargetCase,
    *,
    cpu_index: int,
) -> None:
    expected_scalars = {
        "cpu-index": cpu_index,
        "target": case.target,
        "target-bits": case.bits,
        "target-big-endian": case.big_endian,
    }
    actual_scalars = {key: snapshot.get(key) for key in expected_scalars}
    if actual_scalars != expected_scalars:
        raise SystemExit(
            f"{case.target}/cpu{cpu_index}: snapshot metadata mismatch: "
            f"{actual_scalars!r} != {expected_scalars!r}"
        )
    if not isinstance(snapshot.get("qom-type"), str) or not snapshot["qom-type"]:
        raise SystemExit(f"{case.target}/cpu{cpu_index}: missing CPU QOM type")

    registers = snapshot.get("registers")
    if not isinstance(registers, list) or not registers:
        raise SystemExit(f"{case.target}/cpu{cpu_index}: empty register list")

    numbers: list[int] = []
    names: dict[str, bool] = {}
    for offset, register in enumerate(registers):
        number, name, available = validate_register(
            register,
            context=f"{case.target}/cpu{cpu_index}/register{offset}",
        )
        numbers.append(number)
        names[name] = available

    if numbers != sorted(set(numbers)):
        raise SystemExit(
            f"{case.target}/cpu{cpu_index}: register numbers are not "
            "sorted and unique"
        )

    missing = case.required_registers.difference(names)
    unavailable = {
        name for name in case.required_registers if name in names and not names[name]
    }
    if missing or unavailable:
        raise SystemExit(
            f"{case.target}/cpu{cpu_index}: required register failure: "
            f"missing={sorted(missing)!r}, unavailable={sorted(unavailable)!r}"
        )


def validate_target(build: Path, case: TargetCase) -> None:
    print(
        f"WD40 CPU register snapshot: starting {case.target} runtime checks",
        flush=True,
    )
    binary = build / case.binary
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")

    messages: list[dict[str, Any]] = [
        {"execute": "qmp_capabilities", "id": "cap"},
        {"execute": "x-wd40-query-cpu-registers", "id": "default"},
        {
            "execute": "x-wd40-query-cpu-registers",
            "arguments": {"cpu-index": 0},
            "id": "cpu0",
        },
    ]
    if case.cpus > 1:
        messages.append(
            {
                "execute": "x-wd40-query-cpu-registers",
                "arguments": {"cpu-index": 1},
                "id": "cpu1",
            }
        )
    messages.extend(
        (
            {
                "execute": "x-wd40-query-cpu-registers",
                "arguments": {"cpu-index": 9999},
                "id": "invalid",
            },
            {"execute": "quit", "id": "quit"},
        )
    )

    replies = run_qmp(binary.resolve(), case.arguments, messages)
    default = require_return(replies, "default")
    cpu0 = require_return(replies, "cpu0")
    validate_snapshot(default, case, cpu_index=0)
    validate_snapshot(cpu0, case, cpu_index=0)
    if default != cpu0:
        raise SystemExit(f"{case.target}: default CPU snapshot differs from CPU 0")

    if case.cpus > 1:
        cpu1 = require_return(replies, "cpu1")
        validate_snapshot(cpu1, case, cpu_index=1)
    require_error(replies, "invalid", "does not exist")
    print(f"WD40 CPU register snapshot: {case.target} runtime checks passed")


def select_targets(names: list[str]) -> tuple[TargetCase, ...]:
    if not names:
        return TARGETS

    available = {case.target: case for case in TARGETS}
    unknown = sorted(set(names).difference(available))
    if unknown:
        raise SystemExit(
            f"unknown target(s) {unknown!r}; choose from {sorted(available)!r}"
        )
    return tuple(available[name] for name in names)


def validate_runtime(build: Path, target_names: list[str]) -> None:
    for case in select_targets(target_names):
        validate_target(build, case)


validate_static()
if len(sys.argv) >= 2:
    validate_runtime(Path(sys.argv[1]).resolve(), sys.argv[2:])
