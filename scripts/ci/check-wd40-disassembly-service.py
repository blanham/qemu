#!/usr/bin/env python3
"""Validate WD40 bounded structured guest disassembly."""

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
MAX_INSTRUCTIONS = 256
MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class TargetCase:
    name: str
    binary: str
    arguments: tuple[str, ...]
    address: int
    data: str
    lengths: tuple[int, ...]
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
        address=0x10000,
        data="90c3",
        lengths=(1, 1),
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
        address=0x41000000,
        data="1f2003d5c0035fd6",
        lengths=(4, 4),
    ),
    TargetCase(
        name="m68k",
        binary="qemu-system-m68k",
        arguments=(
            "-machine", "virt,accel=tcg",
            "-m", "64M",
        ),
        address=0x10000,
        data="4e714e75",
        lengths=(2, 2),
    ),
    TargetCase(
        name="ppc",
        binary="qemu-system-ppc",
        arguments=(
            "-machine", "ppce500,accel=tcg",
            "-m", "128M",
        ),
        address=0x10000,
        data="600000004e800020",
        lengths=(4, 4),
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
        "qapi/machine.json",
        "##\n# @WD40DisassembledInstruction:\n",
        "##\n# @memsave:\n",
    )


def implementation_block() -> str:
    return isolate(
        "disas/disas-mon.c",
        "#define WD40_DISASSEMBLY_MAX_INSTRUCTIONS",
        "/* Disassembler for the monitor.  */\n",
    )


def capstone_block() -> str:
    return isolate(
        "disas/capstone.c",
        "/*\n * Decode one instruction without formatting an address",
        "/* Disassemble COUNT insns at PC for the target.  */\n",
    )


