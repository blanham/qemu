#!/usr/bin/env python3
"""Probe whether stock bootcode.bin has loaded and entered start.elf.

The test boots a complete firmware FAT32 image through the ``raspi3b-vc4``
development machine.  It does not inject start.elf into RAM.  After a bounded
run it records the live VPU state, snapshots ARM-visible SDRAM, and searches
for several independent windows from the exact start.elf input.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from typing import Any


class QMPError(RuntimeError):
    pass


class QMP:
    def __init__(self, path: Path, process: subprocess.Popen[bytes]) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        deadline = time.monotonic() + 15.0
        while True:
            if process.poll() is not None:
                raise QMPError(f"QEMU exited during QMP startup: {process.returncode}")
            try:
                self.socket.connect(str(path))
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() >= deadline:
                    raise QMPError("timed out connecting to QMP")
                time.sleep(0.05)
        self.file = self.socket.makefile("rwb", buffering=0)
        greeting = self._receive()
        if "QMP" not in greeting:
            raise QMPError(f"invalid QMP greeting: {greeting}")
        self.execute("qmp_capabilities")

    def _receive(self) -> dict[str, Any]:
        line = self.file.readline()
        if not line:
            raise QMPError("QMP connection closed")
        return json.loads(line)

    def execute(self, command: str, arguments: dict[str, Any] | None = None) -> Any:
        message: dict[str, Any] = {"execute": command}
        if arguments:
            message["arguments"] = arguments
        self.file.write(json.dumps(message).encode("utf-8") + b"\r\n")
        while True:
            response = self._receive()
            if "event" in response:
                continue
            if "error" in response:
                raise QMPError(f"{command}: {response['error']}")
            if "return" in response:
                return response["return"]

    def hmp(self, command: str) -> str:
        result = self.execute(
            "human-monitor-command",
            {"command-line": command, "cpu-index": 0},
        )
        return str(result)

    def close(self) -> None:
        self.file.close()
        self.socket.close()


def select_machine(qemu: Path) -> str:
    output = subprocess.check_output(
        [str(qemu), "-machine", "help"],
        text=True,
        stderr=subprocess.STDOUT,
    )
    names = {line.split()[0] for line in output.splitlines() if line.strip()}
    candidates = (
        "raspi3b-vc4",
        "raspi3b-vc4-hetero",
        "raspi3-vc4",
    )
    for candidate in candidates:
        if candidate in names:
            return candidate
    matching = sorted(name for name in names if "raspi3" in name and "vc4" in name)
    if matching:
        return matching[0]
    raise RuntimeError("no Raspberry Pi 3 VideoCore machine is registered")


def signature_windows(start_elf: bytes) -> list[tuple[int, bytes]]:
    if len(start_elf) < 4096:
        raise ValueError("start.elf is unexpectedly small")
    offsets = {
        0x200,
        0x4000,
        len(start_elf) // 4,
        len(start_elf) // 2,
        3 * len(start_elf) // 4,
        max(0, len(start_elf) - 0x1000),
    }
    windows: list[tuple[int, bytes]] = []
    for offset in sorted(offsets):
        window = start_elf[offset : offset + 64]
        if len(window) == 64 and window.count(0) < 56:
            windows.append((offset, window))
    if len(windows) < 3:
        raise ValueError("could not select enough distinctive start.elf windows")
    return windows


def locate_windows(memory: bytes, windows: list[tuple[int, bytes]]) -> list[dict[str, int]]:
    found: list[dict[str, int]] = []
    for file_offset, window in windows:
        memory_offset = memory.find(window)
        if memory_offset >= 0:
            found.append(
                {
                    "file_offset": file_offset,
                    "memory_offset": memory_offset,
                }
            )
    return found


def qom_debug_state(qmp: QMP) -> dict[str, Any]:
    result: dict[str, Any] = {}
    cpus = qmp.execute("query-cpus-fast")
    if not cpus:
        return result
    vpu = max(cpus, key=lambda entry: int(entry.get("cpu-index", -1)))
    result["cpu"] = vpu
    path = vpu.get("qom-path")
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
        except QMPError as error:
            result[prop] = {"unavailable": str(error)}
    return result


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    machine = select_machine(args.qemu)
    start_elf = args.start_elf.read_bytes()
    windows = signature_windows(start_elf)

    with tempfile.TemporaryDirectory(prefix="vc4-startelf-probe-") as temp_text:
        temp = Path(temp_text)
        qmp_path = temp / "qmp.sock"
        ram_path = temp / "ram.bin"
        stderr_path = temp / "qemu.stderr"
        command = [
            str(args.qemu),
            "-machine",
            machine,
            "-m",
            "1G",
            "-S",
            "-display",
            "none",
            "-serial",
            "none",
            "-monitor",
            "none",
            "-qmp",
            f"unix:{qmp_path},server=on,wait=off",
            "-drive",
            f"file={args.image},if=sd,format=raw",
        ]
        with stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
            )
        qmp: QMP | None = None
        report: dict[str, Any] = {
            "machine": machine,
            "command": command,
            "seconds": args.seconds,
            "windows_requested": [offset for offset, _window in windows],
        }
        try:
            qmp = QMP(qmp_path, process)
            qmp.execute("cont")
            time.sleep(args.seconds)
            report["live_vpu"] = qom_debug_state(qmp)
            report["live_status"] = qmp.execute("query-status")
            qmp.execute("stop")
            report["registers"] = qmp.hmp("cpu 4\ninfo registers")
            try:
                qmp.hmp(
                    f"pmemsave 0 {args.ram_scan_mib * 1024 * 1024:#x} {ram_path}"
                )
            except QMPError as error:
                report["pmemsave_error"] = str(error)
            if ram_path.exists():
                memory = ram_path.read_bytes()
                report["ram_bytes"] = len(memory)
                report["start_elf_matches"] = locate_windows(memory, windows)
            else:
                report["ram_bytes"] = 0
                report["start_elf_matches"] = []
        finally:
            if qmp is not None:
                qmp.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            report["qemu_returncode"] = process.returncode
            report["qemu_stderr"] = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )[-16000:]
        return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--ram-scan-mib", type=int, default=64)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--barrier-is-success", action="store_true")
    args = parser.parse_args()

    report = run_probe(args)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n", encoding="utf-8")

    matches = report.get("start_elf_matches", [])
    success = len(matches) >= 2
    if success:
        print(
            "STARTELF_EXECUTION_FRONTIER "
            f"matches={len(matches)} machine={report['machine']}"
        )
        return 0
    print(
        "STARTELF_EXECUTION_BARRIER "
        f"matches={len(matches)} machine={report['machine']}"
    )
    return 0 if args.barrier_is_success else 1


if __name__ == "__main__":
    raise SystemExit(main())
