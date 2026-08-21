#!/usr/bin/env python3
"""Prove or precisely classify the VC4-to-AArch64 stock-firmware handoff.

The probe builds a verified FAT32 boot volume containing an unchanged pinned
Raspberry Pi firmware trio, a matching DTB, config.txt, and a freestanding
kernel8.img.  It then runs the heterogeneous Pi 3 machine and observes guest
physical memory through qtest.  No QEMU ``-kernel`` shortcut is used.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
from types import ModuleType
from typing import Any, Mapping

SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21
PAYLOAD_LOAD_ADDR = 0x00080000
RECORD_SIZE = 48

ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110

ILLEGAL_RE = re.compile(
    r"VideoCore IV: unimplemented opcode 0x([0-9a-fA-F]+) "
    r"at 0x([0-9a-fA-F]+)"
)
FINAL_ILLEGAL_RE = re.compile(
    r"VideoCore IV: illegal instruction at 0x([0-9a-fA-F]+)"
)
DIAGNOSTIC_FRAGMENTS = (
    "unimplemented opcode",
    "illegal instruction",
    "bad read offset",
    "bad write offset",
    "unknown offset",
    "could not enter swi",
    "exception nesting overflow",
    "arm cpu0 release failed",
    "pm_proc",
    "reqstop",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    return {
        "path": str(path),
        "size": path.stat().st_size,
        "sha256": sha256(path),
    }


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load Python module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QMP:
    def __init__(self, path: Path) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile("rwb", buffering=0)
        greeting = self._read()
        if "QMP" not in greeting:
            raise RuntimeError(f"invalid QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def _read(self) -> dict[str, Any]:
        while True:
            line = self.stream.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            message = json.loads(line)
            if "event" not in message:
                return message

    def execute(
        self,
        command: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> Any:
        request: dict[str, Any] = {"execute": command}
        if arguments:
            request["arguments"] = dict(arguments)
        self.stream.write(json.dumps(request).encode("utf-8") + b"\n")
        response = self._read()
        if "error" in response:
            raise RuntimeError(f"QMP {command} failed: {response['error']!r}")
        return response.get("return")

    def hmp(self, command: str, *, cpu_index: int | None = None) -> str:
        arguments: dict[str, Any] = {"command-line": command}
        if cpu_index is not None:
            arguments["cpu-index"] = cpu_index
        result = self.execute("human-monitor-command", arguments)
        return "" if result is None else str(result)

    def close(self) -> None:
        self.stream.close()
        self.socket.close()


class QTest:
    def __init__(self, path: Path) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile("rwb", buffering=0)

    def command(self, command: str) -> str:
        self.stream.write(command.encode("ascii") + b"\n")
        line = self.stream.readline()
        if not line:
            raise RuntimeError(f"qtest connection closed after {command!r}")
        response = line.decode("ascii", errors="replace").strip()
        if not response.startswith("OK"):
            raise RuntimeError(f"qtest {command!r} failed: {response!r}")
        return response

    @staticmethod
    def _value(response: str) -> int:
        fields = response.split()
        if len(fields) != 2 or fields[0] != "OK":
            raise RuntimeError(
                f"unexpected qtest scalar response: {response!r}"
            )
        return int(fields[1], 0)

    def readl(self, address: int) -> int:
        return self._value(self.command(f"readl 0x{address:x}"))

    def readq(self, address: int) -> int:
        return self._value(self.command(f"readq 0x{address:x}"))

    def words(self, address: int, count: int) -> list[str]:
        return [
            f"0x{self.readl(address + index * 4):08x}"
            for index in range(count)
        ]

    def close(self) -> None:
        self.stream.close()
        self.socket.close()


def connect_until(
    path: Path,
    process: subprocess.Popen[bytes],
    constructor: Any,
    timeout: float,
) -> Any:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"QEMU exited early with status {process.returncode}"
            )
        try:
            return constructor(path)
        except OSError as error:
            last_error = error
            time.sleep(0.01)
    raise TimeoutError(
        f"socket did not accept connections: {path}"
    ) from last_error


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def unique_diagnostic_tail(text: str, limit: int = 96) -> list[str]:
    selected_reversed: list[str] = []
    seen: set[str] = set()
    for raw_line in reversed(text.splitlines()):
        line = raw_line.strip()
        lowered = line.lower()
        if not line or not any(
            fragment in lowered for fragment in DIAGNOSTIC_FRAGMENTS
        ):
            continue
        if line in seen:
            continue
        seen.add(line)
        selected_reversed.append(line)
        if len(selected_reversed) == limit:
            break
    return list(reversed(selected_reversed))


def read_logs(log_path: Path, stderr_path: Path) -> tuple[str, str, str]:
    qemu_log = (
        log_path.read_text(encoding="utf-8", errors="replace")
        if log_path.is_file()
        else ""
    )
    stderr = (
        stderr_path.read_text(encoding="utf-8", errors="replace")
        if stderr_path.is_file()
        else ""
    )
    return qemu_log, stderr, qemu_log + "\n" + stderr


def cpu_snapshot(qmp: QMP) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    raw_cpus = qmp.execute("query-cpus-fast")
    if not isinstance(raw_cpus, list):
        raise RuntimeError(f"query-cpus-fast returned {raw_cpus!r}")

    cpus: list[dict[str, Any]] = []
    registers: dict[str, str] = {}
    for raw in raw_cpus:
        if not isinstance(raw, dict):
            continue
        cpu = dict(raw)
        index = cpu.get("cpu-index")
        cpus.append(cpu)
        if isinstance(index, int):
            try:
                registers[str(index)] = qmp.hmp(
                    "info registers", cpu_index=index
                )
            # Powered-off CPU frontends may reject register HMP commands.
            except Exception as error:
                registers[str(index)] = f"unavailable: {error}"

    return cpus, registers, qmp.hmp("info cpus")


def build_volume(
    image: Path,
    bootcode: Path,
    start_elf: Path,
    fixup_dat: Path,
    dtb: Path,
    payload: Path,
    stock_probe: ModuleType,
) -> dict[str, list[int]]:
    config = (
        "arm_64bit=1\n"
        "kernel=kernel8.img\n"
        "device_tree=rpi3.dtb\n"
        "enable_uart=1\n"
        "disable_splash=1\n"
    ).encode("ascii")
    cmdline = b"console=serial0,115200 earlycon=pl011,0x3f201000\n"
    files = [
        ("BOOTCODE.BIN", bootcode.read_bytes()),
        ("START.ELF", start_elf.read_bytes()),
        ("FIXUP.DAT", fixup_dat.read_bytes()),
        ("CONFIG.TXT", config),
        ("CMDLINE.TXT", cmdline),
        ("RPI3.DTB", dtb.read_bytes()),
        ("KERNEL8.IMG", payload.read_bytes()),
    ]
    raw_layout = stock_probe.build_fat32_image(image, files)
    return {name: list(chain) for name, chain in raw_layout.items()}


def arm_control_snapshot(qtest: QTest) -> dict[str, str]:
    registers = {
        "arm_control0": ARM_CONTROL0,
        "arm_control1": ARM_CONTROL1,
        "arm_status": ARM_STATUS,
        "arm_id": ARM_ID,
        "pm_proc": PM_PROC,
    }
    return {
        name: f"0x{qtest.readl(address):08x}"
        for name, address in registers.items()
    }


def write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("payload", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be positive")

    paths = {
        "qemu": args.qemu.resolve(),
        "bootcode.bin": args.bootcode.resolve(),
        "start.elf": args.start_elf.resolve(),
        "fixup.dat": args.fixup_dat.resolve(),
        "rpi3.dtb": args.dtb.resolve(),
        "kernel8.img": args.payload.resolve(),
    }
    for name, path in paths.items():
        if not path.is_file():
            parser.error(f"{name} is not a file: {path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    image = out_dir / "vc4-arm-payload.img"
    qmp_path = out_dir / "qmp.sock"
    qtest_path = out_dir / "qtest.sock"
    log_path = out_dir / "qemu.log"
    stderr_path = out_dir / "qemu.stderr"
    status_path = out_dir / "VC4_ARM_PAYLOAD_STATUS.json"
    frontier_path = out_dir / "VC4_ARM_PAYLOAD_FRONTIER.json"
    for transient in (qmp_path, qtest_path, log_path, stderr_path):
        transient.unlink(missing_ok=True)

    stock_probe = load_module(
        Path(__file__).with_name("raspi3-stock-bootcode-probe.py"),
        "vc4_stock_bootcode_for_arm_payload",
    )

    status: dict[str, Any] = {
        "schema_version": 1,
        "probe_completed": False,
        "signature_expected": f"0x{SIGNATURE:016x}",
        "signature_seen": False,
        "payload_load_address": f"0x{PAYLOAD_LOAD_ADDR:08x}",
        "files": {name: file_record(path) for name, path in paths.items()},
        "integration_sha": os.environ.get("GITHUB_SHA"),
        "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
    }
    frontier: dict[str, Any] = {
        "schema_version": 1,
        "signature_expected": f"0x{SIGNATURE:016x}",
    }

    process: subprocess.Popen[bytes] | None = None
    qmp: QMP | None = None
    qtest: QTest | None = None
    started = time.monotonic()
    error_text: str | None = None

    try:
        layout = build_volume(
            image,
            paths["bootcode.bin"],
            paths["start.elf"],
            paths["fixup.dat"],
            paths["rpi3.dtb"],
            paths["kernel8.img"],
            stock_probe,
        )
        status["fat32_layout"] = layout
        status["boot_image"] = file_record(image)

        command = [
            str(paths["qemu"]),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-serial", "none",
            "-monitor", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-d", "unimp,guest_errors,int",
            "-D", str(log_path),
            "-no-reboot",
        ]
        status["command"] = command
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp = connect_until(qmp_path, process, QMP, 12.0)
        qtest = connect_until(qtest_path, process, QTest, 12.0)

        deadline = time.monotonic() + args.seconds
        observed = 0
        while time.monotonic() < deadline:
            observed = qtest.readq(SIGNATURE_ADDR)
            if observed == SIGNATURE:
                status["signature_seen"] = True
                break
            if process.poll() is not None:
                break
            time.sleep(0.02)

        status["signature_observed"] = f"0x{observed:016x}"
        status["elapsed_seconds"] = round(time.monotonic() - started, 6)

        if process.poll() is None:
            qmp.execute("stop")
            for _ in range(100):
                state = qmp.execute("query-status")
                if isinstance(state, dict) and not state.get("running", True):
                    break
                time.sleep(0.01)

        record = [
            qtest.readq(SIGNATURE_ADDR + offset)
            for offset in range(0, RECORD_SIZE, 8)
        ]
        status["record"] = {
            "signature": f"0x{record[0]:016x}",
            "mpidr_el1": f"0x{record[1]:016x}",
            "initial_sp": f"0x{record[2]:016x}",
            "x0": f"0x{record[3]:016x}",
            "current_el": f"0x{record[4]:016x}",
            "sctlr_el1": f"0x{record[5]:016x}",
        }
        status["arm_control"] = arm_control_snapshot(qtest)
        cpus, registers, cpu_summary = cpu_snapshot(qmp)
        status["cpus"] = cpus
        status["cpu_registers"] = registers
        status["cpu_summary"] = cpu_summary
        status["memory_words"] = {
            "reset_vector": qtest.words(0, 16),
            "payload_entry": qtest.words(PAYLOAD_LOAD_ADDR, 16),
            "signature_record": qtest.words(SIGNATURE_ADDR, RECORD_SIZE // 4),
        }
        status["qemu_returncode"] = process.poll()
        status["probe_completed"] = True
    except Exception as error:
        error_text = f"{type(error).__name__}: {error}"
        status["error"] = error_text
    finally:
        if qmp is not None and process is not None and process.poll() is None:
            try:
                qmp.execute("quit")
                process.wait(timeout=3)
            except Exception:
                stop_process(process)
        elif process is not None:
            stop_process(process)
        if qtest is not None:
            qtest.close()
        if qmp is not None:
            qmp.close()
        qmp_path.unlink(missing_ok=True)
        qtest_path.unlink(missing_ok=True)

        qemu_log, stderr, combined = read_logs(log_path, stderr_path)
        status["qemu_log_size"] = len(qemu_log.encode("utf-8"))
        status["qemu_stderr_size"] = len(stderr.encode("utf-8"))
        status["diagnostics"] = unique_diagnostic_tail(combined)
        if process is not None:
            status["qemu_returncode"] = process.poll()

        write_json(status_path, status)

        if not status.get("signature_seen", False):
            illegal_matches = list(ILLEGAL_RE.finditer(combined))
            final_illegal = list(FINAL_ILLEGAL_RE.finditer(combined))
            frontier.update({
                "probe_completed": status.get("probe_completed", False),
                "error": error_text,
                "elapsed_seconds": status.get("elapsed_seconds"),
                "signature_observed": status.get("signature_observed"),
                "last_unimplemented_opcode": (
                    {
                        "opcode": (
                            f"0x{int(illegal_matches[-1].group(1), 16):04x}"
                        ),
                        "pc": f"0x{int(illegal_matches[-1].group(2), 16):08x}",
                    }
                    if illegal_matches else None
                ),
                "last_illegal_pc": (
                    f"0x{int(final_illegal[-1].group(1), 16):08x}"
                    if final_illegal else None
                ),
                "diagnostics": status.get("diagnostics", []),
                "arm_control": status.get("arm_control"),
                "cpus": status.get("cpus"),
                "cpu_registers": status.get("cpu_registers"),
                "cpu_summary": status.get("cpu_summary"),
                "memory_words": status.get("memory_words"),
            })
            write_json(frontier_path, frontier)
        else:
            frontier_path.unlink(missing_ok=True)

    print(json.dumps(status, indent=2, sort_keys=True))
    if error_text is not None:
        print(error_text, file=sys.stderr)
        return 1
    if args.strict and not status.get("signature_seen", False):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