def validate_static() -> None:
    need(
        "qapi/machine.json",
        "'struct': 'WD40DisassembledInstruction'",
        "'struct': 'WD40Disassembly'",
        "'command': 'x-wd40-disassemble'",
        "'space': 'WD40MemorySpace'",
        "'instruction-count': 'uint64'",
        "'*max-bytes': 'uint64'",
        "'*cpu-index': 'int'",
        "'returns': 'WD40Disassembly'",
        "'features': [ 'unstable' ]",
        "The guest must be stopped.",
        "between 1 byte and",
        "64 KiB.",
    )
    need(
        "include/disas/dis-asm.h",
        "int cap_disas_one(disassemble_info *info, uint64_t pc);",
        "# define cap_disas_one(i, p)        (-1)",
    )
    need(
        "disas/capstone.c",
        "int cap_disas_one(disassemble_info *info, uint64_t pc)",
        "info->buffer_length",
        "cs_disasm_iter(handle, &cbuf, &available,",
        'info->fprintf_func(info->stream, "%s%s%s"',
        "return result;",
    )
    need(
        "disas/disas-mon.c",
        "#define WD40_DISASSEMBLY_MAX_INSTRUCTIONS 256U",
        "#define WD40_DISASSEMBLY_MAX_BYTES (64U * 1024U)",
        "typedef struct WD40DisassemblyContext",
        "wd40_disassembly_read_memory",
        "cpu_memory_rw_debug(context->debug.cpu, memaddr, myaddr,",
        "address_space_read(context->debug.cpu->as, memaddr,",
        "wd40_disassembly_bytes_to_hex",
        "cap_disas_one(info, pc)",
        "info->print_insn(pc, info)",
        "if (runstate_is_running())",
        "cpu_synchronize_state(cpu)",
        "qmp_x_wd40_disassemble",
        "instruction_count > WD40_DISASSEMBLY_MAX_INSTRUCTIONS",
        "max_bytes > WD40_DISASSEMBLY_MAX_BYTES",
        "address > UINT64_MAX - (max_bytes - 1)",
        "qapi_free_WD40Disassembly(result)",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Bounded structured disassembly",
        "x-wd40-disassemble",
        "does not have to scrape",
        "guest must be stopped",
        "between 1 and 256 instructions",
        "between 1 byte and 64 KiB",
        "single-instruction adapter",
        "no available decoder",
    )

    for path, markers in (
        (
            "qapi/machine.json",
            (
                "'struct': 'WD40DisassembledInstruction'",
                "'struct': 'WD40Disassembly'",
                "'command': 'x-wd40-disassemble'",
            ),
        ),
        (
            "include/disas/dis-asm.h",
            (
                "int cap_disas_one(disassemble_info *info",
                "# define cap_disas_one(i, p)",
            ),
        ),
        (
            "disas/capstone.c",
            ("int cap_disas_one(disassemble_info *info",),
        ),
        (
            "disas/disas-mon.c",
            (
                "#define WD40_DISASSEMBLY_MAX_INSTRUCTIONS",
                "typedef struct WD40DisassemblyContext",
                "qmp_x_wd40_disassemble",
            ),
        ),
        (
            "docs/devel/wd40-monitor-v2.rst",
            ("Bounded structured disassembly",),
        ),
    ):
        for marker in markers:
            exactly_once(path, marker)

    implementation = implementation_block()
    forbidden = (
        "monitor_disas(",
        "human_monitor_command",
        "monitor_puts(",
        "monitor_printf(",
        "cap_disas_monitor(",
        "sscanf(",
        "strtok(",
        "x/NI",
    )
    present = [marker for marker in forbidden if marker in implementation]
    if present:
        raise SystemExit(
            "disas/disas-mon.c: structured service uses a textual "
            f"or legacy path: {present!r}"
        )

    capstone = capstone_block()
    if "cap_dump_insn(" in capstone or "cap_disas_monitor(" in capstone:
        raise SystemExit(
            "disas/capstone.c: one-instruction adapter reuses "
            "monitor-formatted output"
        )

    validation_order = (
        implementation.find("if (instruction_count == 0 ||"),
        implementation.find("if (!has_max_bytes)"),
        implementation.find("if (max_bytes == 0 ||"),
        implementation.find("if (address > UINT64_MAX - (max_bytes - 1))"),
        implementation.find("if (runstate_is_running())"),
        implementation.find("cpu = wd40_disassembly_cpu("),
        implementation.find("cpu_synchronize_state(cpu)"),
        implementation.find("result = g_new0(WD40Disassembly, 1)"),
    )
    if min(validation_order) < 0 or tuple(sorted(validation_order)) != (
        validation_order
    ):
        raise SystemExit(
            "disas/disas-mon.c: validation must precede CPU access "
            "and result allocation"
        )

    read = implementation.find("wd40_disassembly_read_memory(pc, bytes, count,")
    allocate_instruction = implementation.find(
        "instruction = g_new0(WD40DisassembledInstruction, 1)"
    )
    append = implementation.find("*tail = entry;")
    if min(read, allocate_instruction, append) < 0 or not (
        read < allocate_instruction < append
    ):
        raise SystemExit(
            "disas/disas-mon.c: bytes must be read before a result "
            "node is published"
        )

    for offset, line in enumerate(qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/machine.json: disassembly documentation line "
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
        self.sequence = 0

        greeting = self._next_json(30)
        if not isinstance(greeting.get("QMP"), dict):
            self._abort(f"missing QMP greeting: {greeting!r}")
        require_return(
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
        identifier = f"wd40-disassembly-{self.sequence}"
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
            require_return(
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


def require_return(reply: dict[str, Any], label: str) -> Any:
    if "error" in reply:
        raise SystemExit(f"{label}: unexpected QMP error: {reply['error']!r}")
    if "return" not in reply:
        raise SystemExit(f"{label}: missing return value: {reply!r}")
    return reply["return"]


def require_error(
    reply: dict[str, Any],
    label: str,
    fragment: str,
) -> None:
    error = reply.get("error")
    if not isinstance(error, dict):
        raise SystemExit(f"{label}: expected QMP error, got {reply!r}")
    description = error.get("desc")
    if not isinstance(description, str) or fragment not in description:
        raise SystemExit(
            f"{label}: expected error containing {fragment!r}, "
            f"got {reply!r}"
        )


def validate_result(case: TargetCase, result: Any) -> None:
    if not isinstance(result, dict):
        raise SystemExit(f"{case.name}: disassembly result is not an object")
    expected = {
        "space": "physical",
        "cpu-index": 0,
        "address": case.address,
        "instruction-count": len(case.lengths),
        "bytes-consumed": sum(case.lengths),
    }
    for field, value in expected.items():
        if result.get(field) != value:
            raise SystemExit(
                f"{case.name}: {field}={result.get(field)!r}, "
                f"expected {value!r}"
            )

    instructions = result.get("instructions")
    if not isinstance(instructions, list) or len(instructions) != len(
        case.lengths
    ):
        raise SystemExit(
            f"{case.name}: unexpected instructions: {instructions!r}"
        )

    offset = 0
    encoded = ""
    for index, (instruction, length) in enumerate(
        zip(instructions, case.lengths, strict=True)
    ):
        if not isinstance(instruction, dict):
            raise SystemExit(
                f"{case.name}: instruction {index} is not an object"
            )
        if instruction.get("address") != case.address + offset:
            raise SystemExit(
                f"{case.name}: instruction {index} has wrong address"
            )
        if instruction.get("length") != length:
            raise SystemExit(
                f"{case.name}: instruction {index} has length "
                f"{instruction.get('length')!r}, expected {length}"
            )
        raw = instruction.get("bytes")
        text = instruction.get("text")
        if not isinstance(raw, str) or len(raw) != length * 2:
            raise SystemExit(
                f"{case.name}: instruction {index} has invalid bytes {raw!r}"
            )
        if (
            not isinstance(text, str)
            or not text.strip()
            or "\n" in text
            or "\r" in text
        ):
            raise SystemExit(
                f"{case.name}: instruction {index} has invalid text {text!r}"
            )
        encoded += raw
        offset += length

    if encoded != case.data:
        raise SystemExit(
            f"{case.name}: returned bytes {encoded!r}, expected {case.data!r}"
        )


def exercise_target(build_dir: Path, case: TargetCase) -> None:
    binary = build_dir / case.binary
    if not binary.is_file():
        raise SystemExit(f"{case.name}: missing binary {binary}")

    print(f"WD40 disassembly: starting {case.name} runtime checks")
    with QMPClient(binary, case.arguments) as qmp:
        commands = require_return(
            qmp.execute("query-commands"),
            f"{case.name}/query-commands",
        )
        names = {
            entry.get("name")
            for entry in commands
            if isinstance(entry, dict)
        }
        for command in (
            "x-wd40-write-memory",
            "x-wd40-disassemble",
        ):
            if command not in names:
                raise SystemExit(
                    f"{case.name}: query-commands omitted {command}"
                )

        require_return(
            qmp.execute(
                "x-wd40-write-memory",
                {
                    "space": "physical",
                    "address": case.address,
                    "data": case.data,
                },
            ),
            f"{case.name}/write",
        )
        result = require_return(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": case.address,
                    "instruction-count": len(case.lengths),
                    "max-bytes": len(case.data) // 2,
                },
            ),
            f"{case.name}/disassemble",
        )
        validate_result(case, result)

        require_error(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": case.address,
                    "instruction-count": 0,
                },
            ),
            f"{case.name}/zero-count",
            "instruction-count must be between",
        )
        require_error(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": case.address,
                    "instruction-count": MAX_INSTRUCTIONS + 1,
                },
            ),
            f"{case.name}/oversize-count",
            "instruction-count must be between",
        )
        require_error(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": case.address,
                    "instruction-count": 1,
                    "max-bytes": 0,
                },
            ),
            f"{case.name}/zero-budget",
            "max-bytes must be between",
        )
        require_error(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": case.address,
                    "instruction-count": 1,
                    "max-bytes": MAX_BYTES + 1,
                },
            ),
            f"{case.name}/oversize-budget",
            "max-bytes must be between",
        )
        require_error(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": (1 << 64) - 1,
                    "instruction-count": 1,
                    "max-bytes": 2,
                },
            ),
            f"{case.name}/wrap",
            "wraps past UINT64_MAX",
        )
        require_error(
            qmp.execute(
                "x-wd40-disassemble",
                {
                    "space": "physical",
                    "address": case.address,
                    "instruction-count": 1,
                    "max-bytes": case.lengths[0] - 1
                    if case.lengths[0] > 1
                    else 1,
                    "cpu-index": 9999,
                },
            ),
            f"{case.name}/invalid-cpu",
            "does not exist",
        )

        if case.lengths[0] > 1:
            require_error(
                qmp.execute(
                    "x-wd40-disassemble",
                    {
                        "space": "physical",
                        "address": case.address,
                        "instruction-count": 1,
                        "max-bytes": case.lengths[0] - 1,
                    },
                ),
                f"{case.name}/short-budget",
                "Could not decode",
            )

        if case.running_witness:
            require_return(
                qmp.execute("cont"),
                f"{case.name}/cont",
            )
            require_error(
                qmp.execute(
                    "x-wd40-disassemble",
                    {
                        "space": "physical",
                        "address": case.address,
                        "instruction-count": 1,
                    },
                ),
                f"{case.name}/running",
                "must be stopped",
            )
            require_return(
                qmp.execute("stop"),
                f"{case.name}/stop",
            )

    print(f"WD40 disassembly: {case.name} runtime checks passed")


def main(argv: list[str]) -> int:
    validate_static()
    if len(argv) == 1:
        print("WD40 structured disassembly static contract: PASS")
        return 0
    if len(argv) < 3:
        raise SystemExit(
            f"usage: {argv[0]} [BUILD-DIR TARGET ...]"
        )

    build_dir = Path(argv[1]).resolve()
    requested = set(argv[2:])
    known = {case.name for case in TARGETS}
    unknown = requested - known
    if unknown:
        raise SystemExit(f"unknown targets: {sorted(unknown)!r}")

    for case in TARGETS:
        if case.name in requested:
            exercise_target(build_dir, case)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
