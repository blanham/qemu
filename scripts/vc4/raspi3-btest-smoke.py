#!/usr/bin/env python3
"""Exercise VideoCore IV BTEST selected-bit flag semantics."""

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

EXACT_LOOP_HEAD = 0x0000981A
EXACT_BTEST_PC = 0x00009820
EXACT_BTEST_BYTES = bytes.fromhex("70 6c 7c 18")

SET_TEST_PC = 0x00009860
SET_GOOD_PC = 0x00009890
REG_TEST_PC = 0x000098C0
REG_GOOD_PC = 0x000098F0

CLEAR_MARKER = 0x42544330
SET_MARKER = 0x42545331
REG_MARKER = 0x42545232
BAD_SET_MARKER = 0xBAD50001
BAD_REG_MARKER = 0xBAD50002
EXPECTED = (CLEAR_MARKER, SET_MARKER, REG_MARKER)

COND_EQ = 0
VC4_BTST = 12


def load_helper(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load helper module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vc4_alu_imm16(vc4: ModuleType, op: int, rd: int, value: int) -> bytes:
    if value < 0 or value > 31:
        raise ValueError(f"scalar16 ALU immediate is outside 0..31: {value}")
    if op & 1:
        raise ValueError(f"scalar16 ALU immediate requires an even opcode: {op}")
    q = op // 2
    return vc4.half(0x6000 | ((q & 0xF) << 9) |
                    ((value & 0x1F) << 4) | (rd & 0xF))


def vc4_alu_reg16(vc4: ModuleType, op: int, rd: int, rs: int) -> bytes:
    return vc4.half(0x4000 | ((op & 0x1F) << 8) |
                    ((rs & 0xF) << 4) | (rd & 0xF))


def vc4_cond_branch16(vc4: ModuleType, cond: int,
                      pc: int, target: int) -> bytes:
    delta = target - pc
    if delta & 1:
        raise ValueError("VC4 branch target is not halfword aligned")
    offset = delta // 2
    if not -64 <= offset <= 63:
        raise ValueError("VC4 conditional branch is outside scalar16 range")
    return vc4.half(0x1800 | ((cond & 0xF) << 7) | (offset & 0x7F))


class ImageBuilder:
    def __init__(self, size: int) -> None:
        self.image = bytearray(size)
        self.ranges: list[tuple[int, int, str]] = []

    def place(self, offset: int, data: bytes, label: str) -> None:
        end = offset + len(data)
        if offset < 0 or end > len(self.image):
            raise ValueError(
                f"{label}: range 0x{offset:x}..0x{end:x} is outside image"
            )
        for old_start, old_end, old_label in self.ranges:
            if offset < old_end and old_start < end:
                raise ValueError(
                    f"{label}: overlaps {old_label} "
                    f"(0x{old_start:x}..0x{old_end:x})"
                )
        self.image[offset:end] = data
        self.ranges.append((offset, end, label))


def marker_code(vc4: ModuleType, marker: int, slot: int) -> bytes:
    return (
        vc4.vc4_mov32(2, marker)
        + vc4.vc4_memory_offset(True, 2, 14, slot * 4)
    )


def build_firmware(path: Path, vc4: ModuleType) -> bytes:
    b = ImageBuilder(0x9920)

    entry = bytearray()
    entry += vc4.vc4_mov32(14, RESULT_ADDR)
    entry += vc4.vc4_branch32(len(entry), EXACT_LOOP_HEAD)
    b.place(0, bytes(entry), "entry")

    exact = bytearray()
    exact += vc4_alu_imm16(vc4, 0, 0, 0)  # mov r0, 0
    exact += vc4.half(0x0001)              # nop
    exact += vc4.half(0x0001)              # nop
    exact += vc4_alu_imm16(vc4, VC4_BTST, 0, 7)
    exact += vc4_cond_branch16(
        vc4, COND_EQ, EXACT_BTEST_PC + 2, EXACT_LOOP_HEAD
    )
    if bytes(exact[-4:]) != EXACT_BTEST_BYTES:
        raise AssertionError(
            f"stock BTEST/BEQ bytes changed: {bytes(exact[-4:]).hex()}"
        )
    b.place(EXACT_LOOP_HEAD, bytes(exact), "stock clear-bit loop")

    clear_path = bytearray(marker_code(vc4, CLEAR_MARKER, 0))
    clear_path += vc4.vc4_branch32(
        EXACT_BTEST_PC + len(EXACT_BTEST_BYTES) + len(clear_path),
        SET_TEST_PC,
    )
    b.place(
        EXACT_BTEST_PC + len(EXACT_BTEST_BYTES),
        bytes(clear_path),
        "clear-bit fall-through",
    )

    set_test = bytearray()
    set_test += vc4.vc4_mov32(0, 1 << 7)
    set_test += vc4_alu_imm16(vc4, VC4_BTST, 0, 7)
    branch_pc = SET_TEST_PC + len(set_test)
    set_test += vc4_cond_branch16(vc4, COND_EQ, branch_pc, SET_GOOD_PC)
    set_test += marker_code(vc4, BAD_SET_MARKER, 1)
    set_test += vc4.half(0x0000)
    b.place(SET_TEST_PC, bytes(set_test), "set-bit immediate test")

    set_good = bytearray(marker_code(vc4, SET_MARKER, 1))
    set_good += vc4.vc4_branch32(
        SET_GOOD_PC + len(set_good), REG_TEST_PC
    )
    b.place(SET_GOOD_PC, bytes(set_good), "set-bit immediate success")

    reg_test = bytearray()
    reg_test += vc4.vc4_mov32(0, 1 << 7)
    reg_test += vc4.vc4_mov32(1, 7)
    reg_test += vc4_alu_reg16(vc4, VC4_BTST, 0, 1)
    branch_pc = REG_TEST_PC + len(reg_test)
    reg_test += vc4_cond_branch16(vc4, COND_EQ, branch_pc, REG_GOOD_PC)
    reg_test += marker_code(vc4, BAD_REG_MARKER, 2)
    reg_test += vc4.half(0x0000)
    b.place(REG_TEST_PC, bytes(reg_test), "set-bit register test")

    b.place(
        REG_GOOD_PC,
        marker_code(vc4, REG_MARKER, 2) + vc4.half(0x0000),
        "set-bit register success",
    )

    firmware = bytes(b.image)
    observed = firmware[EXACT_BTEST_PC:EXACT_BTEST_PC + 4]
    if observed != EXACT_BTEST_BYTES:
        raise AssertionError(
            f"exact stock BTEST fixture changed: {observed.hex()}"
        )
    path.write_bytes(firmware)
    return firmware


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    power = load_helper("raspi3-hetero-power-smoke.py", "vc4_power_smoke")
    vc4 = load_helper("raspi3-pcrel48-smoke.py", "vc4_pcrel_smoke")

    with tempfile.TemporaryDirectory(prefix="vc4-btest-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "btest.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        build_firmware(firmware_path, vc4)

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

            exact = bytes(
                power.parse_qtest_value(
                    qtest.send_line(
                        f"readb 0x{VPU_ENTRY + EXACT_BTEST_PC + index:x}"
                    )
                )
                for index in range(len(EXACT_BTEST_BYTES))
            )
            if exact != EXACT_BTEST_BYTES:
                raise RuntimeError(
                    "exact stock BTEST/BEQ fixture was not loaded: "
                    f"got {exact.hex()}, expected {EXACT_BTEST_BYTES.hex()}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = (0,) * len(EXPECTED)
            while time.monotonic() < deadline:
                observed = tuple(
                    power.parse_qtest_value(
                        qtest.send_line(
                            f"readl 0x{RESULT_ADDR + index * 4:x}"
                        )
                    )
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
                    "BTEST marker mismatch: "
                    f"got {[f'0x{x:08x}' for x in observed]}, "
                    f"expected {[f'0x{x:08x}' for x in EXPECTED]}"
                )

            print(
                "VideoCore IV BTEST selected-bit flags passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"stock-pc=0x{EXACT_BTEST_PC:08x} "
                f"stock-bytes={EXACT_BTEST_BYTES.hex()} "
                f"clear=0x{CLEAR_MARKER:08x} "
                f"set=0x{SET_MARKER:08x} "
                f"register=0x{REG_MARKER:08x}"
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
