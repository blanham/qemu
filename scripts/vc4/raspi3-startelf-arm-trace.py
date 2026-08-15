#!/usr/bin/env python3
"""Capture ARM0 release state while the official VC4 boot chain is live."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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
    "arm_control1": 0x3F00B440,
    "arm_status": 0x3F00B444,
    "pm_proc": 0x3F100110,
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
        return qmp.execute(
            "human-monitor-command",
            {"command-line": "info registers", "cpu-index": cpu_index},
        )
    except RuntimeError as exc:
        return str(exc)


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
                time.sleep(seconds)

                cpus = qmp.execute("query-cpus-fast")
                live = {
                    "query_status": qmp.execute("query-status"),
                    "cpus": cpus,
                    "arm0_registers": registers(qmp, 0),
                    "vpu_registers": registers(qmp, 4),
                    "vpu_debug": vpu_debug(qmp, cpus),
                    "memory": {
                        name: qtest.readl(address)
                        for name, address in ADDRESSES.items()
                    },
                }
                qmp.execute("stop")
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
