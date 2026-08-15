#!/usr/bin/env python3
"""Exercise signed VideoCore IV scalar ALU 16-bit immediates."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import ModuleType

VPU_ENTRY = 0x3C000000
RESULT_ADDR = 0x00046000

VC4_ADD = 2

NEGATIVE_FRAME = -520
POSITIVE_FRAME = 520
MIN_SIGNED = -32768
MAX_SIGNED = 32767

NEGATIVE_FRAME_BYTES = bytes.fromhex("40 b0 f8 fd")
POSITIVE_FRAME_BYTES = bytes.fromhex("41 b0 08 02")
SP_NEGATIVE_FRAME_BYTES = bytes.fromhex("59 b0 f8 fd")
SP_POSITIVE_FRAME_BYTES = bytes.fromhex("59 b0 08 02")

EXPECTED = (
    0x00000DF8,
    0x00001208,
    0x00000000,
    0x00007FFF,
    0x000FFDF8,
    0x00100000,
)


def load_helper(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load helper module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vc4_alu_imm16(vc4: ModuleType, op: int, rd: int, value: int) -> bytes:
    if not -32768 <= value <= 32767:
        raise ValueError(f"scalar ALU imm16 is outside int16 range: {value}")
    i1 = 0xB000 | ((op & 0x1F) << 5) | (rd & 0x1F)
    return vc4.half(i1) + vc4.half(value)


def place(image: bytearray, offset: int, data: bytes) -> int:
    end = offset + len(data)
    if end > len(image):
        raise ValueError(f"write 0x{offset:x}..0x{end:x} exceeds firmware")
    if any(image[offset:end]):
        raise ValueError(f"firmware range 0x{offset:x}..0x{end:x} overlaps")
    image[offset:end] = data
    return end


def build_firmware(path: Path, vc4: ModuleType) -> bytes:
    image = bytearray(0x200)
    pc = 0

    pc = place(image, pc, vc4.vc4_mov32(14, RESULT_ADDR))

    pc = place(image, pc, vc4.vc4_mov32(0, 0x1000))
    negative_pc = pc
    negative = vc4_alu_imm16(vc4, VC4_ADD, 0, NEGATIVE_FRAME)
    if negative != NEGATIVE_FRAME_BYTES:
        raise AssertionError(f"negative ADD encoding changed: {negative.hex()}")
    pc = place(image, pc, negative)
    pc = place(image, pc, vc4.vc4_memory_offset(True, 0, 14, 0))

    pc = place(image, pc, vc4.vc4_mov32(1, 0x1000))
    positive_pc = pc
    positive = vc4_alu_imm16(vc4, VC4_ADD, 1, POSITIVE_FRAME)
    if positive != POSITIVE_FRAME_BYTES:
        raise AssertionError(f"positive ADD encoding changed: {positive.hex()}")
    pc = place(image, pc, positive)
    pc = place(image, pc, vc4.vc4_memory_offset(True, 1, 14, 4))

    pc = place(image, pc, vc4.vc4_mov32(2, 0x8000))
    pc = place(image, pc, vc4_alu_imm16(vc4, VC4_ADD, 2, MIN_SIGNED))
    pc = place(image, pc, vc4.vc4_memory_offset(True, 2, 14, 8))

    pc = place(image, pc, vc4.vc4_mov32(3, 0))
    pc = place(image, pc, vc4_alu_imm16(vc4, VC4_ADD, 3, MAX_SIGNED))
    pc = place(image, pc, vc4.vc4_memory_offset(True, 3, 14, 12))

    pc = place(image, pc, vc4.vc4_mov32(25, 0x00100000))
    sp_negative_pc = pc
    sp_negative = vc4_alu_imm16(vc4, VC4_ADD, 25, NEGATIVE_FRAME)
    if sp_negative != SP_NEGATIVE_FRAME_BYTES:
        raise AssertionError(
            f"negative SP ADD encoding changed: {sp_negative.hex()}"
        )
    pc = place(image, pc, sp_negative)
    pc = place(image, pc, vc4.vc4_memory_offset(True, 25, 14, 16))

    sp_positive_pc = pc
    sp_positive = vc4_alu_imm16(vc4, VC4_ADD, 25, POSITIVE_FRAME)
    if sp_positive != SP_POSITIVE_FRAME_BYTES:
        raise AssertionError(
            f"positive SP ADD encoding changed: {sp_positive.hex()}"
        )
    pc = place(image, pc, sp_positive)
    pc = place(image, pc, vc4.vc4_memory_offset(True, 25, 14, 20))

    pc = place(image, pc, vc4.half(0x0000))
    firmware = bytes(image[:pc])
    path.write_bytes(firmware)

    checks = (
        (negative_pc, NEGATIVE_FRAME_BYTES),
        (positive_pc, POSITIVE_FRAME_BYTES),
        (sp_negative_pc, SP_NEGATIVE_FRAME_BYTES),
        (sp_positive_pc, SP_POSITIVE_FRAME_BYTES),
    )
    for offset, expected in checks:
        observed = firmware[offset:offset + len(expected)]
        if observed != expected:
            raise AssertionError(
                f"fixture changed at 0x{offset:x}: "
                f"{observed.hex()} != {expected.hex()}"
            )
    return firmware


def readl(qtest: object, power: ModuleType, address: int) -> int:
    return power.parse_qtest_value(qtest.send_line(f"readl 0x{address:x}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    power = load_helper("raspi3-hetero-power-smoke.py", "vc4_power_smoke")
    vc4 = load_helper("raspi3-pcrel48-smoke.py", "vc4_pcrel_smoke")

    with tempfile.TemporaryDirectory(prefix="vc4-signed-alu-imm16-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "signed-alu-imm16.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        firmware = build_firmware(firmware_path, vc4)

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
                command, stdout=subprocess.DEVNULL, stderr=stderr
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
                    qtest.send_line(f"readb 0x{VPU_ENTRY + index:x}")
                )
                for index in range(len(firmware))
            )
            if loaded != firmware:
                raise RuntimeError("signed ALU imm16 fixture was not loaded")

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = (0,) * len(EXPECTED)
            while time.monotonic() < deadline:
                observed = tuple(
                    readl(qtest, power, RESULT_ADDR + index * 4)
                    for index in range(len(EXPECTED))
                )
                if observed == EXPECTED:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if observed != EXPECTED:
                raise RuntimeError(
                    "signed ALU imm16 marker mismatch: "
                    f"got {[f'0x{x:08x}' for x in observed]}, "
                    f"expected {[f'0x{x:08x}' for x in EXPECTED]}"
                )

            print(
                "VideoCore IV signed scalar ALU imm16 passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"negative={NEGATIVE_FRAME} positive={POSITIVE_FRAME} "
                f"min={MIN_SIGNED} max={MAX_SIGNED} "
                f"sp-restored=0x{observed[-1]:08x}"
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
