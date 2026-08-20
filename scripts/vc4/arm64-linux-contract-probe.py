#!/usr/bin/env python3
"""Validate the AArch64 Linux entry contract on raspi3b-vc4-hetero."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import time
from typing import Any


RECORD_BASE = 0x00003000
SIGNATURE = 0x5643344C434F4E54
FDT_MAGIC = 0xD00DFEED

OFFSETS = {
    "signature": 0x00,
    "stage": 0x08,
    "x0": 0x10,
    "x1": 0x18,
    "x2": 0x20,
    "x3": 0x28,
    "sp": 0x30,
    "mpidr_el1": 0x38,
    "current_el": 0x40,
    "daif": 0x48,
    "sctlr": 0x50,
    "aux": 0x58,
    "dtb_word": 0x60,
    "flags": 0x68,
    "entry_pc": 0x70,
}

FLAG_DTB_POINTER = 1 << 0
FLAG_DTB_MAGIC = 1 << 1
FLAG_X1_X3_ZERO = 1 << 2
FLAG_EL1_OR_EL2 = 1 << 3
FLAG_MMU_OFF = 1 << 4
FLAG_UART_WRITTEN = 1 << 5
REQUIRED_FLAGS = (
    FLAG_DTB_POINTER
    | FLAG_DTB_MAGIC
    | FLAG_X1_X3_ZERO
    | FLAG_EL1_OR_EL2
    | FLAG_MMU_OFF
)


class QTestClient:
    def __init__(self, path: Path, timeout: float = 2.0) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.socket.connect(str(path))
        self.buffer = b""

    def close(self) -> None:
        self.socket.close()

    def command(self, command: str) -> str:
        self.socket.sendall(command.encode("ascii") + b"\n")
        while b"\n" not in self.buffer:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise ConnectionError("qtest socket closed")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        response = line.decode("ascii", errors="replace").strip()
        if not response.startswith("OK"):
            raise RuntimeError(f"qtest command {command!r}: {response}")
        return response[2:].strip()

    def readq(self, address: int) -> int:
        value = self.command(f"readq 0x{address:x}")
        return int(value, 0)


class QMPClient:
    def __init__(self, path: Path, timeout: float = 2.0) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.socket.connect(str(path))
        self.file = self.socket.makefile("rwb", buffering=0)
        self._read_message()  # greeting
        self.execute("qmp_capabilities")

    def close(self) -> None:
        self.file.close()
        self.socket.close()

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise ConnectionError("QMP socket closed")
            message = json.loads(line)
            if "event" in message:
                continue
            return message

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        message: dict[str, Any] = {"execute": name}
        if arguments:
            message["arguments"] = arguments
        self.file.write(json.dumps(message).encode("utf-8") + b"\n")
        response = self._read_message()
        if "error" in response:
            raise RuntimeError(f"QMP {name}: {response['error']}")
        return response.get("return")


def connect_with_retry(
    kind: type[QTestClient] | type[QMPClient],
    path: Path,
    process: subprocess.Popen[bytes],
    deadline: float,
) -> QTestClient | QMPClient:
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited with status {process.returncode}")
        try:
            return kind(path)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout) as exc:
            last_error = exc
            time.sleep(0.02)
    raise TimeoutError(f"could not connect to {path}: {last_error}")


def read_record(qtest: QTestClient) -> dict[str, int]:
    return {
        name: qtest.readq(RECORD_BASE + offset)
        for name, offset in OFFSETS.items()
    }


def tail(path: Path, count: int = 240) -> list[str]:
    if not path.is_file():
        return []
    return path.read_text(encoding="utf-8", errors="replace").splitlines()[-count:]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=10.0)
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    kernel = args.kernel.resolve()
    dtb = args.dtb.resolve()
    out_dir = args.out_dir.resolve()
    for label, path in (("QEMU", qemu), ("kernel", kernel), ("DTB", dtb)):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    qtest_path = out_dir / "qtest.sock"
    qmp_path = out_dir / "qmp.sock"
    serial_path = out_dir / "serial.log"
    stderr_path = out_dir / "qemu.stderr"
    for path in (qtest_path, qmp_path, serial_path, stderr_path):
        path.unlink(missing_ok=True)

    command = [
        str(qemu),
        "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",
        "-m", "1G",
        "-accel", "tcg,thread=single",
        "-kernel", str(kernel),
        "-dtb", str(dtb),
        "-display", "none",
        "-monitor", "none",
        "-serial", f"file:{serial_path}",
        "-qtest", f"unix:{qtest_path},server=on,wait=off",
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-no-reboot",
    ]
    (out_dir / "command.json").write_text(
        json.dumps(command, indent=2) + "\n", encoding="utf-8"
    )

    record: dict[str, int] = {}
    cpu_snapshot: Any = None
    registers: Any = None
    probe_error: str | None = None
    process: subprocess.Popen[bytes] | None = None
    qtest: QTestClient | None = None
    qmp: QMPClient | None = None
    start = time.monotonic()

    with stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
            connect_deadline = time.monotonic() + min(args.seconds, 5.0)
            qtest = connect_with_retry(
                QTestClient, qtest_path, process, connect_deadline
            )  # type: ignore[assignment]
            try:
                qmp = connect_with_retry(
                    QMPClient, qmp_path, process, connect_deadline
                )  # type: ignore[assignment]
            except Exception:
                qmp = None

            deadline = start + args.seconds
            while time.monotonic() < deadline:
                record = read_record(qtest)
                if record.get("signature") == SIGNATURE and record.get("stage", 0) >= 2:
                    break
                if process.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {process.returncode}"
                    )
                time.sleep(0.02)

            if qmp is not None:
                try:
                    cpu_snapshot = qmp.execute("query-cpus-fast")
                    registers = qmp.execute(
                        "human-monitor-command",
                        {"command-line": "info registers"},
                    )
                except Exception as exc:
                    registers = f"QMP snapshot failed: {exc}"
        except Exception as exc:
            probe_error = f"{type(exc).__name__}: {exc}"
        finally:
            if qmp is not None:
                try:
                    qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            if qtest is not None:
                qtest.close()
            if process is not None:
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2.0)

    flags = record.get("flags", 0)
    checks = {
        "signature_seen": record.get("signature") == SIGNATURE,
        "final_stage_seen": record.get("stage", 0) >= 2,
        "dtb_pointer_valid": bool(flags & FLAG_DTB_POINTER),
        "dtb_magic_valid": bool(flags & FLAG_DTB_MAGIC)
        and record.get("dtb_word") == FDT_MAGIC,
        "x1_x3_zero": bool(flags & FLAG_X1_X3_ZERO)
        and not (record.get("x1", 0) | record.get("x2", 0) | record.get("x3", 0)),
        "entry_el_valid": bool(flags & FLAG_EL1_OR_EL2)
        and (record.get("current_el", 0) & 0xC) in (0x4, 0x8),
        "mmu_disabled": bool(flags & FLAG_MMU_OFF)
        and not (record.get("sctlr", 1) & 1),
        "entry_pc_valid": record.get("entry_pc") == 0x80000,
        "uart_marker_seen": any(
            "VC4_ARM64_CONTRACT_REACHED" in line for line in tail(serial_path)
        ),
    }
    passed = probe_error is None and all(
        checks[key]
        for key in (
            "signature_seen",
            "final_stage_seen",
            "dtb_pointer_valid",
            "dtb_magic_valid",
            "x1_x3_zero",
            "entry_el_valid",
            "mmu_disabled",
            "entry_pc_valid",
        )
    )

    result = {
        "schema_version": 1,
        "passed": passed,
        "elapsed_seconds": time.monotonic() - start,
        "probe_error": probe_error,
        "qemu_returncode": process.returncode if process is not None else None,
        "record_base": f"0x{RECORD_BASE:08x}",
        "expected_signature": f"0x{SIGNATURE:016x}",
        "record": {key: f"0x{value:016x}" for key, value in record.items()},
        "checks": checks,
        "cpu_snapshot": cpu_snapshot,
        "registers": registers,
        "serial_tail": tail(serial_path),
        "qemu_stderr_tail": tail(stderr_path),
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
