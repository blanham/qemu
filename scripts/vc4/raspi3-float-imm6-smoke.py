#!/usr/bin/env python3
"""Exercise every VideoCore IV six-bit scalar floating immediate."""

from __future__ import annotations

import argparse
import importlib.util
import math
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import time
from types import ModuleType

RESULT_BASE = 0x00052000

IMM_COUNT = 64
PREDICATE_SLOT = 64
COMPARE_SLOT = 65
ADD_SLOT = 66
MULTIPLY_SLOT = 67
DIVIDE_SLOT = 68
REVERSE_SUB_SLOT = 69
ABS_SLOT = 70
DONE_SLOT = 71
DONE_VALUE = 0xF1061A6E

FADD = 0
FMUL = 2
FDIV = 3
FCMP = 4
FABS = 5
FRSB = 6

ALWAYS = 14
EQ = 0


def load_handoff_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-0200-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_bootrom_0200", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load 0x200 handoff module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vc4_float_op_reg(smoke: ModuleType, op: int, rd: int, ra: int, rb: int,
                     cond: int = ALWAYS) -> bytes:
    i1 = 0xC800 | ((op & 0xF) << 5) | (rd & 0x1F)
    i2 = ((ra & 0x1F) << 11) | ((cond & 0xF) << 7) | (rb & 0x1F)
    return smoke.half(i1) + smoke.half(i2)


def vc4_float_op_imm(smoke: ModuleType, op: int, rd: int, ra: int, imm: int,
                     cond: int = ALWAYS) -> bytes:
    if not 0 <= imm < IMM_COUNT:
        raise ValueError(f"floating immediate is out of range: {imm}")
    i1 = 0xC800 | ((op & 0xF) << 5) | (rd & 0x1F)
    i2 = (
        ((ra & 0x1F) << 11)
        | ((cond & 0xF) << 7)
        | 0x40
        | imm
    )
    return smoke.half(i1) + smoke.half(i2)


def float32_bits(value: float) -> int:
    return struct.unpack("<I", struct.pack("<f", value))[0]


def vc4_float_imm6_value(imm: int) -> float:
    """Decode mathematically, independently of QEMU's bit expansion."""
    if not 0 <= imm < IMM_COUNT:
        raise ValueError(f"floating immediate is out of range: {imm}")

    negative = bool(imm & 0x20)
    exponent = (imm >> 2) & 0x7
    if exponent == 0:
        return math.copysign(0.0, -1.0 if negative else 1.0)

    significand = 1.0 + (imm & 0x3) / 4.0
    value = math.ldexp(significand, exponent - 3)
    return -value if negative else value


def expected_results() -> dict[int, int]:
    expected = {
        imm: float32_bits(vc4_float_imm6_value(imm))
        for imm in range(IMM_COUNT)
    }
    expected.update({
        PREDICATE_SLOT: 0xDEADBEEF,
        COMPARE_SLOT: 0x00000006,
        ADD_SLOT: 0x40900000,
        MULTIPLY_SLOT: 0x40000000,
        DIVIDE_SLOT: 0x41000000,
        REVERSE_SUB_SLOT: 0xC0600000,
        ABS_SLOT: 0x3F000000,
    })

    known = {
        0: 0x00000000,
        3: 0x00000000,
        4: 0x3E800000,
        8: 0x3F000000,
        12: 0x3F800000,
        16: 0x40000000,
        20: 0x40800000,
        24: 0x41000000,
        28: 0x41800000,
        31: 0x41E00000,
        32: 0x80000000,
        35: 0x80000000,
        36: 0xBE800000,
        40: 0xBF000000,
        63: 0xC1E00000,
    }
    for imm, bits in known.items():
        if expected[imm] != bits:
            raise AssertionError(
                f"reference decoder mismatch for #{imm}: "
                f"0x{expected[imm]:08x} != 0x{bits:08x}"
            )
    return expected


