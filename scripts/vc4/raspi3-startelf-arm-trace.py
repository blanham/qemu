#!/usr/bin/env python3
"""Capture ARM0 release state while the official VC4 boot chain is live."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from typing import Any

ADDRESSES = {
    "arm_reset_vector_0": 0x00000000,
    "arm_reset_vector_4": 0x00000004,
    "kernel_80000": 0x00080000,
    "kernel_80004": 0x00080004,
    "kernel_marker": 0x10000000,
    "system_timer_clo": 0x3F003004,
    "arm_control0": 0x3F00B000,
    "arm_payload": 0x3F00B42C,
    "arm_control1": 0x3F00B440,
    "arm_status": 0x3F00B444,
    "pm_image": 0x3F100108,
    "pm_proc": 0x3F100110,
}

VPU_REG_RE = {
    "pc": re.compile(r"\bpc=([0-9a-fA-F]{1,8})\b"),
    "sp": re.compile(r"\br25=([0-9a-fA-F]{1,8})\b"),
    "lr": re.compile(r"\br26=([0-9a-fA-F]{1,8})\b"),
    "exception_sp": re.compile(r"\br28=([0-9a-fA-F]{1,8})\b"),
    "r0": re.compile(r"\br0\s*=([0-9a-fA-F]{1,8})\b"),
    "r1": re.compile(r"\br1\s*=([0-9a-fA-F]{1,8})\b"),
    "r2": re.compile(r"\br2\s*=([0-9a-fA-F]{1,8})\b"),
    "r3": re.compile(r"\br3\s*=([0-9a-fA-F]{1,8})\b"),
    "r24": re.compile(r"\br24=([0-9a-fA-F]{1,8})\b"),
}


class QMP:
    def __init__(self, path: Path, deadline: float):
        self.sock = connect(path, deadline)
        self.file = self.sock.makefile("rwb", buffering=0)

    def read(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP closed")
            obj = json.loads(line)
            if "event" not in obj:
                return obj

    def execute(
        self,
        name: str,
        args: dict[str, Any] | None = None,
    ) -> Any:
        request: dict[str, Any] = {"execute": name}
        if args:
            request["arguments"] = args
        self.file.write(json.dumps(request).encode() + b"\n")
        response = self.read()
        if "error" in response:
            raise RuntimeError(f"QMP {name}: {response['error']}")
        return response.get("return")

    def hmp(self, command: str, cpu_index: int | None = None) -> str:
        args: dict[str, Any] = {"command-line": command}
        if cpu_index is not None:
            args["cpu-index"] = cpu_index
        result = self.execute("human-monitor-command", args)
        return "" if result is None else str(result)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QTest:
    def __init__(self, path: Path, deadline: float):
        self.sock = connect(path, deadline)
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, command: str) -> str:
        self.file.write(command.encode() + b"\n")
        reply = self.file.readline().decode(errors="replace").strip()
        if not reply.startswith("OK"):
            raise RuntimeError(f"qtest {command!r}: {reply!r}")
        return reply

    def readl(self, address: int) -> int:
        return int(self.command(f"readl 0x{address:x}").split()[1], 0)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def connect(path: Path, deadline: float) -> socket.socket:
    error: OSError | None = None
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.connect(str(path))
            return sock
        except OSError as exc:
            error = exc
            sock.close()
            time.sleep(0.05)
    raise RuntimeError(f"could not connect to {path}: {error}")


def registers(qmp: QMP, cpu_index: int) -> str:
    try:
        return qmp.hmp("info registers", cpu_index)
    except RuntimeError as exc:
        return str(exc)


def parse_vpu_registers(text: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for name, pattern in VPU_REG_RE.items():
        match = pattern.search(text)
        if match:
            result[name] = int(match.group(1), 16)
    return result


def memory_dump(qmp: QMP, address: int, fmt: str, cpu_index: int) -> str:
    try:
        return qmp.hmp(f"x /{fmt} 0x{address:x}", cpu_index)
    except RuntimeError as exc:
        return str(exc)


def vpu_memory_snapshot(qmp: QMP, parsed: dict[str, int]) -> dict[str, str]:
    result: dict[str, str] = {}
    pc = parsed.get("pc")
    if pc is not None:
        result["pc_bytes"] = memory_dump(qmp, max(0, pc - 32), "96bx", 4)
        result["pc_halfwords"] = memory_dump(qmp, max(0, pc - 32), "48hx", 4)
    lr = parsed.get("lr")
    if lr is not None:
        result["lr_halfwords"] = memory_dump(qmp, max(0, lr - 32), "48hx", 4)
    sp = parsed.get("sp")
    if sp is not None:
        result["stack_words"] = memory_dump(qmp, sp, "32wx", 4)
    exception_sp = parsed.get("exception_sp")
    if exception_sp is not None:
        result["exception_stack_words"] = memory_dump(
            qmp, exception_sp, "24wx", 4
        )
    for name in ("r0", "r1", "r2", "r3", "r24"):
        value = parsed.get(name)
        if value is not None:
            result[f"{name}_words"] = memory_dump(qmp, value, "16wx", 4)
    return result


def vpu_debug(
    qmp: QMP,
    cpus: list[dict[str, Any]],
) -> dict[str, Any]:
    cpu = next(
        (
            item
            for item in cpus
            if item.get("cpu-index") == 4
            or "vc4" in str(item).lower()
        ),
        None,
    )
    if not cpu:
        return {"error": "VC4 CPU not found"}
    path = cpu.get("qom-path")
    result: dict[str, Any] = {"cpu": cpu, "qom_path": path}
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


def wait_for_frontier(
    qmp: QMP,
    qtest: QTest,
    seconds: float,
) -> tuple[str, float]:
    start = time.monotonic()
    deadline = start + seconds
    while time.monotonic() < deadline:
        cpus = qmp.execute("query-cpus-fast")
        debug = vpu_debug(qmp, cpus)
        if debug.get("vc4-debug-halted") is True:
            return "vpu-halted", time.monotonic() - start
        if qtest.readl(ADDRESSES["kernel_marker"]):
            return "kernel-marker", time.monotonic() - start
        time.sleep(0.05)
    return "timeout", time.monotonic() - start


def run(
    qemu: Path,
    image: Path,
    seconds: float,
    output: Path,
    qemu_log: Path,
) -> int:
    with tempfile.TemporaryDirectory(prefix="vc4-arm-trace-") as temp:
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
            "-d",
            "unimp,guest_errors",
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
                if "QMP" not in qmp.read():
                    raise RuntimeError("bad QMP greeting")
                qmp.execute("qmp_capabilities")
                qtest = QTest(qtest_path, deadline)
                qmp.execute("cont")
                stop_reason, elapsed = wait_for_frontier(qmp, qtest, seconds)
                qmp.execute("stop")

                cpus = qmp.execute("query-cpus-fast")
                arm0_regs = registers(qmp, 0)
                vpu_regs = registers(qmp, 4)
                parsed_vpu = parse_vpu_registers(vpu_regs)
                live = {
                    "stop_reason": stop_reason,
                    "elapsed_seconds": elapsed,
                    "query_status": qmp.execute("query-status"),
                    "cpus": cpus,
                    "cpu_summary": qmp.hmp("info cpus"),
                    "arm0_registers": arm0_regs,
                    "vpu_registers": vpu_regs,
                    "vpu_parsed_registers": parsed_vpu,
                    "vpu_memory": vpu_memory_snapshot(qmp, parsed_vpu),
                    "vpu_debug": vpu_debug(qmp, cpus),
                    "memory": {
                        name: qtest.readl(address)
                        for name, address in ADDRESSES.items()
                    },
                }
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(live, indent=2, sort_keys=True) + "\n"
                )
                print(
                    "VC4_ARM_RELEASE_TRACE "
                    + json.dumps(live, sort_keys=True)
                )
                return 0
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("image", type=Path)
    parser.add_argument("--seconds", type=float, default=120)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--qemu-log", type=Path, required=True)
    args = parser.parse_args()
    return run(
        args.qemu,
        args.image,
        args.seconds,
        args.output,
        args.qemu_log,
    )


if __name__ == "__main__":
    raise SystemExit(main())
