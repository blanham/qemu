#!/usr/bin/env python3
"""Validate WD40 exact cross-architecture CPU register writes."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import selectors
import subprocess
import sys
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class TargetCase:
    binary: str
    arguments: tuple[str, ...]
    target: str
    register_name: str
    second_cpu: bool = False
    unavailable_register: int | None = None
    absent_register: int | None = None


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
        register_name="rax",
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
        register_name="x0",
        second_cpu=True,
    ),
    TargetCase(
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
        target="m68k",
        register_name="d0",
    ),
    TargetCase(
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        target="ppc",
        register_name="r0",
        unavailable_register=32,
        absent_register=70,
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
    text = source("qapi/misc.json")
    start_marker = "##\n# @WD40CPURegisterWrite:\n"
    end_marker = "##\n# @LogCategoryInfo:\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "qapi/misc.json: could not isolate CPU register-write block"
        )
    return text[start:end]


def implementation_block() -> str:
    text = source("monitor/qmp-cmds.c")
    start_marker = "static int wd40_register_hex_digit(char value)\n"
    end_marker = "static LogCategoryInfoList *qmp_log_category_info_list(void)\n"
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            "monitor/qmp-cmds.c: could not isolate CPU register-write block"
        )
    return text[start:end]


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/misc.json: CPU register-write documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_static() -> None:
    need(
        "qapi/misc.json",
        "'struct': 'WD40CPURegisterWrite'",
        "'command': 'x-wd40-write-cpu-register'",
        "'returns': 'WD40CPURegisterWrite'",
        "'features': [ 'unstable' ]",
        "'number': 'int'",
        "'value': 'str'",
        "'*cpu-index': 'int'",
        "exact hexadecimal bytes in GDB register byte order",
        "post-write read-back",
    )
    need(
        "monitor/qmp-cmds.c",
        "static int wd40_register_hex_digit",
        "wd40_register_hex_to_bytes",
        "wd40_register_descriptor_for_number",
        "qmp_x_wd40_write_cpu_register",
        "cpu_synchronize_state(cpu)",
        "gdb_get_register_list(cpu)",
        "gdb_read_register(cpu, register_value, descriptor.number)",
        "gdb_write_register(cpu, buffer, descriptor.number)",
        "g_byte_array_set_size(register_value, 0)",
        "result->value = wd40_register_value_to_hex(register_value)",
        "exposes GDB register %\" PRId64",
        "\" more than once",
        "is not writable",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Typed CPU register writes",
        "x-wd40-write-cpu-register",
        "same byte",
        "order returned by ``x-wd40-query-cpu-registers``",
        "reads the",
        "register to establish its exact width",
        "fresh read-back",
        "not a transactional rollback",
        "guarantee.",
        "does not pause a running guest",
    )

    exactly_once("qapi/misc.json", "'struct': 'WD40CPURegisterWrite'")
    exactly_once(
        "qapi/misc.json",
        "'command': 'x-wd40-write-cpu-register'",
    )
    exactly_once(
        "monitor/qmp-cmds.c",
        "qmp_x_wd40_write_cpu_register",
    )
    exactly_once(
        "docs/devel/wd40-monitor-v2.rst",
        "Typed CPU register writes",
    )

    implementation = implementation_block()
    forbidden = (
        "cpu_dump_state",
        "human_monitor_command",
        '"info registers"',
        "cpu_set_pc(",
        "address_space_",
        "cpu_memory_rw_debug",
        "#ifdef TARGET_",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "monitor/qmp-cmds.c: register write bypasses the common GDB "
            f"registry or uses text: {present!r}"
        )

    bounds = implementation.find("if (number < 0 || number > INT_MAX)")
    descriptor = implementation.find(
        "wd40_register_descriptor_for_number(cpu, number, &descriptor"
    )
    synchronize = implementation.find("cpu_synchronize_state(cpu)")
    initial_read = implementation.find(
        "gdb_read_register(cpu, register_value, descriptor.number)"
    )
    decode = implementation.find(
        "buffer = wd40_register_hex_to_bytes(value, register_value->len"
    )
    write = implementation.find(
        "gdb_write_register(cpu, buffer, descriptor.number)"
    )
    clear = implementation.find("g_byte_array_set_size(register_value, 0)")
    readback = implementation.find(
        "gdb_read_register(cpu, register_value, descriptor.number)",
        initial_read + 1,
    )
    encode = implementation.find(
        "result->value = wd40_register_value_to_hex(register_value)"
    )
    if min(
        bounds,
        descriptor,
        synchronize,
        initial_read,
        decode,
        write,
        clear,
        readback,
        encode,
    ) < 0:
        raise SystemExit(
            "monitor/qmp-cmds.c: incomplete register-write validation path"
        )
    if not (
        synchronize
        < descriptor
        < initial_read
        < decode
        < write
        < clear
        < readback
        < encode
    ):
        raise SystemExit(
            "monitor/qmp-cmds.c: register-write operations are ordered unsafely"
        )

    empty = implementation.find("if (hex_length == 0)")
    odd = implementation.find("if (hex_length & 1)")
    exact = implementation.find("if (hex_length / 2 != expected_bytes)")
    allocation = implementation.find("bytes = g_malloc(expected_bytes)")
    if min(empty, odd, exact, allocation) < 0 or not (
        empty < odd < exact < allocation
    ):
        raise SystemExit(
            "monitor/qmp-cmds.c: register hex validation must precede "
            "allocation"
        )

    duplicate = re.search(
        r"if \(matches != 0\) \{(?P<body>.*?)\n\s+\}",
        implementation,
        re.DOTALL,
    )
    if duplicate is None:
        raise SystemExit(
            "monitor/qmp-cmds.c: missing duplicate-register rejection"
        )
    duplicate_body = duplicate.group("body")
    for marker in ("error_setg", "more than once", "return false;"):
        if marker not in duplicate_body:
            raise SystemExit(
                "monitor/qmp-cmds.c: duplicate-register rejection is not fatal"
            )

    validate_qapi_doc_width()


class QMPClient:
    def __init__(self, binary: Path, arguments: tuple[str, ...]) -> None:
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
        self.command = command
        self.process = subprocess.Popen(
            command,
            text=True,
            encoding="utf-8",
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=1,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise SystemExit(f"{binary.name}: failed to open QMP pipes")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.sequence = 0

        greeting = self._next_json(30)
        if not isinstance(greeting.get("QMP"), dict):
            self._abort(f"missing QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def _stderr(self) -> str:
        if self.process.stderr is None or self.process.poll() is None:
            return ""
        return self.process.stderr.read()

    def _abort(self, message: str) -> None:
        if self.process.poll() is None:
            self.process.kill()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        raise SystemExit(
            f"{message}; rc={self.process.returncode}; "
            f"command={self.command!r}; stderr={self._stderr()!r}"
        )

    def _next_json(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort("timed out waiting for a QMP reply")
            ready = self.selector.select(remaining)
            if not ready:
                if self.process.poll() is not None:
                    self._abort("QEMU exited before the expected QMP reply")
                self._abort("timed out waiting for QMP output")
            line = self.process.stdout.readline()
            if line == "":
                self._abort("QMP stdout closed unexpectedly")
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                return item

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        identifier = f"wd40-{self.sequence}"
        message: dict[str, Any] = {
            "execute": command,
            "id": identifier,
        }
        if arguments is not None:
            message["arguments"] = arguments
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except BrokenPipeError:
            self._abort(f"QMP pipe closed while sending {command!r}")

        while True:
            item = self._next_json(45)
            if item.get("id") == identifier:
                return item

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                self.execute("quit")
            except SystemExit:
                if self.process.poll() is None:
                    self.process.kill()
                raise
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.selector.close()
        if self.process.returncode != 0:
            self._abort("QEMU returned failure after QMP quit")

    def __enter__(self) -> QMPClient:
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self.close()
            return
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=5)
        self.selector.close()


def require_return(reply: dict[str, Any], context: str) -> dict[str, Any]:
    result = reply.get("return")
    if not isinstance(result, dict):
        raise SystemExit(f"{context}: expected object return: {reply!r}")
    return result


def require_error(
    reply: dict[str, Any],
    context: str,
    *fragments: str,
) -> None:
    error = reply.get("error")
    description = error.get("desc") if isinstance(error, dict) else None
    missing = (
        list(fragments)
        if not isinstance(description, str)
        else [fragment for fragment in fragments if fragment not in description]
    )
    if not isinstance(error, dict) or missing:
        raise SystemExit(
            f"{context}: expected error containing {fragments!r}: {reply!r}"
        )


def query_snapshot(
    client: QMPClient,
    cpu_index: int | None = None,
) -> dict[str, Any]:
    arguments = None if cpu_index is None else {"cpu-index": cpu_index}
    return require_return(
        client.execute("x-wd40-query-cpu-registers", arguments),
        f"snapshot/cpu{cpu_index if cpu_index is not None else 'default'}",
    )


def register_by_name(
    snapshot: dict[str, Any],
    name: str,
    *,
    context: str,
) -> dict[str, Any]:
    registers = snapshot.get("registers")
    if not isinstance(registers, list):
        raise SystemExit(f"{context}: snapshot lacks a register array")
    matches = [
        register
        for register in registers
        if isinstance(register, dict) and register.get("name") == name
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"{context}: expected one register named {name!r}, "
            f"found {len(matches)}"
        )
    register = matches[0]
    if register.get("available") is not True:
        raise SystemExit(f"{context}: register {name!r} is unavailable")
    value = register.get("value")
    byte_count = register.get("bytes")
    if (
        not isinstance(value, str)
        or not isinstance(byte_count, int)
        or byte_count <= 0
        or len(value) != byte_count * 2
        or re.fullmatch(r"[0-9a-f]+", value) is None
    ):
        raise SystemExit(
            f"{context}: register {name!r} has malformed value metadata: "
            f"{register!r}"
        )
    return register


def register_by_number(
    snapshot: dict[str, Any],
    number: int,
    *,
    context: str,
) -> dict[str, Any] | None:
    registers = snapshot.get("registers")
    if not isinstance(registers, list):
        raise SystemExit(f"{context}: snapshot lacks a register array")
    matches = [
        register
        for register in registers
        if isinstance(register, dict) and register.get("number") == number
    ]
    if len(matches) > 1:
        raise SystemExit(
            f"{context}: duplicate snapshot register number {number}"
        )
    return matches[0] if matches else None


def choose_pattern(original: str, salt: int) -> str:
    byte_count = len(original) // 2
    data = bytes(
        (0x31 + salt * 23 + offset * 29) & 0xFF
        for offset in range(byte_count)
    )
    candidate = data.hex()
    if candidate == original:
        candidate = bytes(value ^ 0xFF for value in data).hex()
    if candidate == original:
        raise SystemExit("could not construct a distinct register value")
    return candidate


def write_arguments(
    number: int,
    value: str,
    cpu_index: int | None = None,
) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "number": number,
        "value": value,
    }
    if cpu_index is not None:
        arguments["cpu-index"] = cpu_index
    return arguments


def validate_write_result(
    result: dict[str, Any],
    register: dict[str, Any],
    *,
    cpu_index: int,
    value: str,
    context: str,
) -> None:
    expected: dict[str, Any] = {
        "cpu-index": cpu_index,
        "number": register["number"],
        "name": register["name"],
        "described": register["described"],
        "bytes": register["bytes"],
        "value": value.lower(),
    }
    if "feature" in register:
        expected["feature"] = register["feature"]
    if result != expected:
        raise SystemExit(
            f"{context}: register write mismatch: {result!r} != {expected!r}"
        )


def validate_target(build: Path, case: TargetCase) -> None:
    print(
        f"WD40 CPU register write: starting {case.target} runtime checks",
        flush=True,
    )
    binary = build / case.binary
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")

    with QMPClient(binary.resolve(), case.arguments) as client:
        snapshot0 = query_snapshot(client, 0)
        register0 = register_by_name(
            snapshot0,
            case.register_name,
            context=f"{case.target}/cpu0/initial",
        )
        number = register0["number"]
        original0 = register0["value"]
        pattern0 = choose_pattern(original0, 0)

        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(number, original0, 9999),
            ),
            f"{case.target}/invalid-cpu",
            "CPU index 9999",
            "does not exist",
        )
        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(-1, "00", 0),
            ),
            f"{case.target}/negative-register",
            "between 0 and",
        )

        numbers = [
            register.get("number")
            for register in snapshot0.get("registers", [])
            if isinstance(register, dict)
            and isinstance(register.get("number"), int)
        ]
        missing_number = max(numbers) + 1
        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(missing_number, "00", 0),
            ),
            f"{case.target}/missing-register",
            "has no GDB register",
            str(missing_number),
        )

        if case.absent_register is not None:
            if register_by_number(
                snapshot0,
                case.absent_register,
                context=f"{case.target}/absent-register",
            ) is not None:
                raise SystemExit(
                    f"{case.target}: register gap {case.absent_register} "
                    "appeared in the snapshot"
                )
            require_error(
                client.execute(
                    "x-wd40-write-cpu-register",
                    write_arguments(case.absent_register, "00", 0),
                ),
                f"{case.target}/gap-register",
                "has no GDB register",
                str(case.absent_register),
            )

        if case.unavailable_register is not None:
            unavailable = register_by_number(
                snapshot0,
                case.unavailable_register,
                context=f"{case.target}/unavailable-register",
            )
            if unavailable is None or unavailable.get("available") is not False:
                raise SystemExit(
                    f"{case.target}: register {case.unavailable_register} "
                    "is not the expected unavailable descriptor"
                )
            require_error(
                client.execute(
                    "x-wd40-write-cpu-register",
                    write_arguments(case.unavailable_register, "00", 0),
                ),
                f"{case.target}/unavailable-register",
                "is not available",
            )

        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(number, "", 0),
            ),
            f"{case.target}/empty-value",
            "at least one byte",
        )
        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(number, "0", 0),
            ),
            f"{case.target}/odd-value",
            "even number",
        )
        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(
                    number,
                    "g0" + "00" * (register0["bytes"] - 1),
                    0,
                ),
            ),
            f"{case.target}/nonhex-value",
            "non-hexadecimal",
            "offset 0",
        )
        require_error(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(number, "00", 0),
            ),
            f"{case.target}/wrong-width",
            "requires exactly",
            "got 1",
        )

        unchanged0 = register_by_name(
            query_snapshot(client, 0),
            case.register_name,
            context=f"{case.target}/cpu0/after-invalid",
        )
        if unchanged0["value"] != original0:
            raise SystemExit(
                f"{case.target}: rejected requests changed "
                f"{case.register_name}"
            )

        written0 = require_return(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(number, pattern0.upper()),
            ),
            f"{case.target}/cpu0/write",
        )
        validate_write_result(
            written0,
            register0,
            cpu_index=0,
            value=pattern0,
            context=f"{case.target}/cpu0/write",
        )
        readback0 = register_by_name(
            query_snapshot(client, 0),
            case.register_name,
            context=f"{case.target}/cpu0/readback",
        )
        if readback0["value"] != pattern0:
            raise SystemExit(
                f"{case.target}: snapshot did not observe CPU 0 write"
            )

        restored0 = require_return(
            client.execute(
                "x-wd40-write-cpu-register",
                write_arguments(number, original0, 0),
            ),
            f"{case.target}/cpu0/restore",
        )
        validate_write_result(
            restored0,
            register0,
            cpu_index=0,
            value=original0,
            context=f"{case.target}/cpu0/restore",
        )

        if case.second_cpu:
            snapshot1 = query_snapshot(client, 1)
            register1 = register_by_name(
                snapshot1,
                case.register_name,
                context=f"{case.target}/cpu1/initial",
            )
            if register1["number"] != number:
                raise SystemExit(
                    f"{case.target}: CPU register numbering differs by CPU"
                )
            original1 = register1["value"]
            pattern1 = choose_pattern(original1, 1)
            if pattern1 == pattern0:
                pattern1 = choose_pattern(original1, 2)

            written1 = require_return(
                client.execute(
                    "x-wd40-write-cpu-register",
                    write_arguments(number, pattern1, 1),
                ),
                f"{case.target}/cpu1/write",
            )
            validate_write_result(
                written1,
                register1,
                cpu_index=1,
                value=pattern1,
                context=f"{case.target}/cpu1/write",
            )
            readback1 = register_by_name(
                query_snapshot(client, 1),
                case.register_name,
                context=f"{case.target}/cpu1/readback",
            )
            if readback1["value"] != pattern1:
                raise SystemExit(
                    f"{case.target}: snapshot did not observe CPU 1 write"
                )
            cpu0_during_cpu1 = register_by_name(
                query_snapshot(client, 0),
                case.register_name,
                context=f"{case.target}/cpu0/during-cpu1-write",
            )
            if cpu0_during_cpu1["value"] != original0:
                raise SystemExit(
                    f"{case.target}: CPU 1 write changed CPU 0 state"
                )

            restored1 = require_return(
                client.execute(
                    "x-wd40-write-cpu-register",
                    write_arguments(number, original1, 1),
                ),
                f"{case.target}/cpu1/restore",
            )
            validate_write_result(
                restored1,
                register1,
                cpu_index=1,
                value=original1,
                context=f"{case.target}/cpu1/restore",
            )
            final1 = register_by_name(
                query_snapshot(client, 1),
                case.register_name,
                context=f"{case.target}/cpu1/final",
            )
            if final1["value"] != original1:
                raise SystemExit(
                    f"{case.target}: CPU 1 register was not restored"
                )

        final0 = register_by_name(
            query_snapshot(client, 0),
            case.register_name,
            context=f"{case.target}/cpu0/final",
        )
        if final0["value"] != original0:
            raise SystemExit(
                f"{case.target}: CPU 0 register was not restored"
            )

    print(f"WD40 CPU register write: {case.target} runtime checks passed")


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
