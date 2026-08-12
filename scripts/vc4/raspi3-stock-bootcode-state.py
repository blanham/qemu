#!/usr/bin/env python3
"""Capture live VideoCore IV progress and the next stock-bootcode barrier."""

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
GPR_RE = re.compile(r"\br([0-9]|[12][0-9])=([0-9a-fA-F]{1,8})\b")
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


def wait_stopped(qmp: QMP) -> None:
    for _ in range(200):
        status = qmp.execute("query-status")
        if isinstance(status, dict) and not status.get("running", True):
            return
        time.sleep(0.005)
    raise TimeoutError("QEMU did not enter the stopped state")


def pause_vm(qmp: QMP) -> None:
    status = qmp.execute("query-status")
    if isinstance(status, dict) and status.get("running", False):
        qmp.execute("stop")
    wait_stopped(qmp)


def parse_registers(text: str) -> tuple[int, int, dict[int, int]]:
    pc_match = PC_RE.search(text)
    sr_match = SR_RE.search(text)
    if not pc_match:
        raise RuntimeError(f"VC4 register dump contains no PC: {text!r}")

    gprs = {
        int(match.group(1)): int(match.group(2), 16)
        for match in GPR_RE.finditer(text)
    }
    return (
        int(pc_match.group(1), 16),
        int(sr_match.group(1), 16) if sr_match else 0,
        gprs,
    )


def sample_text(samples: list[dict[str, int]], limit: int = 12) -> str:
    selected = samples[-limit:]
    return ",".join(
        (
            f"{sample['elapsed_ms']}ms:"
            f"pc={sample['pc']:08x}/"
            f"lr={sample['lr']:08x}/"
            f"r2={sample['r2']:08x}/"
            f"r3={sample['r3']:08x}"
        )
        for sample in selected
    ) or "none"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument("bootcode", help="unmodified bootcode.bin")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--sample-interval", type=float, default=0.5)
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

    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.sample_interval <= 0:
        parser.error("--sample-interval must be positive")

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
            wait_for_socket(qmp_path, proc, 10.0)
            qmp = QMP(qmp_path)
            cpus = qmp.execute("query-cpus-fast")
            vpu_index, qom_types = find_vc4_cpu(cpus)

            started = time.monotonic()
            deadline = started + args.seconds
            next_sample = started
            illegal: re.Match[str] | None = None
            samples: list[dict[str, int]] = []

            while time.monotonic() < deadline:
                log = stderr_path.read_text(encoding="utf-8", errors="replace")
                illegal = ILLEGAL_RE.search(log)
                if illegal or proc.poll() is not None:
                    break

                now = time.monotonic()
                if now >= next_sample:
                    pause_vm(qmp)
                    registers = qmp.hmp(
                        "info registers", cpu_index=vpu_index
                    )
                    pc, _sr, gprs = parse_registers(registers)
                    samples.append({
                        "elapsed_ms": int((now - started) * 1000),
                        "pc": pc,
                        "lr": gprs.get(26, 0),
                        "r2": gprs.get(2, 0),
                        "r3": gprs.get(3, 0),
                    })
                    qmp.execute("cont")
                    next_sample = now + args.sample_interval

                time.sleep(0.02)

            if proc.poll() is None:
                pause_vm(qmp)

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
                    f"samples={sample_text(samples)} "
                    f"qemu-diagnostics={log_tail}"
                )
                return 0 if args.barrier_is_success else 2

            if proc.poll() is not None:
                raise RuntimeError(f"QEMU exited with status {proc.returncode}")

            registers = qmp.hmp("info registers", cpu_index=vpu_index)
            cpu_summary = qmp.hmp("info cpus")
            pc, sr, gprs = parse_registers(registers)
            memory = qmp.hmp(
                f"x /16bx 0x{pc:x}",
                cpu_index=vpu_index,
            )
            timer_state = qmp.hmp(
                "x /2wx 0x7e003004",
                cpu_index=vpu_index,
            )
            power_state = qmp.hmp(
                "x /3wx 0x7e100108",
                cpu_index=vpu_index,
            )
            dbus_state = qmp.hmp(
                "x /4wx 0x7e900100",
                cpu_index=vpu_index,
            )

            if 0 <= pc < len(bootcode):
                context = probe.context_bytes(bootcode, pc, 32)
            else:
                context = "outside-boot-cache"

            final_sample = {
                "elapsed_ms": int((time.monotonic() - started) * 1000),
                "pc": pc,
                "lr": gprs.get(26, 0),
                "r2": gprs.get(2, 0),
                "r3": gprs.get(3, 0),
            }
            samples.append(final_sample)

            analysis_samples = samples[-min(12, len(samples)):]
            unique_pcs = {sample["pc"] for sample in analysis_samples}
            unique_lrs = {sample["lr"] for sample in analysis_samples}
            unique_r2 = {sample["r2"] for sample in analysis_samples}
            unique_r3 = {sample["r3"] for sample in analysis_samples}

            if len(unique_lrs) > 1 or len(unique_r3) > 1:
                kind = "progressing-state"
            elif len(unique_pcs) > 1 or len(unique_r2) > 1:
                kind = "polling-state"
            else:
                kind = "stalled-state"

            print(
                "Official bootcode live-state probe: "
                f"bytes={len(bootcode)} entry=0x{probe.BOOT_ENTRY:08x} "
                f"clusters={probe.FIRST_BOOT_CLUSTER}->{last_cluster} "
                f"cluster-count={cluster_count} qom-types={qom_types}"
            )
            print(
                "STOCK_BOOTCODE_BARRIER "
                f"kind={kind} cpu-index={vpu_index} "
                f"pc=0x{pc:08x} sr=0x{sr:08x} context={context} "
                f"sample-count={len(samples)} "
                f"sample-window={len(analysis_samples)} "
                f"unique-pcs={len(unique_pcs)} "
                f"unique-lrs={len(unique_lrs)} "
                f"unique-r2={len(unique_r2)} "
                f"unique-r3={len(unique_r3)} "
                f"sample-tail={sample_text(samples)} "
                f"registers={flatten(registers)} "
                f"cpu-summary={flatten(cpu_summary)} "
                f"memory={flatten(memory)} "
                f"timer={flatten(timer_state)} "
                f"power={flatten(power_state)} "
                f"dbus={flatten(dbus_state)} "
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
