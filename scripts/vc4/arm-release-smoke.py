#!/usr/bin/env python3
"""Execute a VC4 -> Cortex-A53 release transaction under one TCG process."""

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

RELEASE_BASE = 0x10000000
ARM_ENTRY = 0x1000
MARKER_ADDR = 0x2000
MARKER_VALUE = 0xA55A5AA5

MOV = 0
LSL = 28


def half(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def word(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def vc4_alu_imm16(op: int, rd: int, value: int) -> bytes:
    return half(0xB000 | ((op & 0x1F) << 5) | (rd & 0x1F)) + half(value)


def vc4_mov(rd: int, value: int) -> bytes:
    return vc4_alu_imm16(MOV, rd, value)


def vc4_small_imm(op: int, rd: int, value: int) -> bytes:
    if op & 1 or rd >= 16 or not 0 <= value < 32:
        raise ValueError("invalid VC4 short immediate")
    return half(0x6000 | ((op // 2) << 9) | ((value & 0x1F) << 4) | rd)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
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
    vc4 = bytearray()
    vc4 += vc4_mov(0, 0x1000)
    vc4 += vc4_small_imm(LSL, 0, 16)          # r0 = 0x10000000
    vc4 += vc4_mov(1, ARM_ENTRY)
    vc4 += vc4_memory_offset(True, 1, 0, 0)   # ENTRY_LO
    vc4 += vc4_mov(1, 1)
    vc4 += vc4_memory_offset(True, 1, 0, 8)   # CONTROL.GO
    vc4 += half(0x0000)                       # development HALT

    arm = b"".join([
        word(a64_movz(0, MARKER_ADDR, sf=True)),
        word(a64_movz(1, MARKER_VALUE & 0xFFFF, sf=False)),
        word(a64_movk(1, MARKER_VALUE >> 16, shift=16, sf=False)),
        word(0xB9000001),                     # str w1, [x0]
        word(0x14000000),                     # b .
    ])

    image = bytearray(ARM_ENTRY + len(arm))
    image[:len(vc4)] = vc4
    image[ARM_ENTRY:ARM_ENTRY + len(arm)] = arm
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
            msg = json.loads(line)
            if "event" not in msg:
                return msg

    def execute(self, command: str) -> Any:
        payload = json.dumps({"execute": command}).encode("utf-8") + b"\n"
        self.file.write(payload)
        msg = self._read_message()
        if "error" in msg:
            raise RuntimeError(f"QMP {command} failed: {msg['error']}")
        return msg.get("return")

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
    parts = reply.split()
    if len(parts) != 2 or parts[0] != "OK":
        raise RuntimeError(f"unexpected qtest reply: {reply!r}")
    return int(parts[1], 0)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-arm-release-") as tmp_s:
        tmp = Path(tmp_s)
        image = tmp / "release.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        build_image(image)

        cmd = [
            str(qemu),
            "-M", "vc4-arm-release-smoke",
            "-m", "16M",
            "-kernel", str(image),
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
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=stderr)

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        try:
            wait_for_socket(qmp_path, proc, 5.0)
            wait_for_socket(qtest_path, proc, 5.0)
            qmp = QMP(qmp_path)
            qtest = LineSocket(qtest_path)

            image_word0 = parse_qtest_value(qtest.send_line("readl 0x0"))
            image_word1 = parse_qtest_value(qtest.send_line("readl 0x4"))
            if image_word0 != 0x1000B000:
                raise RuntimeError(
                    f"VC4 image was not loaded at reset: "
                    f"word0=0x{image_word0:08x} word1=0x{image_word1:08x}"
                )

            cpus = qmp.execute("query-cpus-fast")
            if not isinstance(cpus, list) or len(cpus) != 2:
                raise RuntimeError(f"expected two heterogeneous CPUs, got {cpus!r}")

            qmp.execute("cont")
            deadline = time.monotonic() + 5.0
            marker = 0
            while time.monotonic() < deadline:
                marker = parse_qtest_value(qtest.send_line(f"readl 0x{MARKER_ADDR:x}"))
                if marker == MARKER_VALUE:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(f"QEMU exited with status {proc.returncode}")
                time.sleep(0.01)

            if marker != MARKER_VALUE:
                entry_lo = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x00:x}")
                )
                entry_hi = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x04:x}")
                )
                control = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x08:x}")
                )
                status = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x0c:x}")
                )
                count = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x10:x}")
                )
                vc4_pc = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x14:x}")
                )
                vc4_run = parse_qtest_value(
                    qtest.send_line(f"readl 0x{RELEASE_BASE + 0x18:x}")
                )
                image_word0 = parse_qtest_value(qtest.send_line("readl 0x0"))
                image_word1 = parse_qtest_value(qtest.send_line("readl 0x4"))
                entry = entry_lo | (entry_hi << 32)
                raise RuntimeError(
                    f"ARM marker never appeared: got 0x{marker:08x}, "
                    f"expected 0x{MARKER_VALUE:08x}; "
                    f"entry=0x{entry:016x} control=0x{control:08x} "
                    f"status=0x{status:08x} releases={count} "
                    f"vc4_pc=0x{vc4_pc:08x} vc4_run=0x{vc4_run:08x} "
                    f"word0=0x{image_word0:08x} word1=0x{image_word1:08x}"
                )

            status = parse_qtest_value(
                qtest.send_line(f"readl 0x{RELEASE_BASE + 0x0c:x}")
            )
            count = parse_qtest_value(
                qtest.send_line(f"readl 0x{RELEASE_BASE + 0x10:x}")
            )
            if status & 1 == 0 or count != 1:
                raise RuntimeError(
                    f"release device state is wrong: status=0x{status:08x} count={count}"
                )

            print(
                "VC4 -> ARM release passed: "
                f"cpus={len(cpus)} marker=0x{marker:08x} "
                f"status=0x{status:08x} releases={count}"
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
