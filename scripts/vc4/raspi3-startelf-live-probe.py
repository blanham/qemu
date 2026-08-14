#!/usr/bin/env python3
"""Passively capture the live VC4/start.elf frontier before stopping QEMU."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from typing import Any

MARKER_ADDRESS = 0x10000000
MARKER_VALUE = 0x53544152
KERNEL_LOAD_ADDRESS = 0x00080000
BOOTCODE_ENTRY = 0x200
BOOTCODE_SIZE = 52624


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


class QMP:
    def __init__(self, path: Path, deadline: float):
        self.sock = connect_unix(path, deadline)
        self.file = self.sock.makefile("rwb", buffering=0)

    def read(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            value = json.loads(line)
            if "event" not in value:
                return value

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
        request: dict[str, Any] = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self.file.write(json.dumps(request).encode() + b"\n")
        response = self.read()
        if "error" in response:
            raise RuntimeError(f"QMP {command}: {response['error']}")
        return response.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QTest:
    def __init__(self, path: Path, deadline: float):
        self.sock = connect_unix(path, deadline)
        self.file = self.sock.makefile("rwb", buffering=0)

    def readl(self, address: int) -> int:
        self.file.write(f"readl 0x{address:x}\n".encode())
        response = self.file.readline().decode(errors="replace").strip()
        if not response.startswith("OK"):
            raise RuntimeError(f"qtest readl failed: {response}")
        return int(response.split()[1], 0)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def find_vpu(qmp: QMP) -> dict[str, Any]:
    cpus = qmp.execute("query-cpus-fast")
    for cpu in cpus:
        if cpu.get("cpu-index") == 4 or "vc4" in str(cpu).lower():
            return cpu
    raise RuntimeError(f"VC4 CPU not found in {cpus!r}")


def live_state(qmp: QMP) -> dict[str, Any]:
    cpu = find_vpu(qmp)
    result: dict[str, Any] = {"cpu": cpu}
    qom_path = cpu.get("qom-path")
    result["qom_path"] = qom_path
    for prop in (
        "vc4-debug-halted",
        "vc4-debug-stop",
        "vc4-debug-stopped",
        "vc4-debug-exit-request",
        "vc4-debug-thread-kicked",
        "vc4-debug-hard-interrupt",
        "vc4-debug-has-work",
    ):
        if not qom_path:
            result[prop] = "missing qom-path"
            continue
        try:
            result[prop] = qmp.execute(
                "qom-get",
                {"path": qom_path, "property": prop},
            )
        except RuntimeError as exc:
            result[prop] = str(exc)
    try:
        result["registers"] = qmp.execute(
            "human-monitor-command",
            {"command-line": "info registers", "cpu-index": 4},
        )
    except RuntimeError as exc:
        result["registers"] = str(exc)
    result["status"] = qmp.execute("query-status")
    return result


def run_probe(
    qemu: Path,
    image: Path,
    seconds: float,
    qemu_log: Path,
    result_path: Path,
) -> int:
    with tempfile.TemporaryDirectory(prefix="vc4-startelf-live-") as temp:
        temp_path = Path(temp)
        qmp_path = temp_path / "qmp.sock"
        qtest_path = temp_path / "qtest.sock"
        command = [
            str(qemu),
            "-M", "raspi3b-vc4",
            "-m", "1G",
            "-accel", "tcg,thread=single",
            "-S",
            "-drive", f"file={image},if=sd,format=raw",
            "-display", "none",
            "-serial", "none",
            "-monitor", "none",
            "-no-reboot",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]
        qemu_log.parent.mkdir(parents=True, exist_ok=True)
        with qemu_log.open("wb") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            qmp: QMP | None = None
            qtest: QTest | None = None
            try:
                deadline = time.monotonic() + 20
                qmp = QMP(qmp_path, deadline)
                greeting = qmp.read()
                if "QMP" not in greeting:
                    raise RuntimeError(f"bad QMP greeting: {greeting}")
                qmp.execute("qmp_capabilities")
                qtest = QTest(qtest_path, deadline)

                # No monitor or qtest traffic is sent after this command until
                # the complete passive observation interval has elapsed.
                qmp.execute("cont")
                time.sleep(seconds)

                # Capture scheduler and register state while the VM is still
                # running. qmp stop must not contaminate these fields.
                state = live_state(qmp)
                marker = qtest.readl(MARKER_ADDRESS)
                kernel_word = qtest.readl(KERNEL_LOAD_ADDRESS)
                timer_low = qtest.readl(0x3F003004)
                qmp.execute("stop")

                record = {
                    "marker": marker,
                    "expected_marker": MARKER_VALUE,
                    "kernel_word": kernel_word,
                    "global_timer_low": timer_low,
                    "bootcode_range": [
                        BOOTCODE_ENTRY,
                        BOOTCODE_ENTRY + BOOTCODE_SIZE,
                    ],
                    "live_vpu": state,
                }
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_text(
                    json.dumps(record, indent=2, sort_keys=True) + "\n"
                )
                print(
                    "STARTELF_LIVE_FRONTIER "
                    + json.dumps(record, sort_keys=True)
                )
                if marker == MARKER_VALUE:
                    return 0
                return 2
            finally:
                if qmp is not None:
                    qmp.close()
                if qtest is not None:
                    qtest.close()
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
    parser.add_argument("qemu", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("--seconds", type=float, default=150)
    parser.add_argument(
        "--qemu-log",
        type=Path,
        default=Path("startelf-live-qemu.log"),
    )
    parser.add_argument(
        "--result",
        type=Path,
        default=Path("startelf-live-frontier.json"),
    )
    args = parser.parse_args()
    return run_probe(
        args.qemu,
        args.image,
        args.seconds,
        args.qemu_log,
        args.result,
    )


if __name__ == "__main__":
    raise SystemExit(main())
