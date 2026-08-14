#!/usr/bin/env python3
"""Verify the Raspberry Pi VC4 reset vector before guest execution."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from types import ModuleType
from typing import Any

BOOT_ENTRY = 0x00000200
DIRECT_ENTRY = 0x3C000000
SCRATCH_PC = 0x00001234
PC_RE = re.compile(r"\bpc=([0-9a-fA-F]{1,8})\b")


def load_handoff_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-0200-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_bootrom_0200", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load 0x200 boot-ROM smoke from {path}")
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

    def hmp(self, command: str, *, cpu_index: int) -> str:
        result = self.execute(
            "human-monitor-command",
            {
                "command-line": command,
                "cpu-index": cpu_index,
            },
        )
        return "" if result is None else str(result)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_qmp(path: Path, proc: subprocess.Popen[bytes],
                 timeout: float) -> QMP:
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

    raise TimeoutError(f"QMP did not accept connections: {last_error}")


def find_vpu(cpus: Any) -> tuple[int, str, list[str]]:
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
        if "vc4" not in qom_type.lower():
            continue
        cpu_index = cpu.get("cpu-index")
        qom_path = cpu.get("qom-path")
        if not isinstance(cpu_index, int) or not isinstance(qom_path, str):
            raise RuntimeError(f"VC4 CPU has incomplete QMP metadata: {cpu!r}")
        return cpu_index, qom_path, qom_types

    raise RuntimeError(f"no VC4 CPU in qom-types={qom_types!r}")


def read_pc(qmp: QMP, cpu_index: int) -> tuple[int, str]:
    registers = qmp.hmp("info registers", cpu_index=cpu_index)
    match = PC_RE.search(registers)
    if not match:
        raise RuntimeError(f"VC4 register dump has no PC: {registers!r}")
    return int(match.group(1), 16), registers


def wait_for_pc(qmp: QMP, cpu_index: int, expected: int,
                timeout: float = 2.0) -> tuple[int, str]:
    deadline = time.monotonic() + timeout
    pc = -1
    registers = ""

    while time.monotonic() < deadline:
        pc, registers = read_pc(qmp, cpu_index)
        if pc == expected:
            return pc, registers
        time.sleep(0.01)
    raise RuntimeError(
        f"VC4 PC did not become 0x{expected:08x}: "
        f"pc=0x{pc & 0xffffffff:08x} registers={registers!r}"
    )


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def probe_case(qemu: Path, name: str, expected_pc: int,
               extra_args: list[str], stderr_path: Path) -> None:
    qmp_path = stderr_path.with_suffix(".qmp")
    command = [
        str(qemu),
        "-M", "raspi3b-vc4-hetero",
        "-m", "1G",
        "-smp", "5",
        "-accel", "tcg,thread=single",
        "-display", "none",
        "-monitor", "none",
        "-serial", "none",
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-S",
        *extra_args,
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
        cpu_index, qom_path, qom_types = find_vpu(
            qmp.execute("query-cpus-fast")
        )

        initial_pc, initial_registers = read_pc(qmp, cpu_index)
        if initial_pc != expected_pc:
            raise RuntimeError(
                f"{name} initial reset vector is wrong: "
                f"expected=0x{expected_pc:08x} "
                f"actual=0x{initial_pc:08x} "
                f"registers={initial_registers!r}"
            )

        halted = qmp.execute(
            "qom-get",
            {
                "path": qom_path,
                "property": "vc4-debug-halted",
            },
        )
        if halted:
            raise RuntimeError(f"{name} VPU started halted at its reset vector")

        qmp.hmp(f"set $pc = 0x{SCRATCH_PC:x}", cpu_index=cpu_index)
        scratch_pc, _ = read_pc(qmp, cpu_index)
        if scratch_pc != SCRATCH_PC:
            raise RuntimeError(
                f"{name} could not perturb PC before reset: "
                f"actual=0x{scratch_pc:08x}"
            )

        qmp.execute("system_reset")
        reset_pc, _ = wait_for_pc(qmp, cpu_index, expected_pc)
        halted_after = qmp.execute(
            "qom-get",
            {
                "path": qom_path,
                "property": "vc4-debug-halted",
            },
        )
        if halted_after:
            raise RuntimeError(f"{name} VPU was halted after system_reset")

        print(
            "VC4_RESET_ENTRY "
            f"case={name} cpu-index={cpu_index} "
            f"initial=0x{initial_pc:08x} scratch=0x{scratch_pc:08x} "
            f"reset=0x{reset_pc:08x} qom-path={qom_path} "
            f"qom-types={qom_types}"
        )
        qmp.execute("quit")
        proc.wait(timeout=5)
    except Exception:
        diagnostics = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
        if diagnostics:
            print(f"--- {name} qemu stderr ---", file=os.sys.stderr)
            print(diagnostics, file=os.sys.stderr)
        raise
    finally:
        if qmp is not None:
            qmp.close()
        stop_process(proc)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    handoff = load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    handoff.install_real_handoff(smoke)

    with tempfile.TemporaryDirectory(prefix="vc4-reset-entry-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "bootrom.img"
        direct_path = tmp / "direct-vpu.bin"

        smoke.build_sd_image(image_path, smoke.build_bootcode())
        direct_path.write_bytes(b"\x01\x00" * 8)

        probe_case(
            qemu,
            "fat-bootcode",
            BOOT_ENTRY,
            ["-drive", f"file={image_path},format=raw,if=sd"],
            tmp / "fat-bootcode.stderr",
        )
        probe_case(
            qemu,
            "direct-image",
            DIRECT_ENTRY,
            ["-kernel", str(direct_path)],
            tmp / "direct-image.stderr",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
