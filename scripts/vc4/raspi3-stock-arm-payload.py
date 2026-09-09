#!/usr/bin/env python3
"""Prove the stock Raspberry Pi firmware-to-AArch64 handoff.

This probe builds a normal FAT32 boot volume containing an unchanged pinned
firmware trio, CONFIG.TXT, an optional matching DTB, and a caller-supplied
kernel8.img.  It deliberately does not use QEMU's ``-kernel`` shortcut:
success requires ``bootcode.bin`` and ``start.elf`` to load and enter the
payload through the emulated VideoCore/ARM boot path.

The running TCG machine is observed through QMP/HMP physical-memory reads.
Unlike a qtest-accelerator session, ordinary TCG owns and advances its virtual
clock.  Keeping the qtest server out of this production-firmware probe avoids
both qtest-only clock commands and high-frequency test-transport interference
with firmware delay loops.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any

SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21  # "VC4_ARM!"

SYSTEM_TIMER_CLO = 0x3F003004
ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110
KERNEL_LOAD_ADDR = 0x00080000
POLL_INTERVAL_SECONDS = 0.10


def load_stock_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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

    def execute(
        self,
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> Any:
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

    def physical_bytes(self, address: int, size: int) -> bytes:
        if address < 0 or address > 0xFFFFFFFFFFFFFFFF:
            raise ValueError(f"physical address out of range: {address}")
        if size <= 0 or size > 64:
            raise ValueError(f"unsupported physical read size: {size}")

        output = self.hmp(f"xp /{size}bx 0x{address:x}")
        values: list[int] = []
        for line in output.splitlines():
            if ":" not in line:
                continue
            right = line.split(":", 1)[1]
            values.extend(
                int(match, 16)
                for match in re.findall(
                    r"(?:^|\s)0x([0-9a-fA-F]{2})(?=\s|$)",
                    right,
                )
            )
        if len(values) != size:
            raise RuntimeError(
                f"HMP physical read returned {len(values)} of {size} bytes "
                f"at 0x{address:x}: {output!r}"
            )
        return bytes(values)

    def readl(self, address: int) -> int:
        return int.from_bytes(self.physical_bytes(address, 4), "little")

    def readq(self, address: int) -> int:
        return int.from_bytes(self.physical_bytes(address, 8), "little")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_qmp(
    path: Path,
    process: subprocess.Popen[bytes],
    timeout: float,
) -> QMP:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"QEMU exited early with status {process.returncode}"
            )
        try:
            return QMP(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
            time.sleep(0.02)
    raise TimeoutError(
        f"QMP socket did not accept connections: {path}"
    ) from last_error


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def cpu_snapshot(qmp: QMP) -> dict[str, Any]:
    cpus = qmp.execute("query-cpus-fast")
    if not isinstance(cpus, list):
        raise RuntimeError(f"query-cpus-fast returned {cpus!r}")
    records: list[dict[str, Any]] = []
    for item in cpus:
        if not isinstance(item, dict):
            continue
        index = item.get("cpu-index")
        registers = ""
        if isinstance(index, int):
            try:
                registers = qmp.hmp("info registers", cpu_index=index)
            except Exception as error:
                registers = f"register query failed: {error}"
        records.append(
            {
                "cpu_index": index,
                "qom_type": item.get("qom-type"),
                "target": item.get("target"),
                "thread_id": item.get("thread-id"),
                "halted": item.get("halted"),
                "registers": registers,
            }
        )
    return {
        "query_cpus_fast": cpus,
        "cpus": records,
        "info_cpus": qmp.hmp("info cpus"),
    }


def diagnostics_tail(text: str, lines: int = 256) -> list[str]:
    return text.splitlines()[-lines:]


def default_config(has_dtb: bool) -> bytes:
    lines = [
        "arm_64bit=1",
        "kernel=kernel8.img",
        "enable_gic=1",
        "disable_splash=1",
        "boot_delay=0",
    ]
    if has_dtb:
        lines.append("device_tree=rpi3.dtb")
    return ("\n".join(lines) + "\n").encode("ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("kernel8", type=Path)
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=360.0)
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    required = (
        args.qemu,
        args.bootcode,
        args.start_elf,
        args.fixup_dat,
        args.kernel8,
    )
    for path in required:
        if not path.is_file():
            parser.error(f"not a file: {path}")
    if args.dtb is not None and not args.dtb.is_file():
        parser.error(f"not a file: {args.dtb}")

    if args.config is None:
        config = default_config(args.dtb is not None)
    else:
        if not args.config.is_file():
            parser.error(f"not a file: {args.config}")
        config = args.config.read_bytes()

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    stderr_path = out_dir / "qemu.stderr"
    result_path = out_dir / "result.json"
    image_path = out_dir / "stock-arm-payload.img"

    stock = load_stock_probe()
    files = [
        ("BOOTCODE.BIN", args.bootcode.read_bytes()),
        ("START.ELF", args.start_elf.read_bytes()),
        ("FIXUP.DAT", args.fixup_dat.read_bytes()),
        ("CONFIG.TXT", config),
        ("CMDLINE.TXT", b"console=serial0,115200 earlycon=pl011,0x3f201000\n"),
        ("KERNEL8.IMG", args.kernel8.read_bytes()),
    ]
    if args.dtb is not None:
        files.insert(-1, ("RPI3.DTB", args.dtb.read_bytes()))
    layouts = stock.build_fat32_image(image_path, files)

    with tempfile.TemporaryDirectory(prefix="vc4-stock-arm-") as temporary:
        tmp = Path(temporary)
        qmp_path = tmp / "qmp.sock"
        command = [
            str(args.qemu.resolve()),
            "-M",
            "raspi3b-vc4-hetero",
            "-m",
            "1G",
            "-smp",
            "5",
            "-drive",
            f"file={image_path},format=raw,if=sd",
            "-accel",
            "tcg,thread=single",
            "-display",
            "none",
            "-monitor",
            "none",
            "-serial",
            "none",
            "-no-reboot",
            "-d",
            "guest_errors,unimp",
            "-qmp",
            f"unix:{qmp_path},server=on,wait=off",
        ]

        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp: QMP | None = None
        result: dict[str, Any] = {
            "schema_version": 4,
            "source_sha": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "observer": "qmp-hmp-xp-byte-reads",
            "signature_address": f"0x{SIGNATURE_ADDR:08x}",
            "expected_signature": f"0x{SIGNATURE:016x}",
            "signature_seen": False,
            "image": str(image_path),
            "fat_layout": {
                name: list(chain) for name, chain in layouts.items()
            },
            "qemu_command": command,
        }
        try:
            qmp = wait_for_qmp(qmp_path, process, 15.0)

            initial_kernel_word = qmp.readq(KERNEL_LOAD_ADDR)
            initial_system_timer = qmp.readl(SYSTEM_TIMER_CLO)
            started = time.monotonic()
            deadline = started + args.seconds
            signature = 0
            polls = 0

            while time.monotonic() < deadline:
                signature = qmp.readq(SIGNATURE_ADDR)
                polls += 1
                if signature == SIGNATURE:
                    result["signature_seen"] = True
                    break
                if process.poll() is not None:
                    break
                time.sleep(POLL_INTERVAL_SECONDS)

            try:
                qmp.execute("stop")
            except Exception:
                pass

            final_system_timer = qmp.readl(SYSTEM_TIMER_CLO)
            result.update(
                {
                    "observed_signature": f"0x{signature:016x}",
                    "elapsed_seconds": time.monotonic() - started,
                    "poll_count": polls,
                    "qemu_returncode": process.poll(),
                    "system_timer_clo_before": (
                        f"0x{initial_system_timer:08x}"
                    ),
                    "system_timer_clo_after": (
                        f"0x{final_system_timer:08x}"
                    ),
                    "system_timer_delta_us": (
                        final_system_timer - initial_system_timer
                    )
                    & 0xFFFFFFFF,
                    "kernel_word_before_boot": (
                        f"0x{initial_kernel_word:016x}"
                    ),
                    "kernel_word_after_boot": (
                        f"0x{qmp.readq(KERNEL_LOAD_ADDR):016x}"
                    ),
                    "payload_argument_x0": (
                        f"0x{qmp.readq(SIGNATURE_ADDR + 24):016x}"
                    ),
                    "payload_initial_sp": (
                        f"0x{qmp.readq(SIGNATURE_ADDR + 16):016x}"
                    ),
                    "payload_mpidr_el1": (
                        f"0x{qmp.readq(SIGNATURE_ADDR + 8):016x}"
                    ),
                    "payload_current_el": (
                        f"0x{qmp.readq(SIGNATURE_ADDR + 32):016x}"
                    ),
                    "payload_sctlr_el1": (
                        f"0x{qmp.readq(SIGNATURE_ADDR + 40):016x}"
                    ),
                    "arm_control0": f"0x{qmp.readl(ARM_CONTROL0):08x}",
                    "arm_control1": f"0x{qmp.readl(ARM_CONTROL1):08x}",
                    "arm_status": f"0x{qmp.readl(ARM_STATUS):08x}",
                    "arm_id": f"0x{qmp.readl(ARM_ID):08x}",
                    "pm_proc": f"0x{qmp.readl(PM_PROC):08x}",
                    "cpu_snapshot": cpu_snapshot(qmp),
                }
            )
        except Exception as error:
            result["probe_error"] = f"{type(error).__name__}: {error}"
        finally:
            if stderr_path.is_file():
                diagnostics = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                result["qemu_diagnostics_tail"] = diagnostics_tail(
                    diagnostics
                )
            result_path.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if qmp is not None:
                try:
                    if process.poll() is None:
                        qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            stop_process(process)

    return 0 if result.get("signature_seen") is True else 2


if __name__ == "__main__":
    sys.exit(main())
