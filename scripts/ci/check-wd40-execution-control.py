#!/usr/bin/env python3
"""Validate WD40 one-instruction execution and debug-stop provenance."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
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
    name: str
    binary: str
    arguments: tuple[str, ...]
    pc_register: int
    pc_value: int
    code_address: int
    code: str
    instruction_length: int
    second_cpu: bool = False
    running_witness: bool = False


TARGETS = (
    TargetCase(
        name="x86_64",
        binary="qemu-system-x86_64",
        arguments=(
            "-machine", "q35,accel=tcg",
            "-cpu", "max",
            "-smp", "2",
            "-m", "128M",
        ),
        pc_register=16,
        pc_value=0xFFF0,
        code_address=0xFFFFFFF0,
        code="9090",
        instruction_length=1,
        second_cpu=True,
        running_witness=True,
    ),
    TargetCase(
        name="aarch64",
        binary="qemu-system-aarch64",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-cpu", "max",
            "-smp", "2",
            "-m", "128M",
        ),
        pc_register=32,
        pc_value=0x41000000,
        code_address=0x41000000,
        code="1f2003d51f2003d5",
        instruction_length=4,
        second_cpu=True,
    ),
    TargetCase(
        name="m68k",
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
        pc_register=17,
        pc_value=0x10000,
        code_address=0x10000,
        code="4e714e71",
        instruction_length=2,
    ),
    TargetCase(
        name="ppc",
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        pc_register=64,
        pc_value=0x10000,
        code_address=0x10000,
        code="6000000060000000",
        instruction_length=4,
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


def isolate(path: str, start_marker: str, end_marker: str) -> str:
    text = source(path)
    start = text.find(start_marker)
    end = text.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit(
            f"{path}: could not isolate {start_marker.strip()!r} block"
        )
    return text[start:end]


def qapi_block() -> str:
    return isolate(
        "qapi/run-state.json",
        "##\n# @WD40DebugStopReason:\n",
        "##\n# @ShutdownCause:\n",
    )


def implementation_block() -> str:
    return isolate(
        "system/runstate.c",
        "typedef struct WD40DebugStopRecord {\n",
        "StatusInfo *qmp_query_status(Error **errp)\n",
    )


def validate_static() -> None:
    need(
        "qapi/run-state.json",
        "'enum': 'WD40DebugStopReason'",
        "'single-step', 'breakpoint', 'watchpoint'",
        "'enum': 'WD40WatchpointAccess'",
        "'read', 'write', 'access'",
        "'struct': 'WD40StepRequest'",
        "'struct': 'WD40DebugStop'",
        "'struct': 'WD40DebugStopState'",
        "'command': 'x-wd40-step-cpu'",
        "'command': 'x-wd40-query-last-debug-stop'",
        "'event': 'WD40_DEBUG_STOP'",
        "The machine must be stopped in prelaunch, paused, or debug state.",
        "matching WD40 step request, when one was outstanding",
        "'features': [ 'unstable' ]",
    )
    need(
        "include/system/runstate.h",
        " * wd40_debug_stop_notify:",
        "void wd40_debug_stop_notify(CPUState *cpu);",
        "before GDB or another frontend can",
    )
    need(
        "system/cpus.c",
        "wd40_debug_stop_notify(cpu);",
        "gdb_set_stop_cpu(cpu);",
        "qemu_system_debug_request();",
    )
    need(
        "system/runstate.c",
        '#include "system/hw_accel.h"',
        "typedef struct WD40DebugStopRecord",
        "wd40_debug_next_nonzero",
        "wd40_debug_stop_to_qapi",
        "wd40_debug_step_cancel",
        "void wd40_debug_stop_notify(CPUState *cpu)",
        "static void wd40_debug_stop_publish(RunState state)",
        "qmp_x_wd40_step_cpu",
        "qmp_x_wd40_query_last_debug_stop",
        "cpu->watchpoint_hit->vaddr",
        "cpu->watchpoint_hit->flags & BP_MEM_ACCESS",
        "cpu->cc->get_pc(cpu)",
        "current_accel()->gdbstub.sstep_flags",
        "vm_prepare_start(true)",
        "cpu_single_step(cpu, flags)",
        "cpu_resume(cpu)",
        "qemu_clock_enable(QEMU_CLOCK_VIRTUAL, true)",
        "qapi_event_send_wd40_debug_stop(",
        "wd40_debug_stop_publish(state);",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Single-step execution and debug-stop provenance",
        "x-wd40-step-cpu",
        "WD40_DEBUG_STOP",
        "x-wd40-query-last-debug-stop",
        "before the GDB frontend",
        "SSTEP_NOIRQ",
        "SSTEP_NOTIMER",
        "correlate",
    )

    for path, markers in (
        (
            "qapi/run-state.json",
            (
                "'enum': 'WD40DebugStopReason'",
                "'enum': 'WD40WatchpointAccess'",
                "'struct': 'WD40StepRequest'",
                "'struct': 'WD40DebugStop'",
                "'struct': 'WD40DebugStopState'",
                "'command': 'x-wd40-step-cpu'",
                "'command': 'x-wd40-query-last-debug-stop'",
                "'event': 'WD40_DEBUG_STOP'",
            ),
        ),
        (
            "include/system/runstate.h",
            ("void wd40_debug_stop_notify(CPUState *cpu);",),
        ),
        (
            "system/cpus.c",
            ("wd40_debug_stop_notify(cpu);",),
        ),
        (
            "system/runstate.c",
            (
                "typedef struct WD40DebugStopRecord",
                "void wd40_debug_stop_notify(CPUState *cpu)",
                "qmp_x_wd40_step_cpu",
                "qmp_x_wd40_query_last_debug_stop",
                "wd40_debug_stop_publish(state);",
            ),
        ),
        (
            "docs/devel/wd40-monitor-v2.rst",
            ("Single-step execution and debug-stop provenance",),
        ),
    ):
        for marker in markers:
            exactly_once(path, marker)

    cpus = source("system/cpus.c")
    capture = cpus.find("wd40_debug_stop_notify(cpu);")
    gdb = cpus.find("gdb_set_stop_cpu(cpu);", capture)
    request = cpus.find("qemu_system_debug_request();", gdb)
    if min(capture, gdb, request) < 0 or not capture < gdb < request:
        raise SystemExit(
            "system/cpus.c: WD40 provenance must be captured before GDB "
            "and the asynchronous stop request"
        )

    runstate = source("system/runstate.c")
    stop_event = runstate.find("qapi_event_send_stop();")
    publish = runstate.find("wd40_debug_stop_publish(state);", stop_event)
    if stop_event < 0 or publish < 0 or stop_event > publish:
        raise SystemExit(
            "system/runstate.c: typed stop publication must follow STOP"
        )

    implementation = implementation_block()
    forbidden = (
        "gdb_continue_partial(",
        "human_monitor_command",
        "monitor_printf(",
        "monitor_puts(",
        "#ifdef TARGET_",
        "cpu_exec(",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "system/runstate.c: execution control uses a textual, target, "
            f"or CPU-loop bypass: {present!r}"
        )

    state_check = implementation.find("if (state != RUN_STATE_PRELAUNCH &&")
    cpu_lookup = implementation.find("cpu = wd40_debug_cpu_by_index(")
    capability = implementation.find("flags &= current_accel()->gdbstub.sstep_flags")
    prepare = implementation.find("if (vm_prepare_start(true) != 0)")
    arm = implementation.find("wd40_debug_step_cpu = cpu;")
    enable = implementation.find("cpu_single_step(cpu, flags)")
    resume = implementation.find("cpu_resume(cpu)")
    if min(
        state_check, cpu_lookup, capability, prepare, arm, enable, resume
    ) < 0 or not (
        state_check < cpu_lookup < capability < prepare < arm < enable < resume
    ):
        raise SystemExit(
            "system/runstate.c: step validation, arming, and resume order "
            "is unsafe"
        )

    watch = implementation.find("if (cpu->watchpoint_hit)")
    pc = implementation.find("if (cpu->cc->get_pc)")
    pending = implementation.find("wd40_debug_pending_stop = record;")
    cancel = implementation.find("wd40_debug_step_cancel();", pending)
    if min(watch, pc, pending, cancel) < 0 or not watch < pc < pending < cancel:
        raise SystemExit(
            "system/runstate.c: transient stop details are not copied before "
            "the one-shot step is cleared"
        )

    qapi = qapi_block()
    for offset, line in enumerate(qapi.splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/run-state.json: execution-control documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


class QMPClient:
    def __init__(self, binary: Path, arguments: tuple[str, ...]) -> None:
        self.command = [
            str(binary),
            *arguments,
            "-display", "none",
            "-serial", "none",
            "-monitor", "none",
            "-nodefaults",
            "-S",
            "-qmp", "stdio",
        ]
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if self.process.stdin is None or self.process.stdout is None:
            raise SystemExit(f"{binary.name}: failed to open QMP pipes")
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.output = bytearray()
        self.events: list[dict[str, Any]] = []
        self.sequence = 0

        greeting = self._next_json(30)
        if not isinstance(greeting.get("QMP"), dict):
            self._abort(f"missing QMP greeting: {greeting!r}")
        require_empty_return(
            self.execute("qmp_capabilities"),
            f"{binary.name}/qmp_capabilities",
        )

    def _stderr(self) -> str:
        if self.process.stderr is None or self.process.poll() is None:
            return ""
        return self.process.stderr.read().decode("utf-8", errors="replace")

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

    def _pop_json(self) -> dict[str, Any] | None:
        while True:
            newline = self.output.find(b"\n")
            if newline < 0:
                return None
            line = bytes(self.output[:newline])
            del self.output[:newline + 1]
            line = line.strip()
            if not line.startswith(b"{"):
                continue
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(item, dict):
                return item

    def _next_json(self, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while True:
            item = self._pop_json()
            if item is not None:
                return item
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort("timed out waiting for QMP data")
            ready = self.selector.select(remaining)
            if not ready:
                if self.process.poll() is not None:
                    self._abort("QEMU exited before the expected QMP data")
                self._abort("timed out waiting for QMP output")
            chunk = os.read(self.process.stdout.fileno(), 65536)
            if not chunk:
                self._abort("QMP stdout closed unexpectedly")
            self.output.extend(chunk)

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.sequence += 1
        identifier = f"wd40-execution-{self.sequence}"
        message: dict[str, Any] = {"execute": command, "id": identifier}
        if arguments is not None:
            message["arguments"] = arguments
        assert self.process.stdin is not None
        try:
            self.process.stdin.write(
                json.dumps(message).encode("utf-8") + b"\n"
            )
            self.process.stdin.flush()
        except BrokenPipeError:
            self._abort(f"QMP pipe closed while sending {command!r}")

        while True:
            item = self._next_json(45)
            if item.get("id") == identifier:
                return item
            if isinstance(item.get("event"), str):
                self.events.append(item)

    def wait_event(self, name: str, timeout: float = 45) -> dict[str, Any]:
        for index, event in enumerate(self.events):
            if event.get("event") == name:
                return self.events.pop(index)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._abort(f"timed out waiting for event {name!r}")
            item = self._next_json(remaining)
            if item.get("event") == name:
                return item
            if isinstance(item.get("event"), str):
                self.events.append(item)

    def close(self) -> None:
        if self.process.poll() is None:
            require_empty_return(
                self.execute("quit"),
                f"{Path(self.command[0]).name}/quit",
            )
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.selector.close()
        if self.process.returncode != 0:
            self._abort("QEMU returned failure after QMP quit")

    def __enter__(self) -> "QMPClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self.close()
        else:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait(timeout=5)
            self.selector.close()


def require_empty_return(reply: dict[str, Any], context: str) -> None:
    if reply.get("return") != {}:
        raise SystemExit(f"{context}: expected empty return: {reply!r}")


def require_object_return(
    reply: dict[str, Any],
    context: str,
) -> dict[str, Any]:
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


def snapshot(client: QMPClient, cpu_index: int) -> dict[str, Any]:
    return require_object_return(
        client.execute(
            "x-wd40-query-cpu-registers",
            {"cpu-index": cpu_index},
        ),
        f"snapshot/cpu{cpu_index}",
    )


def register_by_number(
    value: dict[str, Any],
    number: int,
    context: str,
) -> dict[str, Any]:
    registers = value.get("registers")
    if not isinstance(registers, list):
        raise SystemExit(f"{context}: snapshot lacks registers")
    matches = [
        item
        for item in registers
        if isinstance(item, dict) and item.get("number") == number
    ]
    if len(matches) != 1:
        raise SystemExit(
            f"{context}: expected one register {number}, found {len(matches)}"
        )
    register = matches[0]
    raw = register.get("value")
    width = register.get("bytes")
    if (
        register.get("available") is not True
        or not isinstance(raw, str)
        or not isinstance(width, int)
        or width <= 0
        or len(raw) != width * 2
        or re.fullmatch(r"[0-9a-f]+", raw) is None
    ):
        raise SystemExit(f"{context}: malformed register: {register!r}")
    return register


def byte_order(value: dict[str, Any]) -> str:
    big = value.get("target-big-endian")
    if not isinstance(big, bool):
        raise SystemExit(f"snapshot lacks target endianness: {value!r}")
    return "big" if big else "little"


def decode_register(register: dict[str, Any], order: str) -> int:
    return int.from_bytes(bytes.fromhex(register["value"]), order)


def encode_register(value: int, width: int, order: str) -> str:
    return value.to_bytes(width, order).hex()


def set_pc(
    client: QMPClient,
    case: TargetCase,
    initial: dict[str, Any],
) -> None:
    register = register_by_number(
        initial,
        case.pc_register,
        f"{case.name}/pc-register",
    )
    value = encode_register(
        case.pc_value,
        register["bytes"],
        byte_order(initial),
    )
    result = require_object_return(
        client.execute(
            "x-wd40-write-cpu-register",
            {
                "cpu-index": 0,
                "number": case.pc_register,
                "value": value,
            },
        ),
        f"{case.name}/set-pc",
    )
    if result.get("value") != value:
        raise SystemExit(
            f"{case.name}: PC write normalized unexpectedly: {result!r}"
        )


def write_code(client: QMPClient, case: TargetCase) -> None:
    result = require_object_return(
        client.execute(
            "x-wd40-write-memory",
            {
                "space": "virtual",
                "address": case.code_address,
                "data": case.code,
                "cpu-index": 0,
            },
        ),
        f"{case.name}/write-code",
    )
    if result.get("bytes") != len(case.code) // 2:
        raise SystemExit(f"{case.name}: short code write: {result!r}")


def validate_stop(
    event: dict[str, Any],
    case: TargetCase,
    sequence: int | None,
    reason: str,
    previous_generation: int,
) -> dict[str, Any]:
    data = event.get("data")
    if not isinstance(data, dict):
        raise SystemExit(f"{case.name}: stop event lacks data: {event!r}")
    generation = data.get("generation")
    if not isinstance(generation, int) or generation <= previous_generation:
        raise SystemExit(
            f"{case.name}: invalid stop generation {generation!r}"
        )
    if data.get("cpu-index") != 0 or data.get("reason") != reason:
        raise SystemExit(f"{case.name}: unexpected stop: {data!r}")
    if sequence is None:
        if "step-sequence" in data:
            raise SystemExit(
                f"{case.name}: unrelated stop retained step sequence"
            )
    elif data.get("step-sequence") != sequence:
        raise SystemExit(
            f"{case.name}: stop did not correlate sequence {sequence}: "
            f"{data!r}"
        )
    if not isinstance(data.get("pc"), int):
        raise SystemExit(f"{case.name}: stop omitted the program counter")
    return data


def require_debug_state(client: QMPClient, context: str) -> None:
    status = require_object_return(client.execute("query-status"), context)
    if status != {"running": False, "status": "debug"}:
        raise SystemExit(f"{context}: expected debug state, got {status!r}")


def validate_last_stop(
    client: QMPClient,
    expected: dict[str, Any],
    context: str,
) -> None:
    state = require_object_return(
        client.execute("x-wd40-query-last-debug-stop"),
        context,
    )
    if state.get("available") is not True or state.get("stop") != expected:
        raise SystemExit(
            f"{context}: last-stop mismatch: {state!r} != {expected!r}"
        )


def exercise_target(build_dir: Path, case: TargetCase) -> None:
    binary = build_dir / case.binary
    if not binary.is_file():
        raise SystemExit(f"{case.name}: missing binary {binary}")

    print(f"WD40 execution control: starting {case.name}", flush=True)
    with QMPClient(binary, case.arguments) as client:
        commands = client.execute("query-commands").get("return")
        if not isinstance(commands, list):
            raise SystemExit(f"{case.name}: query-commands failed")
        names = {
            item.get("name")
            for item in commands
            if isinstance(item, dict)
        }
        for command in (
            "x-wd40-step-cpu",
            "x-wd40-query-last-debug-stop",
            "x-wd40-query-cpu-registers",
            "x-wd40-write-cpu-register",
            "x-wd40-write-memory",
            "x-wd40-insert-debug-point",
            "x-wd40-remove-debug-point",
        ):
            if command not in names:
                raise SystemExit(
                    f"{case.name}: query-commands omitted {command}"
                )

        initial_state = require_object_return(
            client.execute("x-wd40-query-last-debug-stop"),
            f"{case.name}/initial-stop",
        )
        if initial_state != {"available": False}:
            raise SystemExit(
                f"{case.name}: unexpected initial stop: {initial_state!r}"
            )

        require_error(
            client.execute(
                "x-wd40-step-cpu",
                {"cpu-index": 9999},
            ),
            f"{case.name}/invalid-cpu",
            "CPU index 9999",
            "does not exist",
        )

        initial0 = snapshot(client, 0)
        write_code(client, case)
        set_pc(client, case, initial0)
        before0 = snapshot(client, 0)
        pc0 = register_by_number(
            before0,
            case.pc_register,
            f"{case.name}/before-step",
        )
        order = byte_order(before0)
        before_pc = decode_register(pc0, order)
        if before_pc != case.pc_value:
            raise SystemExit(
                f"{case.name}: PC set failed: {before_pc:#x}"
            )

        second_before: str | None = None
        if case.second_cpu:
            second = register_by_number(
                snapshot(client, 1),
                case.pc_register,
                f"{case.name}/cpu1-before",
            )
            second_before = second["value"]

        generation = 0
        last_event: dict[str, Any] | None = None
        for step_index in range(2):
            request = require_object_return(
                client.execute(
                    "x-wd40-step-cpu",
                    {"cpu-index": 0},
                ),
                f"{case.name}/step-{step_index}/request",
            )
            sequence = request.get("sequence")
            if (
                not isinstance(sequence, int)
                or sequence <= 0
                or request.get("cpu-index") != 0
            ):
                raise SystemExit(
                    f"{case.name}: malformed step request: {request!r}"
                )
            event = client.wait_event("WD40_DEBUG_STOP")
            data = validate_stop(
                event,
                case,
                sequence,
                "single-step",
                generation,
            )
            generation = data["generation"]
            last_event = data
            require_debug_state(
                client,
                f"{case.name}/step-{step_index}/status",
            )
            validate_last_stop(
                client,
                data,
                f"{case.name}/step-{step_index}/last-stop",
            )

            after = snapshot(client, 0)
            after_pc = decode_register(
                register_by_number(
                    after,
                    case.pc_register,
                    f"{case.name}/step-{step_index}/pc",
                ),
                order,
            )
            expected_pc = case.pc_value + (
                step_index + 1
            ) * case.instruction_length
            if after_pc != expected_pc:
                raise SystemExit(
                    f"{case.name}: step {step_index} moved PC to "
                    f"{after_pc:#x}, expected {expected_pc:#x}"
                )

            if case.second_cpu:
                second_after = register_by_number(
                    snapshot(client, 1),
                    case.pc_register,
                    f"{case.name}/cpu1-after-{step_index}",
                )
                if second_after["value"] != second_before:
                    raise SystemExit(
                        f"{case.name}: unselected CPU executed during step"
                    )

        assert last_event is not None
        breakpoint_address = last_event["pc"]
        point = {
            "type": "software-breakpoint",
            "address": breakpoint_address,
            "length": 1,
        }
        inserted = require_object_return(
            client.execute("x-wd40-insert-debug-point", point),
            f"{case.name}/breakpoint/insert",
        )
        if inserted != point:
            raise SystemExit(
                f"{case.name}: breakpoint tuple mismatch: {inserted!r}"
            )
        require_empty_return(
            client.execute("cont"),
            f"{case.name}/breakpoint/cont",
        )
        breakpoint_event = validate_stop(
            client.wait_event("WD40_DEBUG_STOP"),
            case,
            None,
            "breakpoint",
            generation,
        )
        generation = breakpoint_event["generation"]
        if breakpoint_event["pc"] != breakpoint_address:
            raise SystemExit(
                f"{case.name}: breakpoint stop moved PC: "
                f"{breakpoint_event!r}"
            )
        require_debug_state(client, f"{case.name}/breakpoint/status")
        validate_last_stop(
            client,
            breakpoint_event,
            f"{case.name}/breakpoint/last-stop",
        )
        removed = require_object_return(
            client.execute("x-wd40-remove-debug-point", point),
            f"{case.name}/breakpoint/remove",
        )
        if removed != point:
            raise SystemExit(
                f"{case.name}: removed breakpoint tuple mismatch"
            )

        if case.running_witness:
            require_empty_return(
                client.execute("cont"),
                f"{case.name}/running/cont",
            )
            require_error(
                client.execute("x-wd40-step-cpu", {"cpu-index": 0}),
                f"{case.name}/running/rejection",
                "requires prelaunch, paused, or debug state",
                "running",
            )
            require_empty_return(
                client.execute("stop"),
                f"{case.name}/running/stop",
            )

    print(f"WD40 execution control: {case.name} PASS", flush=True)


def exercise_no_cpu(build_dir: Path) -> None:
    binary = build_dir / "qemu-system-x86_64"
    with QMPClient(binary, ("-machine", "none")) as client:
        require_error(
            client.execute("x-wd40-step-cpu"),
            "no-cpu/step",
            "No realized CPU is available",
        )


def selected_targets(names: list[str]) -> tuple[TargetCase, ...]:
    if not names:
        return TARGETS
    known = {case.name: case for case in TARGETS}
    unknown = [name for name in names if name not in known]
    if unknown:
        raise SystemExit(
            f"unknown target(s) {unknown!r}; choose from {sorted(known)!r}"
        )
    return tuple(known[name] for name in names)


def main() -> None:
    validate_static()
    if len(sys.argv) == 1:
        print("WD40 execution-control static contract: PASS")
        return

    build_dir = Path(sys.argv[1]).resolve()
    if not build_dir.is_dir():
        raise SystemExit(f"build directory does not exist: {build_dir}")

    cases = selected_targets(sys.argv[2:])
    for case in cases:
        exercise_target(build_dir, case)
    if any(case.name == "x86_64" for case in cases):
        exercise_no_cpu(build_dir)
        print("WD40 execution control: no-CPU PASS")


if __name__ == "__main__":
    main()
