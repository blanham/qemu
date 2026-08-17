#!/usr/bin/env python3
"""Prove the stock Raspberry Pi firmware-to-AArch64 handoff.

This probe builds a normal FAT32 boot volume containing the pinned firmware
trio plus CONFIG.TXT and a caller-supplied kernel8.img.  It deliberately does
not use QEMU's -kernel shortcut: success requires bootcode.bin and start.elf to
load and enter the payload through the emulated VideoCore/ARM boot path.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any

SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21  # "VC4_ARM!"

ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110
KERNEL_LOAD_ADDR = 0x00080000


def load_stock_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LineSocket:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def send_line(self, line: str) -> str:
        self.file.write(line.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest socket closed while waiting for {line!r}")
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
                        kind: str, timeout: float) -> QMP | LineSocket:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        try:
            if kind == "qmp":
                return QMP(path)
            return LineSocket(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.02)
    raise TimeoutError(f"{kind} socket did not accept connections: {path}") from last_error


def parse_qtest_value(reply: str) -> int:
    fields = reply.split()
    if len(fields) != 2 or fields[0] != "OK":
        raise RuntimeError(f"unexpected qtest reply: {reply!r}")
    return int(fields[1], 0)


def readq(qtest: LineSocket, address: int) -> int:
    return parse_qtest_value(qtest.send_line(f"readq 0x{address:x}"))


def readl(qtest: LineSocket, address: int) -> int:
    return parse_qtest_value(qtest.send_line(f"readl 0x{address:x}"))


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
            except Exception as exc:  # Preserve all other CPUs on one failure.
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


def diagnostics_tail(text: str, lines: int = 160) -> list[str]:
    return text.splitlines()[-lines:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("kernel8", type=Path)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=360.0)
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    for path in (args.qemu, args.bootcode, args.start_elf,
                 args.fixup_dat, args.kernel8):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    if args.config is None:
        config = (
            b"arm_64bit=1\n"
            b"kernel=kernel8.img\n"
            b"enable_gic=1\n"
            b"disable_splash=1\n"
            b"boot_delay=0\n"
        )
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
        ("KERNEL8.IMG", args.kernel8.read_bytes()),
    ]
    layouts = stock.build_fat32_image(image_path, files)

    with tempfile.TemporaryDirectory(prefix="vc4-stock-arm-") as tmp_s:
        tmp = Path(tmp_s)
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        command = [
            str(args.qemu.resolve()),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-no-reboot",
            "-d", "guest_errors,unimp",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                    stderr=stderr)

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        result: dict[str, Any] = {
            "schema_version": 1,
            "signature_address": f"0x{SIGNATURE_ADDR:08x}",
            "expected_signature": f"0x{SIGNATURE:016x}",
            "signature_seen": False,
            "image": str(image_path),
            "fat_layout": {name: list(chain) for name, chain in layouts.items()},
            "qemu_command": command,
        }
        try:
            qmp = wait_for_connection(qmp_path, proc, "qmp", 15.0)
            qtest = wait_for_connection(qtest_path, proc, "qtest", 15.0)
            assert isinstance(qmp, QMP)
            assert isinstance(qtest, LineSocket)

            initial_kernel_word = readq(qtest, KERNEL_LOAD_ADDR)
            deadline = time.monotonic() + args.seconds
            signature = 0
            while time.monotonic() < deadline:
                signature = readq(qtest, SIGNATURE_ADDR)
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
                "elapsed_seconds": args.seconds - max(0.0, deadline - time.monotonic()),
                "qemu_returncode": proc.poll(),
                "kernel_word_before_boot": f"0x{initial_kernel_word:016x}",
                "kernel_word_after_boot": f"0x{readq(qtest, KERNEL_LOAD_ADDR):016x}",
                "payload_argument_x0": f"0x{readq(qtest, SIGNATURE_ADDR + 8):016x}",
                "payload_initial_sp": f"0x{readq(qtest, SIGNATURE_ADDR + 16):016x}",
                "payload_mpidr_el1": f"0x{readq(qtest, SIGNATURE_ADDR + 24):016x}",
                "arm_control0": f"0x{readl(qtest, ARM_CONTROL0):08x}",
                "arm_control1": f"0x{readl(qtest, ARM_CONTROL1):08x}",
                "arm_status": f"0x{readl(qtest, ARM_STATUS):08x}",
                "arm_id": f"0x{readl(qtest, ARM_ID):08x}",
                "pm_proc": f"0x{readl(qtest, PM_PROC):08x}",
                "cpu_snapshot": cpu_snapshot(qmp),
            })
        except Exception as exc:
            result["probe_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if stderr_path.is_file():
                diagnostics = stderr_path.read_text(
                    encoding="utf-8", errors="replace")
                result["qemu_diagnostics_tail"] = diagnostics_tail(diagnostics)
            result_path.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if qmp is not None:
                try:
                    if proc.poll() is None:
                        qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            if qtest is not None:
                qtest.close()
            stop_process(proc)

    return 0 if result.get("signature_seen") is True else 2


if __name__ == "__main__":
    sys.exit(main())