def install_float_imm_bootcode(smoke: ModuleType, handoff: ModuleType) -> None:
    handoff.install_real_handoff(smoke)

    def store(program: bytearray, reg: int, slot: int) -> None:
        program.extend(smoke.vc4_memory_offset(True, reg, 20, slot * 4))

    def build_bootcode() -> bytes:
        program = bytearray()
        program += smoke.vc4_mov32(20, RESULT_BASE)
        program += smoke.vc4_mov32(21, 0x3F800000)

        for imm in range(IMM_COUNT):
            program += vc4_float_op_imm(smoke, FMUL, 22, 21, imm)
            store(program, 22, imm)

        program += smoke.vc4_mov32(23, 0x3E800000)
        program += smoke.vc4_mov32(24, 0x3F000000)
        program += vc4_float_op_reg(smoke, FCMP, 0, 23, 24)
        program += smoke.vc4_mov32(25, 0xDEADBEEF)
        program += vc4_float_op_imm(smoke, FADD, 25, 23, 8, EQ)
        store(program, 25, PREDICATE_SLOT)

        program += vc4_float_op_imm(smoke, FCMP, 0, 23, 8)
        store(program, 30, COMPARE_SLOT)

        program += smoke.vc4_mov32(23, 0x40800000)
        program += vc4_float_op_imm(smoke, FADD, 26, 23, 8)
        store(program, 26, ADD_SLOT)
        program += vc4_float_op_imm(smoke, FMUL, 27, 23, 8)
        store(program, 27, MULTIPLY_SLOT)
        program += vc4_float_op_imm(smoke, FDIV, 28, 23, 8)
        store(program, 28, DIVIDE_SLOT)
        program += vc4_float_op_imm(smoke, FRSB, 29, 23, 8)
        store(program, 29, REVERSE_SUB_SLOT)
        program += vc4_float_op_imm(smoke, FABS, 22, 23, 40)
        store(program, 22, ABS_SLOT)

        program += smoke.vc4_mov32(24, DONE_VALUE)
        store(program, 24, DONE_SLOT)
        program += smoke.half(0x0000)

        if len(program) > handoff.BOOT_PAYLOAD_SIZE:
            raise AssertionError(
                "float-immediate program exceeds boot payload: "
                f"{len(program)} > {handoff.BOOT_PAYLOAD_SIZE}"
            )
        program += bytes(handoff.BOOT_PAYLOAD_SIZE - len(program))
        image = bytes(handoff.BOOT_ENTRY) + bytes(program)
        if len(image) != smoke.BOOT_FILE_SIZE:
            raise AssertionError("unexpected float-immediate bootcode size")
        return image

    smoke.build_bootcode = build_bootcode


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    expected = expected_results()
    handoff = load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    install_float_imm_bootcode(smoke, handoff)

    with tempfile.TemporaryDirectory(prefix="vc4-float-imm6-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "float-imm6-sd.img"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        smoke.build_sd_image(image_path, smoke.build_bootcode())

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-d", "unimp,guest_errors",
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
            smoke.wait_for_socket(qmp_path, proc, 10.0)
            smoke.wait_for_socket(qtest_path, proc, 10.0)
            qmp = smoke.QMP(qmp_path)
            qtest = smoke.LineSocket(qtest_path)
            smoke.validate_cpu_topology(qmp.execute("query-cpus-fast"))
            qmp.execute("cont")

            deadline = time.monotonic() + 10.0
            done = 0
            while time.monotonic() < deadline:
                done = smoke.parse_qtest_value(
                    qtest.send_line(
                        f"readl 0x{RESULT_BASE + DONE_SLOT * 4:x}"
                    )
                )
                if done == DONE_VALUE:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if done != DONE_VALUE:
                raise RuntimeError(
                    f"float-immediate program did not complete: 0x{done:08x}"
                )

            actual = {
                slot: smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{RESULT_BASE + slot * 4:x}")
                )
                for slot in expected
            }
            mismatches = {
                slot: (expected[slot], actual[slot])
                for slot in expected
                if actual[slot] != expected[slot]
            }
            if mismatches:
                details = ", ".join(
                    f"slot{slot}=0x{got:08x}/expected-0x{want:08x}"
                    for slot, (want, got) in mismatches.items()
                )
                raise RuntimeError(f"float-immediate mismatch: {details}")

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if "unimplemented opcode" in diagnostics.lower():
                raise RuntimeError(
                    "float-immediate program reached the illegal-opcode path"
                )

            print(
                "VideoCore IV six-bit floating immediates passed: "
                f"encodings={IMM_COUNT} checks={len(expected)} "
                "ops=fadd,fmul,fdiv,fcmp,frsb,fabs "
                "predication=preserved signed-zero=preserved "
                "nonzero-range=-28.0..+28.0"
            )
            qmp.execute("quit")
            proc.wait(timeout=5)
            return 0
        except Exception:
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
            stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
