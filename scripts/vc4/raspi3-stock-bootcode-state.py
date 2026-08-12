#!/usr/bin/env python3
"""Capture the live VideoCore IV state when stock bootcode stops progressing."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from types import ModuleType
from typing import Any

PC_RE = re.compile(r"\bpc=([0-9a-fA-F]{1,8})\b")
SR_RE = re.compile(r"\bsr=([0-9a-fA-F]{1,8})\b")
ILLEGAL_RE = re.compile(
    r"VideoCore IV: unimplemented opcode 0x([0-9a-fA-F]+) "
    r"at 0x([0-9a-fA-F]+)"
)


def load_probe_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock probe module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QMP:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self.sock.connect(str(path))
        except OSError:
            self.sock.close()
            raise
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


def wait_for_qmp(path: Path, proc: subprocess.Popen[bytes],
                 timeout: float) -> QMP:
    """Wait until QEMU is accepting QMP connections, not merely bound."""
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None

    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        try:
            return QMP(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.01)

    raise TimeoutError(
        f"QMP socket did not accept connections: {path}"
    ) from last_error


def flatten(text: str) -> str:
    return " | ".join(line.strip() for line in text.splitlines() if line.strip())


def diagnostic_tail(log: str, limit: int = 48) -> str:
    """Return recent distinct QEMU diagnostics without probe housekeeping."""
    ignored = (
        "loaded bootcode.bin",
        "terminating on signal",
    )
    distinct_reversed: list[str] = []
    seen: set[str] = set()

    for raw_line in reversed(log.splitlines()):
        line = raw_line.strip()
        if not line or any(fragment in line for fragment in ignored):
            continue
        if line in seen:
            continue
        seen.add(line)
        distinct_reversed.append(line)
        if len(distinct_reversed) == limit:
            break

    return " | ".join(reversed(distinct_reversed)) or "none"


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def find_vc4_cpu(cpus: Any) -> tuple[int, list[str]]:
    if not isinstance(cpus, list):
        raise RuntimeError(f"query-cpus-fast returned {cpus!r}")

    qom_types = [
        str(cpu.get("qom-type", ""))
        for cpu in cpus
        if isinstance(cpu, dict)
    ]
    for cpu in cpus:
        if not isinstance(cpu, dict):
            continue
        qom_type = str(cpu.get("qom-type", ""))
        if "vc4" in qom_type.lower():
            index = cpu.get("cpu-index")
            if not isinstance(index, int):
                raise RuntimeError(f"VC4 CPU has no integer cpu-index: {cpu!r}")
            return index, qom_types

    raise RuntimeError(f"no VC4 CPU in qom-types={qom_types!r}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument("bootcode", help="unmodified bootcode.bin")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument(
        "--icount-shift",
        type=int,
        default=10,
        help=(
            "advance virtual time by 2^SHIFT nanoseconds per guest "
            "instruction; this lets polling delays progress deterministically"
        ),
    )
    parser.add_argument("--barrier-is-success", action="store_true")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    bootcode_path = Path(args.bootcode).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")
    if not bootcode_path.is_file():
        parser.error(f"not a file: {bootcode_path}")

    probe = load_probe_module()
    bootcode = bootcode_path.read_bytes()

    with tempfile.TemporaryDirectory(prefix="vc4-stock-state-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "stock-bootcode.img"
        qmp_path = tmp / "qmp.sock"
        stderr_path = tmp / "qemu.stderr"
        cluster_count, last_cluster = probe.build_sd_image(image_path, bootcode)

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-icount",
            f"shift={args.icount_shift},align=off,sleep=off",
            "-d", "unimp,guest_errors",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp: QMP | None = None
        try:
            qmp = wait_for_qmp(qmp_path, proc, 10.0)
            deadline = time.monotonic() + args.seconds
            illegal: re.Match[str] | None = None

            while time.monotonic() < deadline:
                log = stderr_path.read_text(encoding="utf-8", errors="replace")
                illegal = ILLEGAL_RE.search(log)
                if illegal or proc.poll() is not None:
                    break
                time.sleep(0.02)

            if proc.poll() is None:
                qmp.execute("stop")
                for _ in range(100):
                    status = qmp.execute("query-status")
                    if isinstance(status, dict) and not status.get("running", True):
                        break
                    time.sleep(0.01)

            log = stderr_path.read_text(encoding="utf-8", errors="replace")
            log_tail = diagnostic_tail(log)
            illegal = illegal or ILLEGAL_RE.search(log)
            if illegal:
                opcode = int(illegal.group(1), 16)
                pc = int(illegal.group(2), 16)
                print(
                    "STOCK_BOOTCODE_BARRIER "
                    f"kind=illegal-opcode opcode=0x{opcode:04x} "
                    f"pc=0x{pc:08x} "
                    f"context={probe.context_bytes(bootcode, pc, 24)} "
                    f"qemu-diagnostics={log_tail}"
                )
                return 0 if args.barrier_is_success else 2

            if proc.poll() is not None:
                raise RuntimeError(f"QEMU exited with status {proc.returncode}")

            cpus = qmp.execute("query-cpus-fast")
            vpu_index, qom_types = find_vc4_cpu(cpus)
            registers = qmp.hmp("info registers", cpu_index=vpu_index)
            cpu_summary = qmp.hmp("info cpus")

            pc_match = PC_RE.search(registers)
            sr_match = SR_RE.search(registers)
            if not pc_match:
                raise RuntimeError(
                    "VC4 info registers did not contain a PC: "
                    f"cpu-index={vpu_index} qom-types={qom_types!r} "
                    f"registers={registers!r}"
                )

            pc = int(pc_match.group(1), 16)
            sr = int(sr_match.group(1), 16) if sr_match else 0
            memory = qmp.hmp(
                f"x /16bx 0x{pc:x}",
                cpu_index=vpu_index,
            )
            if 0 <= pc < len(bootcode):
                context = probe.context_bytes(bootcode, pc, 32)
            else:
                context = "outside-boot-cache"

            print(
                "Official bootcode live-state probe: "
                f"bytes={len(bootcode)} entry=0x{probe.BOOT_ENTRY:08x} "
                f"clusters={probe.FIRST_BOOT_CLUSTER}->{last_cluster} "
                f"cluster-count={cluster_count} qom-types={qom_types}"
            )
            print(
                "STOCK_BOOTCODE_BARRIER "
                f"kind=stalled-state cpu-index={vpu_index} "
                f"pc=0x{pc:08x} sr=0x{sr:08x} context={context} "
                f"registers={flatten(registers)} "
                f"cpu-summary={flatten(cpu_summary)} "
                f"memory={flatten(memory)} "
                f"qemu-diagnostics={log_tail}"
            )
            return 0 if args.barrier_is_success else 3
        finally:
            if qmp is not None:
                try:
                    if proc.poll() is None:
                        qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
