#!/usr/bin/env python3
"""Run and evaluate a controlled VC4-to-ARM scheduler wake-up experiment.

The stock-image probe deliberately uses the firmware SD path and qtest rather
than QEMU's -kernel shortcut.  The materializer adds qemu_cpu_kick() only after
code explicitly changes a CPU from halted to runnable.  Callers must compare a
clean baseline with the candidate and must not retain the patch unless the
real firmware payload signature appears only with the candidate.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any

SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21  # "VC4_ARM!"
ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110
KERNEL_LOAD_ADDR = 0x00080000


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, command: str) -> list[str]:
        self.file.write(command.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest closed during {command!r}")
        fields = reply.decode("ascii", errors="replace").strip().split()
        if not fields or fields[0] != "OK":
            raise RuntimeError(f"qtest rejected {command!r}: {fields!r}")
        return fields

    def readl(self, address: int) -> int:
        fields = self.command(f"readl 0x{address:x}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)

    def readq(self, address: int) -> int:
        fields = self.command(f"readq 0x{address:x}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QMP:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"invalid QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP socket closed")
            message = json.loads(line)
            if "event" not in message:
                return message

    def execute(self, command: str,
                arguments: dict[str, Any] | None = None) -> Any:
        request: dict[str, Any] = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self.file.write(json.dumps(request).encode("utf-8") + b"\n")
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

    def hmp(self, command: str, *, cpu_index: int | None = None) -> str:
        arguments: dict[str, Any] = {"command-line": command}
        if cpu_index is not None:
            arguments["cpu-index"] = cpu_index
        result = self.execute("human-monitor-command", arguments)
        return "" if result is None else str(result)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_connection(path: Path, proc: subprocess.Popen[bytes],
                        kind: str, timeout: float = 15.0) -> QTest | QMP:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with {proc.returncode}")
        try:
            return QMP(path) if kind == "qmp" else QTest(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.02)
    raise TimeoutError(f"{kind} socket unavailable: {path}") from last_error


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def cpu_snapshot(qmp: QMP) -> dict[str, Any]:
    cpus = qmp.execute("query-cpus-fast")
    records: list[dict[str, Any]] = []
    if isinstance(cpus, list):
        for item in cpus:
            if not isinstance(item, dict):
                continue
            index = item.get("cpu-index")
            registers = ""
            if isinstance(index, int):
                try:
                    registers = qmp.hmp("info registers", cpu_index=index)
                except Exception as exc:
                    registers = f"register query failed: {exc}"
            records.append({
                "cpu_index": index,
                "qom_type": item.get("qom-type"),
                "thread_id": item.get("thread-id"),
                "halted": item.get("halted"),
                "registers": registers,
            })
    return {
        "query_cpus_fast": cpus,
        "cpus": records,
        "info_cpus": qmp.hmp("info cpus"),
    }


def probe(args: argparse.Namespace) -> int:
    qemu = args.qemu.resolve()
    image = args.image.resolve()
    if not qemu.is_file():
        raise SystemExit(f"not a QEMU executable: {qemu}")
    if not image.is_file():
        raise SystemExit(f"not a stock SD image: {image}")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vc4-scheduler-ab-") as tmp_s:
        tmp = Path(tmp_s)
        qtest_path = tmp / "qtest.sock"
        qmp_path = tmp / "qmp.sock"
        stderr_path = args.out.with_suffix(".stderr")
        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image},format=raw,if=sd",
            "-accel", f"tcg,thread={args.thread_mode}",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-no-reboot",
            "-d", "guest_errors,unimp",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]
        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=stderr
            )

        qtest: QTest | None = None
        qmp: QMP | None = None
        started = time.monotonic()
        result: dict[str, Any] = {
            "schema_version": 1,
            "qemu": str(qemu),
            "image": str(image),
            "thread_mode": args.thread_mode,
            "seconds_requested": args.seconds,
            "signature_address": f"0x{SIGNATURE_ADDR:08x}",
            "expected_signature": f"0x{SIGNATURE:016x}",
            "signature_seen": False,
            "qemu_command": command,
        }
        try:
            qmp_obj = wait_for_connection(qmp_path, proc, "qmp")
            qtest_obj = wait_for_connection(qtest_path, proc, "qtest")
            assert isinstance(qmp_obj, QMP)
            assert isinstance(qtest_obj, QTest)
            qmp = qmp_obj
            qtest = qtest_obj

            initial_kernel_word = qtest.readq(KERNEL_LOAD_ADDR)
            deadline = time.monotonic() + args.seconds
            signature = 0
            while time.monotonic() < deadline:
                signature = qtest.readq(SIGNATURE_ADDR)
                if signature == SIGNATURE:
                    result["signature_seen"] = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)

            try:
                qmp.execute("stop")
            except Exception:
                pass

            result.update({
                "observed_signature": f"0x{signature:016x}",
                "elapsed_seconds": time.monotonic() - started,
                "qemu_returncode": proc.poll(),
                "kernel_word_before_boot":
                    f"0x{initial_kernel_word:016x}",
                "kernel_word_after_boot":
                    f"0x{qtest.readq(KERNEL_LOAD_ADDR):016x}",
                "payload_argument_x0":
                    f"0x{qtest.readq(SIGNATURE_ADDR + 8):016x}",
                "payload_initial_sp":
                    f"0x{qtest.readq(SIGNATURE_ADDR + 16):016x}",
                "payload_mpidr_el1":
                    f"0x{qtest.readq(SIGNATURE_ADDR + 24):016x}",
                "arm_control0": f"0x{qtest.readl(ARM_CONTROL0):08x}",
                "arm_control1": f"0x{qtest.readl(ARM_CONTROL1):08x}",
                "arm_status": f"0x{qtest.readl(ARM_STATUS):08x}",
                "arm_id": f"0x{qtest.readl(ARM_ID):08x}",
                "pm_proc": f"0x{qtest.readl(PM_PROC):08x}",
                "cpu_snapshot": cpu_snapshot(qmp),
            })
        except Exception as exc:
            result["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()
            stop_process(proc)

        stderr_text = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
        result["stderr_tail"] = stderr_text.splitlines()[-500:]
        args.out.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result.get("signature_seen") else 2


@dataclass(frozen=True)
class Edit:
    path: str
    line: int
    cpu_expression: str
    function: str | None


HALTED_RE = re.compile(
    r"^(?P<indent>\s*)(?P<expr>"
    r"(?:CPU\([^;]+\)|[A-Za-z_][A-Za-z0-9_]*(?:\[[^\]]+\])?"
    r"|[A-Za-z_][A-Za-z0-9_]*->[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\[[^\]]+\])?))"
    r"->halted\s*=\s*(?:false|0)\s*;(?P<tail>.*)$"
)
FUNCTION_RE = re.compile(
    r"^\s*(?:static\s+)?(?:inline\s+)?(?:[A-Za-z_][A-Za-z0-9_]*"
    r"(?:\s+|\s*\*\s*))+"
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*\)\s*$"
)


def interesting_file(path: Path, text: str) -> bool:
    if path.suffix != ".c":
        return False
    if not any(part in {"arm", "misc", "core"} for part in path.parts):
        return False
    lower = text.lower()
    return (
        "raspi3b-vc4-hetero" in lower
        or "vc4" in lower
        or "bcm283" in lower
        or "arm_control" in lower
        or "armctrl" in lower
        or "cpu_reset" in lower
    )


def add_cpu_header(text: str) -> str:
    header = '#include "system/cpus.h"\n'
    if header in text:
        return text
    includes = list(re.finditer(r'^#include [^\n]+\n', text, re.M))
    if not includes:
        raise RuntimeError("could not find include block")
    position = includes[-1].end()
    return text[:position] + header + text[position:]


def materialize(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    edits: list[Edit] = []
    changed: list[str] = []

    candidates = sorted(
        p for directory in (root / "hw" / "arm",
                            root / "hw" / "misc",
                            root / "hw" / "core")
        if directory.is_dir()
        for p in directory.glob("*.c")
    )
    for path in candidates:
        original = path.read_text(encoding="utf-8")
        if not interesting_file(path, original):
            continue
        lines = original.splitlines(keepends=True)
        output: list[str] = []
        function: str | None = None
        file_edits: list[Edit] = []
        for line_number, line in enumerate(lines, 1):
            stripped = line.rstrip("\n")
            match_function = FUNCTION_RE.match(stripped)
            if match_function:
                function = match_function.group("name")
            output.append(line)
            match = HALTED_RE.match(stripped)
            if not match:
                continue
            expr = match.group("expr").strip()
            following = "".join(lines[line_number:line_number + 3])
            if f"qemu_cpu_kick({expr})" in following:
                continue
            output.append(
                f"{match.group('indent')}qemu_cpu_kick({expr});\n"
            )
            file_edits.append(Edit(
                path=str(path.relative_to(root)),
                line=line_number,
                cpu_expression=expr,
                function=function,
            ))

        if not file_edits:
            continue
        updated = "".join(output)
        updated = add_cpu_header(updated)
        path.write_text(updated, encoding="utf-8")
        edits.extend(file_edits)
        changed.append(str(path.relative_to(root)))

    result = {
        "schema_version": 1,
        "changed_files": changed,
        "edits": [edit.__dict__ for edit in edits],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    if not edits:
        return 3
    return 0


def compare(args: argparse.Namespace) -> int:
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    baseline_seen = bool(baseline.get("signature_seen"))
    candidate_seen = bool(candidate.get("signature_seen"))
    result = {
        "schema_version": 1,
        "baseline": baseline,
        "candidate": candidate,
        "baseline_signature_seen": baseline_seen,
        "candidate_signature_seen": candidate_seen,
        "improved": candidate_seen and not baseline_seen,
        "regressed": baseline_seen and not candidate_seen,
    }
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["improved"] else 4


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    probe_parser = subparsers.add_parser("probe")
    probe_parser.add_argument("--qemu", required=True, type=Path)
    probe_parser.add_argument("--image", required=True, type=Path)
    probe_parser.add_argument("--out", required=True, type=Path)
    probe_parser.add_argument(
        "--thread-mode", choices=("single", "multi"), default="single"
    )
    probe_parser.add_argument("--seconds", type=float, default=180.0)
    probe_parser.set_defaults(func=probe)

    materialize_parser = subparsers.add_parser("materialize")
    materialize_parser.add_argument("--root", type=Path, default=Path("."))
    materialize_parser.add_argument("--out", required=True, type=Path)
    materialize_parser.set_defaults(func=materialize)

    compare_parser = subparsers.add_parser("compare")
    compare_parser.add_argument("--baseline", required=True, type=Path)
    compare_parser.add_argument("--candidate", required=True, type=Path)
    compare_parser.add_argument("--out", required=True, type=Path)
    compare_parser.set_defaults(func=compare)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "seconds", 1) <= 0:
        parser.error("--seconds must be positive")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
