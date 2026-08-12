#!/usr/bin/env python3
"""Boot Cortex-A53 core 0 from VC4 through BCM2837 hardware registers."""

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

VC4_ENTRY = 0x00010000
ARM_MARKER_ADDR = 0x00002000
OBSERVATION_ADDR = 0x00003000
ARM_MARKER = 0x52504933  # "RPI3"
VC4_DONE = 0x56433444    # "VC4D"

ARM_VIEW_BASE = 0x3F000000
ARM_CONTROL0 = ARM_VIEW_BASE + 0x0000B000
ARM_CONTROL1 = ARM_VIEW_BASE + 0x0000B440
ARM_STATUS = ARM_VIEW_BASE + 0x0000B444
ARM_ID = ARM_VIEW_BASE + 0x0000B44C
PM_PROC = ARM_VIEW_BASE + 0x00100110

VC_VIEW_ARM_CONTROL = 0x7E00B000
VC_VIEW_PM = 0x7E100000
VC_VIEW_RAM = 0xC0000000

CONTROL0_VALUE = 0x0000A243
CONTROL1_VALUE = 0x00000100
PM_PROC_VALUE = 0x0000007F
ARM_ID_VALUE = 0x364D5241

MOV = 0


def half(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def word(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def vc4_mov32(rd: int, value: int) -> bytes:
    """Encode the scalar48 MOV immediate form."""
    return half(0xE800 | ((MOV & 0x1F) << 5) | (rd & 0x1F)) + word(value)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
    if not -2048 <= offset <= 2047:
        raise ValueError(f"VC4 memory offset out of range: {offset}")
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | ((fmt & 3) << 6) | (rd & 0x1F)
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


def build_image(path: Path) -> None:
    """Build one RAM image containing reset-vector ARM code and VC4 firmware."""
    arm = b"".join([
        word(a64_movz(0, ARM_MARKER_ADDR, sf=True)),
        word(a64_movz(1, ARM_MARKER & 0xFFFF, sf=False)),
        word(a64_movk(1, ARM_MARKER >> 16, shift=16, sf=False)),
        word(0xB9000001),  # str w1, [x0]
        word(0x14000000),  # b .
    ])

    vc4 = bytearray()

    # Select AArch64, one-gigabyte ARM memory, pass-through APROT, and full
    # peripheral access through the real VideoCore-visible ARM control block.
    vc4 += vc4_mov32(0, VC_VIEW_ARM_CONTROL)
    vc4 += vc4_mov32(1, CONTROL0_VALUE)
    vc4 += vc4_memory_offset(True, 1, 0, 0x000)
    vc4 += vc4_memory_offset(False, 2, 0, 0x44C)  # ARM_ID

    # Execute the firmware power-domain sequence.  The model supplies the
    # hardware-owned POWOK and MRDONE response bits.
    vc4 += vc4_mov32(3, VC_VIEW_PM)
    for value in (0x00000000, 0x00000001, 0x00000005,
                  0x0000000D, 0x0000002D, 0x0000006D):
        vc4 += vc4_mov32(1, 0x5A000000 | value)
        vc4 += vc4_memory_offset(True, 1, 3, 0x110)
    vc4 += vc4_memory_offset(False, 4, 3, 0x110)

    # Clear REQSTOP only after PM_PROC says the ARM domain is functional.
    vc4 += vc4_mov32(1, CONTROL1_VALUE)
    vc4 += vc4_memory_offset(True, 1, 0, 0x440)
    vc4 += vc4_memory_offset(False, 5, 0, 0x440)
    vc4 += vc4_memory_offset(False, 6, 0, 0x444)

    # Publish the register observations through the VC cached RAM alias.
    vc4 += vc4_mov32(7, VC_VIEW_RAM + OBSERVATION_ADDR)
    vc4 += vc4_memory_offset(True, 2, 7, 0x00)
    vc4 += vc4_memory_offset(True, 4, 7, 0x04)
    vc4 += vc4_memory_offset(True, 5, 7, 0x08)
    vc4 += vc4_memory_offset(True, 6, 7, 0x0C)
    vc4 += vc4_mov32(1, VC4_DONE)
    vc4 += vc4_memory_offset(True, 1, 7, 0x10)
    vc4 += half(0x0000)  # HALT

    image = bytearray(VC4_ENTRY + len(vc4))
    image[:len(arm)] = arm
    image[VC4_ENTRY:VC4_ENTRY + len(vc4)] = vc4
    path.write_bytes(image)


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
        self.file.write(json.dumps({"execute": command}).encode() + b"\n")
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_socket(path: Path, proc: subprocess.Popen[bytes], timeout: float) -> None:
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


def readl(qtest: LineSocket, address: int) -> int:
    return parse_qtest_value(qtest.send_line(f"readl 0x{address:x}"))


def validate_topology(cpus: Any) -> list[str]:
    if not isinstance(cpus, list) or len(cpus) != 5:
        raise RuntimeError(f"expected four A53s plus one VPU, got {cpus!r}")
    qom_types = [str(cpu.get("qom-type", "")) for cpu in cpus
                 if isinstance(cpu, dict)]
    if len(qom_types) != 5:
        raise RuntimeError(f"malformed query-cpus-fast result: {cpus!r}")
    if sum("cortex-a53" in item for item in qom_types) != 4:
        raise RuntimeError(f"expected four Cortex-A53 CPUs: {qom_types!r}")
    if sum("vc4" in item for item in qom_types) != 1:
        raise RuntimeError(f"expected one VideoCore IV CPU: {qom_types!r}")
    return qom_types


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-arm-start-") as tmp_s:
        tmp = Path(tmp_s)
        image = tmp / "raspi3-vpu-boot.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        build_image(image)

        command = [
            str(qemu),
            "-M", "raspi3b-vc4",
            "-kernel", str(image),
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-d", "guest_errors,unimp",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-S",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(command, stdout=subprocess.DEVNULL,
                                    stderr=stderr)

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        try:
            wait_for_socket(qmp_path, proc, 8.0)
            wait_for_socket(qtest_path, proc, 8.0)
            qmp = QMP(qmp_path)
            qtest = LineSocket(qtest_path)

            qom_types = validate_topology(qmp.execute("query-cpus-fast"))

            initial_control1 = readl(qtest, ARM_CONTROL1)
            initial_proc = readl(qtest, PM_PROC)
            initial_marker = readl(qtest, ARM_MARKER_ADDR)
            if initial_control1 != 0x200 or initial_proc != 0 or initial_marker != 0:
                raise RuntimeError(
                    "incorrect held-reset state before VPU execution: "
                    f"control1=0x{initial_control1:08x} "
                    f"pm_proc=0x{initial_proc:08x} "
                    f"marker=0x{initial_marker:08x}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 8.0
            marker = done = 0
            while time.monotonic() < deadline:
                marker = readl(qtest, ARM_MARKER_ADDR)
                done = readl(qtest, OBSERVATION_ADDR + 0x10)
                if marker == ARM_MARKER and done == VC4_DONE:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"QEMU exited with status {proc.returncode}")
                time.sleep(0.01)

            observations = {
                "arm_id": readl(qtest, OBSERVATION_ADDR + 0x00),
                "pm_proc": readl(qtest, OBSERVATION_ADDR + 0x04),
                "control1": readl(qtest, OBSERVATION_ADDR + 0x08),
                "status": readl(qtest, OBSERVATION_ADDR + 0x0C),
                "done": done,
            }
            registers = {
                "control0": readl(qtest, ARM_CONTROL0),
                "control1": readl(qtest, ARM_CONTROL1),
                "status": readl(qtest, ARM_STATUS),
                "arm_id": readl(qtest, ARM_ID),
                "pm_proc": readl(qtest, PM_PROC),
            }

            expected_observations = {
                "arm_id": ARM_ID_VALUE,
                "pm_proc": PM_PROC_VALUE,
                "control1": CONTROL1_VALUE,
                "status": 0,
                "done": VC4_DONE,
            }
            expected_registers = {
                "control0": CONTROL0_VALUE,
                "control1": CONTROL1_VALUE,
                "status": 0,
                "arm_id": ARM_ID_VALUE,
                "pm_proc": PM_PROC_VALUE,
            }

            if marker != ARM_MARKER:
                raise RuntimeError(
                    "Cortex-A53 core 0 never executed reset-vector payload: "
                    f"marker=0x{marker:08x}; observations={observations!r}; "
                    f"registers={registers!r}"
                )
            if observations != expected_observations:
                raise RuntimeError(
                    f"VC4 register observations differ: {observations!r} "
                    f"!= {expected_observations!r}"
                )
            if registers != expected_registers:
                raise RuntimeError(
                    f"BCM2837 register state differs: {registers!r} "
                    f"!= {expected_registers!r}"
                )

            print(
                "Raspberry Pi 3 VC4 ARM-start passed: "
                f"cpus={len(qom_types)} marker=0x{marker:08x} "
                f"control0=0x{registers['control0']:08x} "
                f"pm_proc=0x{registers['pm_proc']:08x} "
                f"control1=0x{registers['control1']:08x}"
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
            diagnostics = stderr_path.read_text(encoding="utf-8", errors="replace")
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
