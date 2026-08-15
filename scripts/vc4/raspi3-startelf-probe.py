#!/usr/bin/env python3
"""Probe official start.elf loading and first ARM kernel execution."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any

MARKER_ADDRESS = 0x10000000
MARKER_VALUE = 0x53544152  # "STAR"
KERNEL_LOAD_ADDRESS = 0x00080000


def movz(sf: bool, rd: int, imm16: int, shift: int = 0) -> int:
    if shift not in (0, 16, 32, 48) or (not sf and shift > 16):
        raise ValueError("invalid MOVZ shift")
    base = 0xD2800000 if sf else 0x52800000
    return (
        base
        | ((shift // 16) << 21)
        | ((imm16 & 0xFFFF) << 5)
        | rd
    )


def movk(sf: bool, rd: int, imm16: int, shift: int = 0) -> int:
    if shift not in (0, 16, 32, 48) or (not sf and shift > 16):
        raise ValueError("invalid MOVK shift")
    base = 0xF2800000 if sf else 0x72800000
    return (
        base
        | ((shift // 16) << 21)
        | ((imm16 & 0xFFFF) << 5)
        | rd
    )


def build_kernel(path: Path) -> None:
    """Create a tiny AArch64 kernel which publishes a RAM marker."""
    words = [
        movz(True, 0, (MARKER_ADDRESS >> 16) & 0xFFFF, 16),
        movz(False, 1, MARKER_VALUE & 0xFFFF),
        movk(False, 1, (MARKER_VALUE >> 16) & 0xFFFF, 16),
        0xB9000001,  # str w1, [x0]
        0x14000000,  # b .
    ]
    payload = b"".join(struct.pack("<I", word) for word in words)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload + bytes(64 * 1024 - len(payload)))
    print(
        f"created AArch64 marker kernel: path={path} "
        f"bytes={path.stat().st_size} marker=0x{MARKER_VALUE:08x}"
    )


def connect_unix(path: Path, deadline: float) -> socket.socket:
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(path))
            return sock
        except OSError as exc:
            last_error = exc
            sock.close()
            time.sleep(0.05)
    raise RuntimeError(f"could not connect to {path}: {last_error}")


class JsonLineSocket:
    def __init__(self, path: Path, deadline: float):
        self.sock = connect_unix(path, deadline)
        self.file = self.sock.makefile("rwb", buffering=0)

    def read(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            value = json.loads(line)
            if "event" in value:
                continue
            return value

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        request: dict[str, Any] = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self.file.write(json.dumps(request).encode("utf-8") + b"\n")
        response = self.read()
        if "error" in response:
            raise RuntimeError(f"QMP {command} failed: {response['error']}")
        return response.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QTestSocket:
    def __init__(self, path: Path, deadline: float):
        self.sock = connect_unix(path, deadline)
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, text: str) -> str:
        self.file.write(text.encode("ascii") + b"\n")
        line = self.file.readline().decode(
            "ascii",
            errors="replace",
        ).strip()
        if not line.startswith("OK"):
            raise RuntimeError(f"qtest command {text!r} failed: {line!r}")
        return line

    def readl(self, address: int) -> int:
        response = self.command(f"readl 0x{address:x}")
        return int(response.split()[1], 0)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def debug_vpu(qmp: JsonLineSocket) -> dict[str, Any]:
    result: dict[str, Any] = {}
    cpus = qmp.execute("query-cpus-fast")
    result["cpus"] = cpus
    vpu = None
    for cpu in cpus:
        if cpu.get("cpu-index") == 4 or "vc4" in str(cpu).lower():
            vpu = cpu
            break
    if not vpu:
        return result
    path = vpu.get("qom-path")
    result["vpu_qom_path"] = path
    if not path:
        return result
    for prop in (
        "vc4-debug-halted",
        "vc4-debug-stop",
        "vc4-debug-stopped",
        "vc4-debug-exit-request",
        "vc4-debug-thread-kicked",
        "vc4-debug-hard-interrupt",
        "vc4-debug-has-work",
    ):
        try:
            result[prop] = qmp.execute(
                "qom-get",
                {"path": path, "property": prop},
            )
        except RuntimeError as exc:
            result[prop] = str(exc)
    return result


def probe(qemu: Path, image: Path, seconds: float, log_path: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="vc4-startelf-") as temp:
        root = Path(temp)
        qmp_path = root / "qmp.sock"
        qtest_path = root / "qtest.sock"
        command = [
            str(qemu),
            "-M",
            "raspi3b-vc4-hetero",
            "-m",
            "1G",
            "-smp",
            "5",
            "-accel",
            "tcg,thread=single",
            "-S",
            "-drive",
            f"file={image},if=sd,format=raw",
            "-display",
            "none",
            "-serial",
            "none",
            "-monitor",
            "none",
            "-no-reboot",
            "-qmp",
            f"unix:{qmp_path},server=on,wait=off",
            "-qtest",
            f"unix:{qtest_path},server=on,wait=off",
        ]
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            qmp: JsonLineSocket | None = None
            qtest: QTestSocket | None = None
            try:
                deadline = time.monotonic() + 20
                qmp = JsonLineSocket(qmp_path, deadline)
                greeting = qmp.read()
                if "QMP" not in greeting:
                    raise RuntimeError(f"bad QMP greeting: {greeting}")
                qmp.execute("qmp_capabilities")
                qtest = QTestSocket(qtest_path, deadline)

                # This is intentionally the final control-plane request before
                # the passive interval. Neither qtest nor QMP can accidentally
                # act as the single-threaded TCG scheduler during the probe.
                qmp.execute("cont")
                time.sleep(seconds)

                marker = qtest.readl(MARKER_ADDRESS)
                kernel_word = qtest.readl(KERNEL_LOAD_ADDRESS)
                global_timer = qtest.readl(0x3F003004)
                qmp.execute("stop")
                state = debug_vpu(qmp)
                status = qmp.execute("query-status")
                expected_kernel_word = movz(
                    True,
                    0,
                    (MARKER_ADDRESS >> 16) & 0xFFFF,
                    16,
                )
                record = {
                    "marker": marker,
                    "expected_marker": MARKER_VALUE,
                    "kernel_word": kernel_word,
                    "expected_kernel_word": expected_kernel_word,
                    "global_timer_low": global_timer,
                    "status": status,
                    "vpu": state,
                }
                print(
                    "STARTELF_PROBE "
                    + json.dumps(record, sort_keys=True)
                )
                if marker == MARKER_VALUE:
                    print(
                        "Official start.elf loaded and executed kernel8.img."
                    )
                    return 0
                if kernel_word == expected_kernel_word:
                    print(
                        "start.elf loaded kernel8.img but ARM did not "
                        "execute it."
                    )
                    return 3
                print("start.elf has not yet loaded kernel8.img.")
                return 2
            finally:
                if qmp is not None:
                    try:
                        qmp.close()
                    except OSError:
                        pass
                if qtest is not None:
                    try:
                        qtest.close()
                    except OSError:
                        pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                elif process.returncode not in (0, None):
                    print(f"QEMU exited with status {process.returncode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    kernel = subparsers.add_parser("make-kernel")
    kernel.add_argument("output", type=Path)

    run = subparsers.add_parser("probe")
    run.add_argument("qemu", type=Path)
    run.add_argument("image", type=Path)
    run.add_argument("--seconds", type=float, default=90)
    run.add_argument(
        "--log",
        type=Path,
        default=Path("startelf-qemu.log"),
    )

    args = parser.parse_args()
    if args.command == "make-kernel":
        build_kernel(args.output)
        return 0
    return probe(args.qemu, args.image, args.seconds, args.log)


if __name__ == "__main__":
    raise SystemExit(main())
