#!/usr/bin/env python3
"""Exercise scalar48 PC-relative VideoCore IV load/store instructions."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from types import ModuleType

VPU_ENTRY = 0x3C000000
EXACT_PC = 0x000017AA
EXACT_LITERAL = 0x00009D90
NEAR_HALF = 0x00001800
NEAR_BYTE = 0x00001802
NEAR_SIGNED_HALF = 0x00001804
PCREL_STORE_SLOT = 0x00001810
NEGATIVE_LITERAL = 0x00001818
NEGATIVE_CODE = 0x00001830
RESULT_ADDR = 0x00046000

WORD_VALUE = 0xD15EA5ED
HALF_VALUE = 0xBEEF
BYTE_VALUE = 0xA5
SIGNED_HALF_VALUE = 0xFFFFFF80
NEGATIVE_VALUE = 0x89ABCDEF

EXPECTED = (
    WORD_VALUE,
    HALF_VALUE,
    BYTE_VALUE,
    SIGNED_HALF_VALUE,
    NEGATIVE_VALUE,
)

VC4_MOV = 0


def load_power_smoke() -> ModuleType:
    path = Path(__file__).with_name("raspi3-hetero-power-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_power_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load heterogeneous smoke helpers: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def half(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def word(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def vc4_alu_imm32(op: int, rd: int, value: int) -> bytes:
    return half(0xE800 | ((op & 0x1F) << 5) | (rd & 0x1F)) + word(value)


def vc4_mov32(rd: int, value: int) -> bytes:
    return vc4_alu_imm32(VC4_MOV, rd, value)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | ((fmt & 3) << 6)
    i1 |= rd & 0x1F
    if raw & 0x800:
        i1 |= 0x100
    i2 = ((rb & 0x1F) << 11) | (raw & 0x7FF)
    return half(i1) + half(i2)


def vc4_branch32(pc: int, target: int) -> bytes:
    delta = target - pc
    if delta & 1:
        raise ValueError("VC4 branch target is not halfword aligned")
    offset = delta // 2
    if not -(1 << 22) <= offset < (1 << 22):
        raise ValueError("VC4 branch target is outside scalar32 range")
    raw = offset & ((1 << 23) - 1)
    return half(0x9E00 | ((raw >> 16) & 0x7F)) + half(raw)


def vc4_branch16(pc: int, target: int) -> bytes:
    delta = target - pc
    if delta & 1:
        raise ValueError("VC4 branch target is not halfword aligned")
    offset = delta // 2
    if not -64 <= offset <= 63:
        raise ValueError("VC4 branch target is outside scalar16 range")
    return half(0x1F00 | (offset & 0x7F))


def vc4_pcrel48(store: bool, fmt: int, rd: int,
                 pc: int, target: int) -> bytes:
    offset = target - pc
    if not -(1 << 26) <= offset < (1 << 26):
        raise ValueError("PC-relative target is outside signed 27-bit range")
    raw = offset & ((1 << 27) - 1)
    i1 = (0xE700 | ((fmt & 3) << 6) |
          (0x20 if store else 0) | (rd & 0x1F))
    i2 = raw & 0xFFFF
    i3 = 0xF800 | ((raw >> 16) & 0x7FF)

    # VideoCore's scalar48 physical halfword order is short0, short2, short1.
    return half(i1) + half(i2) + half(i3)


def place(image: bytearray, offset: int, data: bytes) -> None:
    end = offset + len(data)
    if end > len(image):
        raise ValueError(f"write 0x{offset:x}..0x{end:x} exceeds firmware")
    if any(image[offset:end]):
        raise ValueError(f"firmware range 0x{offset:x}..0x{end:x} overlaps")
    image[offset:end] = data


def build_firmware(path: Path) -> bytes:
    image = bytearray(EXACT_LITERAL + 4)

    place(image, 0, vc4_branch32(0, EXACT_PC))

    pc = EXACT_PC
    exact = vc4_pcrel48(False, 0, 3, pc, EXACT_LITERAL)
    if exact != bytes.fromhex("03e7e68500f8"):
        raise AssertionError(f"stock instruction encoding changed: {exact.hex()}")
    place(image, pc, exact)
    pc += len(exact)

    place(image, pc, vc4_mov32(14, RESULT_ADDR))
    pc += 6
    place(image, pc, vc4_memory_offset(True, 3, 14, 0))
    pc += 4

    place(image, pc, vc4_pcrel48(False, 1, 4, pc, NEAR_HALF))
    pc += 6
    place(image, pc, vc4_memory_offset(True, 4, 14, 4))
    pc += 4

    place(image, pc, vc4_pcrel48(False, 2, 5, pc, NEAR_BYTE))
    pc += 6
    place(image, pc, vc4_memory_offset(True, 5, 14, 8))
    pc += 4

    place(image, pc, vc4_pcrel48(False, 3, 6, pc, NEAR_SIGNED_HALF))
    pc += 6
    place(image, pc, vc4_memory_offset(True, 6, 14, 12))
    pc += 4

    place(image, pc, vc4_pcrel48(True, 0, 3, pc, PCREL_STORE_SLOT))
    pc += 6
    place(image, pc, vc4_branch16(pc, NEGATIVE_CODE))

    place(image, NEAR_HALF, half(HALF_VALUE))
    place(image, NEAR_BYTE, bytes([BYTE_VALUE]))
    place(image, NEAR_SIGNED_HALF, half(SIGNED_HALF_VALUE))
    place(image, NEGATIVE_LITERAL, word(NEGATIVE_VALUE))

    pc = NEGATIVE_CODE
    place(image, pc, vc4_pcrel48(False, 0, 7, pc, NEGATIVE_LITERAL))
    pc += 6
    place(image, pc, vc4_memory_offset(True, 7, 14, 16))
    pc += 4
    place(image, pc, half(0x0000))  # development HALT

    place(image, EXACT_LITERAL, word(WORD_VALUE))
    path.write_bytes(image)
    return bytes(image)


def readl(qtest: object, power: ModuleType, address: int) -> int:
    return power.parse_qtest_value(qtest.send_line(f"readl 0x{address:x}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    power = load_power_smoke()

    with tempfile.TemporaryDirectory(prefix="vc4-pcrel48-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "pcrel48.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        firmware = build_firmware(firmware_path)

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

        qmp = None
        qtest = None
        try:
            power.wait_for_socket(qmp_path, proc, 10.0)
            power.wait_for_socket(qtest_path, proc, 10.0)
            qmp = power.QMP(qmp_path)
            qtest = power.LineSocket(qtest_path)
            qom_types, arm_count, vc4_count = power.validate_cpu_topology(
                qmp.execute("query-cpus-fast")
            )

            loaded = bytes(
                power.parse_qtest_value(
                    qtest.send_line(f"readb 0x{VPU_ENTRY + EXACT_PC + i:x}")
                )
                for i in range(6)
            )
            if loaded != bytes.fromhex("03e7e68500f8"):
                raise RuntimeError(
                    "exact stock scalar48 instruction was not loaded: "
                    f"{loaded.hex()}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = (0,) * len(EXPECTED)
            pcrel_store = 0
            while time.monotonic() < deadline:
                observed = tuple(
                    readl(qtest, power, RESULT_ADDR + index * 4)
                    for index in range(len(EXPECTED))
                )
                pcrel_store = readl(
                    qtest, power, VPU_ENTRY + PCREL_STORE_SLOT
                )
                if observed == EXPECTED and pcrel_store == WORD_VALUE:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if observed != EXPECTED or pcrel_store != WORD_VALUE:
                raise RuntimeError(
                    "scalar48 PC-relative load/store mismatch: "
                    f"observed={[f'0x{x:08x}' for x in observed]} "
                    f"store=0x{pcrel_store:08x}"
                )

            print(
                "VideoCore IV scalar48 PC-relative memory passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"exact-pc=0x{EXACT_PC:08x} exact-opcode=03e7e68500f8 "
                f"literal=0x{WORD_VALUE:08x} "
                f"half=0x{HALF_VALUE:04x} byte=0x{BYTE_VALUE:02x} "
                f"signed-half=0x{SIGNED_HALF_VALUE:08x} "
                f"negative=0x{NEGATIVE_VALUE:08x} "
                f"pcrel-store=0x{pcrel_store:08x}"
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
