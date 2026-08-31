#!/usr/bin/env python3
"""Validate WD40 typed accelerator-backed breakpoints and watchpoints."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
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
    running_state_witness: bool = False


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
        running_state_witness=True,
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
    ),
    TargetCase(
        name="m68k",
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
    ),
    TargetCase(
        name="ppc",
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
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
        "qapi/misc.json",
        "##\n# @WD40DebugPointType:\n",
        "##\n# @LogCategoryInfo:\n",
    )


def implementation_block() -> str:
    return isolate(
        "monitor/qmp-cmds.c",
        "static bool wd40_debug_point_type_to_gdb(",
        "static LogCategoryInfoList *qmp_log_category_info_list(void)\n",
    )


def header_block() -> str:
    return isolate(
        "include/exec/gdbstub.h",
        "/**\n * gdb_breakpoint_insert:\n",
        "void gdb_set_stop_cpu(CPUState *cpu);\n",
    )


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/misc.json: debug-point documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_static() -> None:
    need(
        "include/exec/gdbstub.h",
        '#include "exec/vaddr.h"',
        '#include "gdbstub/enums.h"',
        " * gdb_breakpoint_insert:",
        "int gdb_breakpoint_insert(CPUState *cpu, GdbBreakpointType type,",
        "int gdb_breakpoint_remove(CPUState *cpu, GdbBreakpointType type,",
        "void gdb_breakpoint_remove_all(CPUState *cpu);",
        "Returns: zero on success or a negative errno value.",
    )
    need(
        "qapi/misc.json",
        "'enum': 'WD40DebugPointType'",
        "'software-breakpoint', 'hardware-breakpoint'",
        "'write-watchpoint', 'read-watchpoint'",
        "'access-watchpoint'",
        "'struct': 'WD40DebugPoint'",
        "'type': 'WD40DebugPointType'",
        "'address': 'uint64'",
        "'length': 'uint64'",
        "'command': 'x-wd40-insert-debug-point'",
        "'command': 'x-wd40-remove-debug-point'",
        "'returns': 'WD40DebugPoint'",
        "'features': [ 'unstable' ]",
        "The guest must be stopped.",
        "Removal is not",
        "idempotent: a missing point is an error.",
    )
    need(
        "monitor/qmp-cmds.c",
        "static bool wd40_debug_point_type_to_gdb(",
        "WD40_DEBUG_POINT_TYPE_SOFTWARE_BREAKPOINT",
        "WD40_DEBUG_POINT_TYPE_HARDWARE_BREAKPOINT",
        "WD40_DEBUG_POINT_TYPE_WRITE_WATCHPOINT",
        "WD40_DEBUG_POINT_TYPE_READ_WATCHPOINT",
        "WD40_DEBUG_POINT_TYPE_ACCESS_WATCHPOINT",
        "*gdb_type = GDB_BREAKPOINT_SW;",
        "*gdb_type = GDB_BREAKPOINT_HW;",
        "*gdb_type = GDB_WATCHPOINT_WRITE;",
        "*gdb_type = GDB_WATCHPOINT_READ;",
        "*gdb_type = GDB_WATCHPOINT_ACCESS;",
        "if (length == 0)",
        "if (address > max_address || length > max_address ||",
        "address > max_address - (length - 1))",
        "if (!first_cpu)",
        "if (runstate_is_running())",
        "gdb_breakpoint_insert(first_cpu, gdb_type,",
        "gdb_breakpoint_remove(first_cpu, gdb_type,",
        "if (ret == -ENOSYS)",
        "if (!insert && ret == -ENOENT)",
        "error_setg_errno(errp, -ret,",
        "qmp_x_wd40_insert_debug_point",
        "qmp_x_wd40_remove_debug_point",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Typed breakpoints and watchpoints",
        "x-wd40-insert-debug-point",
        "x-wd40-remove-debug-point",
        "accelerator guest-debug hooks",
        "guest must be stopped",
        "complete",
        "range must fit QEMU's ``vaddr`` container",
        "share the accelerator's GDB debug-point plane",
        "not a cross-CPU rollback",
        "guarantee.",
    )

    for path, markers in (
        (
            "include/exec/gdbstub.h",
            (
                " * gdb_breakpoint_insert:\n",
                "int gdb_breakpoint_insert(CPUState *cpu",
                "int gdb_breakpoint_remove(CPUState *cpu",
                "void gdb_breakpoint_remove_all(CPUState *cpu);",
            ),
        ),
        (
            "qapi/misc.json",
            (
                "'enum': 'WD40DebugPointType'",
                "'struct': 'WD40DebugPoint'",
                "'command': 'x-wd40-insert-debug-point'",
                "'command': 'x-wd40-remove-debug-point'",
            ),
        ),
        (
            "monitor/qmp-cmds.c",
            (
                "static bool wd40_debug_point_type_to_gdb(",
                "static WD40DebugPoint *\nwd40_change_debug_point(",
                "qmp_x_wd40_insert_debug_point",
                "qmp_x_wd40_remove_debug_point",
            ),
        ),
        (
            "docs/devel/wd40-monitor-v2.rst",
            ("Typed breakpoints and watchpoints",),
        ),
    ):
        for marker in markers:
            exactly_once(path, marker)

    header = header_block()
    if header.count("GdbBreakpointType") != 2:
        raise SystemExit(
            "include/exec/gdbstub.h: unexpected debug-point declaration shape"
        )

    implementation = implementation_block()
    forbidden = (
        "cpu_breakpoint_insert(",
        "cpu_breakpoint_remove(",
        "cpu_watchpoint_insert(",
        "cpu_watchpoint_remove(",
        "insert_gdbstub_breakpoint",
        "remove_gdbstub_breakpoint",
        "human_monitor_command",
        "#ifdef TARGET_",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "monitor/qmp-cmds.c: debug-point service bypasses the shared "
            f"gdbstub boundary: {present!r}"
        )

    no_cpu = implementation.find("if (!first_cpu)")
    running = implementation.find("if (runstate_is_running())")
    validate = implementation.find(
        "if (!wd40_debug_point_validate(type, address, length,"
    )
    insert = implementation.find(
        "ret = gdb_breakpoint_insert(first_cpu, gdb_type,"
    )
    remove = implementation.find(
        "ret = gdb_breakpoint_remove(first_cpu, gdb_type,"
    )
    allocate = implementation.find("result = g_new0(WD40DebugPoint, 1)")
    if min(no_cpu, running, validate, insert, remove, allocate) < 0:
        raise SystemExit(
            "monitor/qmp-cmds.c: incomplete debug-point operation path"
        )
    if not no_cpu < running < validate < insert < remove < allocate:
        raise SystemExit(
            "monitor/qmp-cmds.c: debug-point validation and mutation "
            "are ordered unsafely"
        )

    zero = implementation.find("if (length == 0)")
    range_check = implementation.find(
        "if (address > max_address || length > max_address ||"
    )
    conversion = implementation.find(
        "return wd40_debug_point_type_to_gdb(type, gdb_type, errp);"
    )
    if min(zero, range_check, conversion) < 0 or not (
        zero < range_check < conversion
    ):
        raise SystemExit(
            "monitor/qmp-cmds.c: range validation must precede enum "
            "conversion and accelerator dispatch"
        )

    for marker in (
        "GDB_BREAKPOINT_SW",
        "GDB_BREAKPOINT_HW",
        "GDB_WATCHPOINT_WRITE",
        "GDB_WATCHPOINT_READ",
        "GDB_WATCHPOINT_ACCESS",
    ):
        if implementation.count(marker) != 1:
            raise SystemExit(
                f"monitor/qmp-cmds.c: expected one mapping for {marker}"
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
        return self.process.stderr.read().decode(
            "utf-8",
            errors="replace",
        )

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
                self._abort("timed out waiting for a QMP reply")
            ready = self.selector.select(remaining)
            if not ready:
                if self.process.poll() is not None:
                    self._abort("QEMU exited before the expected QMP reply")
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
        identifier = f"wd40-debug-point-{self.sequence}"
        message: dict[str, Any] = {
            "execute": command,
            "id": identifier,
        }
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

    def close(self) -> None:
        if self.process.poll() is None:
            try:
                require_empty_return(
                    self.execute("quit"),
                    f"{Path(self.command[0]).name}/quit",
                )
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

    def __enter__(self) -> "QMPClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if exc_type is None:
            self.close()
            return
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


def point_arguments(
    point_type: str,
    address: int,
    length: int,
) -> dict[str, Any]:
    return {
        "type": point_type,
        "address": address,
        "length": length,
    }


def require_point(
    reply: dict[str, Any],
    context: str,
    point_type: str,
    address: int,
    length: int,
) -> None:
    result = require_object_return(reply, context)
    expected = point_arguments(point_type, address, length)
    if result != expected:
        raise SystemExit(
            f"{context}: expected exact tuple {expected!r}, got {result!r}"
        )


def query_running(client: QMPClient, context: str) -> bool:
    result = require_object_return(client.execute("query-status"), context)
    running = result.get("running")
    if not isinstance(running, bool):
        raise SystemExit(f"{context}: invalid query-status result: {result!r}")
    return running


def wait_running(
    client: QMPClient,
    expected: bool,
    context: str,
) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if query_running(client, context) is expected:
            return
        time.sleep(0.05)
    raise SystemExit(f"{context}: running state did not become {expected}")


def exercise_target(build_dir: Path, case: TargetCase) -> None:
    binary = build_dir / case.binary
    if not binary.is_file():
        raise SystemExit(f"{case.name}: missing binary {binary}")

    point_types = (
        "software-breakpoint",
        "hardware-breakpoint",
        "write-watchpoint",
        "read-watchpoint",
        "access-watchpoint",
    )

    with QMPClient(binary, case.arguments) as client:
        require_error(
            client.execute(
                "x-wd40-insert-debug-point",
                point_arguments("software-breakpoint", 0x1000, 0),
            ),
            f"{case.name}/zero-length",
            "length must be nonzero",
        )
        require_error(
            client.execute(
                "x-wd40-insert-debug-point",
                point_arguments(
                    "software-breakpoint",
                    0xFFFFFFFFFFFFFFFF,
                    2,
                ),
            ),
            f"{case.name}/wrapped-range",
            "exceeds the guest virtual-address container",
        )

        for index, point_type in enumerate(point_types):
            address = 0x2000 + index * 0x100
            length = 1 if "breakpoint" in point_type else 4
            arguments = point_arguments(point_type, address, length)
            require_point(
                client.execute("x-wd40-insert-debug-point", arguments),
                f"{case.name}/{point_type}/insert",
                point_type,
                address,
                length,
            )
            require_point(
                client.execute("x-wd40-remove-debug-point", arguments),
                f"{case.name}/{point_type}/remove",
                point_type,
                address,
                length,
            )
            require_error(
                client.execute("x-wd40-remove-debug-point", arguments),
                f"{case.name}/{point_type}/remove-missing",
                "not installed",
            )

        address = 0x3000
        require_point(
            client.execute(
                "x-wd40-insert-debug-point",
                point_arguments("write-watchpoint", address, 4),
            ),
            f"{case.name}/exact-watchpoint/insert",
            "write-watchpoint",
            address,
            4,
        )
        require_error(
            client.execute(
                "x-wd40-remove-debug-point",
                point_arguments("write-watchpoint", address, 2),
            ),
            f"{case.name}/exact-watchpoint/wrong-length",
            "not installed",
        )
        require_point(
            client.execute(
                "x-wd40-remove-debug-point",
                point_arguments("write-watchpoint", address, 4),
            ),
            f"{case.name}/exact-watchpoint/remove",
            "write-watchpoint",
            address,
            4,
        )

        if case.running_state_witness:
            require_empty_return(
                client.execute("cont"),
                f"{case.name}/cont",
            )
            wait_running(client, True, f"{case.name}/wait-running")
            require_error(
                client.execute(
                    "x-wd40-insert-debug-point",
                    point_arguments("software-breakpoint", 0x4000, 1),
                ),
                f"{case.name}/running-rejection",
                "guest must be stopped",
            )
            require_empty_return(
                client.execute("stop"),
                f"{case.name}/stop",
            )
            wait_running(client, False, f"{case.name}/wait-stopped")


def exercise_no_cpu(build_dir: Path) -> None:
    binary = build_dir / "qemu-system-x86_64"
    if not binary.is_file():
        raise SystemExit(f"no-cpu witness: missing binary {binary}")
    with QMPClient(binary, ("-machine", "none")) as client:
        require_error(
            client.execute(
                "x-wd40-insert-debug-point",
                point_arguments("software-breakpoint", 0, 1),
            ),
            "no-cpu witness",
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
        print("WD40 debug-point static contract: PASS")
        return

    build_dir = Path(sys.argv[1]).resolve()
    if not build_dir.is_dir():
        raise SystemExit(f"build directory does not exist: {build_dir}")

    cases = selected_targets(sys.argv[2:])
    for case in cases:
        exercise_target(build_dir, case)
        print(f"WD40 debug-point runtime contract ({case.name}): PASS")
    if any(case.name == "x86_64" for case in cases):
        exercise_no_cpu(build_dir)
        print("WD40 debug-point no-CPU contract: PASS")


if __name__ == "__main__":
    main()
