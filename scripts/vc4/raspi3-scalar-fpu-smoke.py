#!/usr/bin/env python3
"""Exercise scalar VideoCore IV floating-point instructions under TCG."""

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
RESULT_ADDR = 0x00042000

INPUT_INTEGER = 0x0124F800          # 19,200,000
INPUT_FLOAT = 0x4B927C00            # float(19,200,000)
ONE_MILLION_FLOAT = 0x49742400
DIV_RESULT = 0x4199999A             # float32(19.2)
NEG_TWO_FLOAT = 0xC0000000
NEG_TWO_INTEGER = 0xFFFFFFFE
EQUAL_FLAGS = 0x00000008
LESS_FLAGS = 0x00000006

VC4_MOV = 0
VC4_FTRUNC = 0
VC4_FLTS = 2
VC4_FLTU = 3
VC4_FDIV = 3
VC4_FCMP = 4
VC4_SR = 30


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


def vc4_float_conv(op: int, rd: int, ra: int, shift: int = 0,
                   cond: int = 14) -> bytes:
    i1 = 0xCA00 | ((op & 3) << 5) | (rd & 0x1F)
    i2 = ((ra & 0x1F) << 11) | ((cond & 0xF) << 7) | 0x40
    i2 |= shift & 0x3F
    return half(i1) + half(i2)


def vc4_float_reg(op: int, rd: int, ra: int, rb: int,
                  cond: int = 14) -> bytes:
    i1 = 0xC800 | ((op & 0xF) << 5) | (rd & 0x1F)
    i2 = ((ra & 0x1F) << 11) | ((cond & 0xF) << 7) | (rb & 0x1F)
    return half(i1) + half(i2)


def vc4_mov_reg(rd: int, rb: int, cond: int = 14) -> bytes:
    i1 = 0xC000 | (rd & 0x1F)
    i2 = ((cond & 0xF) << 7) | (rb & 0x1F)
    return half(i1) + half(i2)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | ((fmt & 3) << 6)
    i1 |= rd & 0x1F
    if raw & 0x800:
        i1 |= 0x100
    i2 = ((rb & 0x1F) << 11) | (raw & 0x7FF)
    return half(i1) + half(i2)


def build_firmware(path: Path) -> bytes:
    code = bytearray()

    # This exact FLTU is the first scalar-FPU instruction reached by the
    # pinned official bootcode.bin at PC 0x269a.
    code += vc4_mov32(0, INPUT_INTEGER)
    code += vc4_float_conv(VC4_FLTU, 0, 0)

    code += vc4_mov32(1, ONE_MILLION_FLOAT)
    code += vc4_float_reg(VC4_FDIV, 2, 0, 1)

    code += vc4_float_reg(VC4_FCMP, 0, 2, 2)
    code += vc4_mov_reg(3, VC4_SR)
    code += vc4_float_reg(VC4_FCMP, 0, 2, 0)
    code += vc4_mov_reg(5, VC4_SR)

    code += vc4_mov32(6, NEG_TWO_INTEGER)
    code += vc4_float_conv(VC4_FLTS, 6, 6)
    code += vc4_float_conv(VC4_FTRUNC, 7, 6)

    code += vc4_mov32(4, RESULT_ADDR)
    for offset, reg in (
        (0, 0),
        (4, 2),
        (8, 3),
        (12, 5),
        (16, 6),
        (20, 7),
    ):
        code += vc4_memory_offset(True, reg, 4, offset)

    code += half(0x0000)            # development HALT
    path.write_bytes(code)
    return bytes(code)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    power = load_power_smoke()

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-fpu-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "scalar-fpu.bin"
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

            expected0 = int.from_bytes(firmware[0:4], "little")
            expected1 = int.from_bytes(firmware[4:8], "little")
            image0 = power.parse_qtest_value(
                qtest.send_line(f"readl 0x{VPU_ENTRY:x}")
            )
            image1 = power.parse_qtest_value(
                qtest.send_line(f"readl 0x{VPU_ENTRY + 4:x}")
            )
            if (image0, image1) != (expected0, expected1):
                raise RuntimeError(
                    "scalar-FPU image was not loaded into VPU memory: "
                    f"got 0x{image0:08x}/0x{image1:08x}, "
                    f"expected 0x{expected0:08x}/0x{expected1:08x}"
                )

            expected = (
                INPUT_FLOAT,
                DIV_RESULT,
                EQUAL_FLAGS,
                LESS_FLAGS,
                NEG_TWO_FLOAT,
                NEG_TWO_INTEGER,
            )
            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            values = (0,) * len(expected)
            while time.monotonic() < deadline:
                values = tuple(
                    power.parse_qtest_value(
                        qtest.send_line(f"readl 0x{RESULT_ADDR + i * 4:x}")
                    )
                    for i in range(len(expected))
                )
                if values == expected:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if values != expected:
                formatted = ", ".join(f"0x{value:08x}" for value in values)
                wanted = ", ".join(f"0x{value:08x}" for value in expected)
                raise RuntimeError(
                    f"scalar-FPU results were [{formatted}], expected [{wanted}]"
                )

            print(
                "VideoCore IV scalar FPU passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"fltu=0x{values[0]:08x} fdiv=0x{values[1]:08x} "
                f"fcmp-eq=0x{values[2]:08x} "
                f"fcmp-lt=0x{values[3]:08x} "
                f"flts=0x{values[4]:08x} "
                f"ftrunc=0x{values[5]:08x}"
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
