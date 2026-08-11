#!/usr/bin/env python3
"""Verify translation-block isolation between linked AArch64 and VC4 CPUs."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from typing import Any

MARKER_ADDR = 0x1000
MARKER_VALUE = 0x4A11C0DE
STATUS_BASE = 0x10000000
STATUS_ARM_HALTED = 1 << 0
STATUS_VC4_HALTED = 1 << 1


class LineSocket:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def send_line(self, line: str) -> str:
        self.file.write(line.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"socket closed while waiting for {line!r}")
        return reply.decode("ascii", errors="replace").strip()

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
            raise RuntimeError(f"invalid QMP greeting: {greeting}")
        self.execute("qmp_capabilities")

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP socket closed")
            message = json.loads(line)
            if "event" not in message:
                return message

    def execute(self, command: str) -> Any:
        payload = json.dumps({"execute": command}).encode("utf-8") + b"\n"
        self.file.write(payload)
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_socket(path: Path, proc: subprocess.Popen[bytes],
                    timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        time.sleep(0.01)
    raise TimeoutError(f"socket did not appear: {path}")


def parse_qtest_value(reply: str) -> int:
    fields = reply.split()
    if len(fields) != 2 or fields[0] != "OK":
        raise RuntimeError(f"unexpected qtest reply: {reply!r}")
    return int(fields[1], 0)


def validate_cpu_topology(cpus: Any) -> list[str]:
    if not isinstance(cpus, list) or len(cpus) != 2:
        raise RuntimeError(f"expected two heterogeneous CPUs, got {cpus!r}")

    qom_types = [str(cpu.get("qom-type", "")) for cpu in cpus
                 if isinstance(cpu, dict)]
    if len(qom_types) != 2:
        raise RuntimeError(f"malformed query-cpus-fast response: {cpus!r}")
    if not any("cortex-a53" in qom_type for qom_type in qom_types):
        raise RuntimeError(f"missing Cortex-A53 CPU: {qom_types!r}")
    if not any("vc4" in qom_type for qom_type in qom_types):
        raise RuntimeError(f"missing VC4 CPU: {qom_types!r}")
    return qom_types


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-hetero-") as tmp_s:
        tmp = Path(tmp_s)
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"

        command = [
            str(qemu),
            "-M", "vc4-hetero-smoke",
            "-m", "16M",
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-S",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        try:
            wait_for_socket(qmp_path, proc, 5.0)
            wait_for_socket(qtest_path, proc, 5.0)
            qmp = QMP(qmp_path)
            qtest = LineSocket(qtest_path)

            qom_types = validate_cpu_topology(qmp.execute("query-cpus-fast"))
            qmp.execute("cont")

            deadline = time.monotonic() + 5.0
            marker = 0
            status = 0
            while time.monotonic() < deadline:
                marker = parse_qtest_value(
                    qtest.send_line(f"readl 0x{MARKER_ADDR:x}")
                )
                status = parse_qtest_value(
                    qtest.send_line(f"readl 0x{STATUS_BASE:x}")
                )
                if marker == MARKER_VALUE and status == STATUS_VC4_HALTED:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if marker != MARKER_VALUE:
                raise RuntimeError(
                    "AArch64 payload did not execute independently: "
                    f"marker=0x{marker:08x}, expected=0x{MARKER_VALUE:08x}"
                )
            if status & STATUS_ARM_HALTED:
                raise RuntimeError(
                    "AArch64 CPU incorrectly halted while executing the "
                    f"same-PC polyglot: status=0x{status:08x}"
                )
            if not (status & STATUS_VC4_HALTED):
                raise RuntimeError(
                    "VC4 CPU did not execute its HALT decoding of the "
                    f"same-PC polyglot: status=0x{status:08x}"
                )

            print(
                "Linked frontend isolation passed: "
                f"qom-types={','.join(sorted(qom_types))} "
                f"marker=0x{marker:08x} status=0x{status:08x}"
            )
            qmp.execute("quit")
            proc.wait(timeout=5)
            return 0
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if diagnostics:
                print("--- qemu stderr ---", file=os.sys.stderr)
                print(diagnostics, file=os.sys.stderr)
            raise
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()


if __name__ == "__main__":
    raise SystemExit(main())
