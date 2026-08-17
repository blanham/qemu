#!/usr/bin/env python3
"""Run VC4 firmware through the BCM2837 GPU bus and release ARM CPU0."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any

VPU_ENTRY = 0x3C000000
PM_PROC_ARM = 0x3F100110
PM_PROC_GPU = 0x7E100110
PM_PROC_READY = 0x0000007F
ARM_MARKER_ADDR = 0x1000
ARM_MARKER_VALUE = 0xB007C0DE

MOV = 0


def half(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def word(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def vc4_alu_imm32(op: int, rd: int, value: int) -> bytes:
    """Encode the scalar 48-bit ALU-immediate form."""
    return half(0xE800 | ((op & 0x1F) << 5) | (rd & 0x1F)) + word(value)


def vc4_mov32(rd: int, value: int) -> bytes:
    return vc4_alu_imm32(MOV, rd, value)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | ((fmt & 3) << 6)
    i1 |= rd & 0x1F
    if raw & 0x800:
        i1 |= 0x100
    i2 = ((rb & 0x1F) << 11) | (raw & 0x7FF)
    return half(i1) + half(i2)


def a64_movz(rd: int, imm16: int, shift: int = 0, *, sf: bool = True) -> int:
    base = 0xD2800000 if sf else 0x52800000
    return base | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def a64_movk(rd: int, imm16: int, shift: int = 0, *, sf: bool = True) -> int:
    base = 0xF2800000 if sf else 0x72800000
    return base | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def build_vpu_firmware(path: Path) -> bytes:
    firmware = bytearray()
    firmware += vc4_mov32(0, PM_PROC_GPU & ~0xFFF)

    # Broadcom PM_PROC power-domain sequence.  The model supplies POWOK and
    # MRDONE as read-only completion bits after the corresponding requests.
    for requested in (0x01, 0x05, 0x0D, 0x2D, 0x6D):
        firmware += vc4_mov32(1, 0x5A000000 | requested)
        firmware += vc4_memory_offset(True, 1, 0, PM_PROC_GPU & 0xFFF)

    firmware += half(0x0000)  # development HALT
    path.write_bytes(firmware)
    return bytes(firmware)


def build_arm_payload() -> list[int]:
    return [
        a64_movz(0, ARM_MARKER_ADDR, sf=True),
        a64_movz(1, ARM_MARKER_VALUE & 0xFFFF, sf=False),
        a64_movk(1, ARM_MARKER_VALUE >> 16, shift=16, sf=False),
        0xB9000001,  # str w1, [x0]
        0x14000000,  # b .
    ]


class LineSocket:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def send_line(self, line: str) -> str:
        self.file.write(line.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"socket closed while waiting for {line!r}")
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

    def execute(self, command: str) -> Any:
        payload = json.dumps({"execute": command}).encode("utf-8") + b"\n"
        self.file.write(payload)
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

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


def parse_qtest_value(reply: str) -> int:
    fields = reply.split()
    if len(fields) != 2 or fields[0] != "OK":
        raise RuntimeError(f"unexpected qtest reply: {reply!r}")
    return int(fields[1], 0)


def qtest_writel(qtest: LineSocket, address: int, value: int) -> None:
    reply = qtest.send_line(f"writel 0x{address:x} 0x{value & 0xffffffff:x}")
    if reply != "OK":
        raise RuntimeError(f"unexpected qtest write reply: {reply!r}")


def validate_cpu_topology(cpus: Any) -> tuple[list[str], int, int]:
    if not isinstance(cpus, list) or len(cpus) != 5:
        raise RuntimeError(f"expected four A53s and one VPU, got {cpus!r}")

    qom_types = [str(cpu.get("qom-type", "")) for cpu in cpus
                 if isinstance(cpu, dict)]
    arm_count = sum("cortex-a53" in qom_type for qom_type in qom_types)
    vc4_count = sum("vc4" in qom_type for qom_type in qom_types)
    if arm_count != 4 or vc4_count != 1:
        raise RuntimeError(
            f"unexpected heterogeneous topology: qom-types={qom_types!r}"
        )
    return qom_types, arm_count, vc4_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-power-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "pm-proc.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        firmware = build_vpu_firmware(firmware_path)
        arm_payload = build_arm_payload()

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-kernel", str(firmware_path),
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-d", "guest_errors",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-S",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        try:
            wait_for_socket(qmp_path, proc, 10.0)
            wait_for_socket(qtest_path, proc, 10.0)
            qmp = QMP(qmp_path)
            qtest = LineSocket(qtest_path)

            qom_types, arm_count, vc4_count = validate_cpu_topology(
                qmp.execute("query-cpus-fast")
            )

            proc_before = parse_qtest_value(
                qtest.send_line(f"readl 0x{PM_PROC_ARM:x}")
            )
            if proc_before != 0:
                raise RuntimeError(
                    f"PM_PROC did not reset to zero: 0x{proc_before:08x}"
                )

            expected0 = int.from_bytes(firmware[0:4], "little")
            expected1 = int.from_bytes(firmware[4:8], "little")
            image0 = parse_qtest_value(
                qtest.send_line(f"readl 0x{VPU_ENTRY:x}")
            )
            image1 = parse_qtest_value(
                qtest.send_line(f"readl 0x{VPU_ENTRY + 4:x}")
            )
            if (image0, image1) != (expected0, expected1):
                raise RuntimeError(
                    "VPU firmware was not loaded through its GPU-bus address "
                    f"space: got 0x{image0:08x}/0x{image1:08x}, "
                    f"expected 0x{expected0:08x}/0x{expected1:08x}"
                )

            for index, instruction in enumerate(arm_payload):
                qtest_writel(qtest, index * 4, instruction)

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            marker = 0
            proc_state = 0
            while time.monotonic() < deadline:
                marker = parse_qtest_value(
                    qtest.send_line(f"readl 0x{ARM_MARKER_ADDR:x}")
                )
                proc_state = parse_qtest_value(
                    qtest.send_line(f"readl 0x{PM_PROC_ARM:x}")
                )
                if marker == ARM_MARKER_VALUE and proc_state == PM_PROC_READY:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if marker != ARM_MARKER_VALUE or proc_state != PM_PROC_READY:
                raise RuntimeError(
                    "real-map VC4 -> ARM release did not complete: "
                    f"marker=0x{marker:08x} "
                    f"expected=0x{ARM_MARKER_VALUE:08x} "
                    f"PM_PROC=0x{proc_state:08x} "
                    f"expected=0x{PM_PROC_READY:08x}"
                )

            print(
                "BCM2837 VC4-first power-on passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"vpu-entry=0x{VPU_ENTRY:08x} "
                f"pm-proc=0x{proc_state:08x} marker=0x{marker:08x}"
            )
            qmp.execute("quit")
            proc.wait(timeout=5)
            return 0
        except Exception:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if diagnostics:
                print("--- qemu stderr ---", file=os.sys.stderr)
                print(diagnostics, file=os.sys.stderr)
            raise
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()


if __name__ == "__main__":
    raise SystemExit(main())
