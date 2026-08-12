#!/usr/bin/env python3
"""Exercise production VideoCore IV scalar floating-point encodings."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import ModuleType

RESULT_BASE = 0x00050000
DONE_SLOT = 20
DONE_VALUE = 0xF10A7E00

FADD = 0
FMUL = 2
FDIV = 3
FCMP = 4
FABS = 5
FRSB = 6
FMAX = 7
FRCP = 8
FRSQRT = 9
FNMUL = 10
FMIN = 11
FCEIL = 12
FFLOOR = 13
FLOG2 = 14
FEXP2 = 15

FTRUNC = 0
FLOOR = 1
FLTS = 2
FLTU = 3

ALWAYS = 14
EQ = 0

EXPECTED = {
    0: 0x4B927C00,   # fltu(19,200,000)
    1: 0x4199999A,   # 19,200,000.0 / 1,000,000.0 = 19.2
    2: 0x00000006,   # fcmp(19.2, 20.0): N | C
    3: 0xDEADBEEF,   # predicated-false fadd.eq does not write
    4: 0x00000013,   # ftrunc(19.2)
    5: 0xFFFFFFEC,   # floor(-19.2)
    6: 0xC1A00000,   # flts(-20)
    7: 0x4199999A,   # fabs(-19.2)
    8: 0x421CCCCD,   # 19.2 + 20.0
    9: 0x43C80000,   # 20.0 * 20.0
    10: 0x3F4CCCC0,  # 20.0 - 19.2
    11: 0x41A00000,  # max(19.2, 20.0)
    12: 0x4199999A,  # min(19.2, 20.0)
    13: 0x3E800000,  # reciprocal(4.0)
    14: 0x3F000000,  # reciprocal-sqrt(4.0)
    15: 0xC1800000,  # -(4.0 * 4.0)
    16: 0x41A00000,  # ceil(19.2)
    17: 0x41980000,  # floor(19.2), still represented as float32
    18: 0x40000000,  # log2(4.0)
    19: 0x41800000,  # exp2(4.0)
}


def load_handoff_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-0200-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_bootrom_0200", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load 0x200 handoff module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def vc4_float_op(smoke: ModuleType, op: int, rd: int, ra: int, rb: int,
                 cond: int = ALWAYS) -> bytes:
    i1 = 0xC800 | ((op & 0xF) << 5) | (rd & 0x1F)
    i2 = ((ra & 0x1F) << 11) | ((cond & 0xF) << 7) | (rb & 0x1F)
    return smoke.half(i1) + smoke.half(i2)


def vc4_float_conv(smoke: ModuleType, op: int, rd: int, ra: int,
                   shift: int = 0, cond: int = ALWAYS) -> bytes:
    i1 = 0xCA00 | ((op & 3) << 5) | (rd & 0x1F)
    i2 = ((ra & 0x1F) << 11) | ((cond & 0xF) << 7)
    i2 |= 0x40 | (shift & 0x3F)
    return smoke.half(i1) + smoke.half(i2)


def install_float_bootcode(smoke: ModuleType, handoff: ModuleType) -> None:
    handoff.install_real_handoff(smoke)

    def store(program: bytearray, reg: int, slot: int) -> None:
        program.extend(
            smoke.vc4_memory_offset(True, reg, 20, slot * 4)
        )

    def build_bootcode() -> bytes:
        program = bytearray()
        program += smoke.vc4_mov32(20, RESULT_BASE)

        program += smoke.vc4_mov32(0, 19_200_000)
        program += vc4_float_conv(smoke, FLTU, 0, 0)
        store(program, 0, 0)

        program += smoke.vc4_mov32(1, 0x49742400)  # 1,000,000.0f
        program += vc4_float_op(smoke, FDIV, 2, 0, 1)
        store(program, 2, 1)

        program += smoke.vc4_mov32(3, 0x41A00000)  # 20.0f
        program += vc4_float_op(smoke, FCMP, 0, 2, 3)
        store(program, 30, 2)  # architectural SR

        program += smoke.vc4_mov32(9, 0xDEADBEEF)
        program += vc4_float_op(smoke, FADD, 9, 2, 3, EQ)
        store(program, 9, 3)

        program += vc4_float_conv(smoke, FTRUNC, 4, 2)
        store(program, 4, 4)

        program += smoke.vc4_mov32(5, 0xC199999A)  # -19.2f
        program += vc4_float_conv(smoke, FLOOR, 6, 5)
        store(program, 6, 5)

        program += vc4_float_conv(smoke, FLTS, 7, 6)
        store(program, 7, 6)

        program += vc4_float_op(smoke, FABS, 8, 5, 5)
        store(program, 8, 7)

        program += vc4_float_op(smoke, FADD, 10, 2, 3)
        store(program, 10, 8)

        program += vc4_float_op(smoke, FMUL, 11, 3, 3)
        store(program, 11, 9)

        program += vc4_float_op(smoke, FRSB, 12, 2, 3)
        store(program, 12, 10)

        program += vc4_float_op(smoke, FMAX, 13, 2, 3)
        store(program, 13, 11)

        program += vc4_float_op(smoke, FMIN, 14, 2, 3)
        store(program, 14, 12)

        program += smoke.vc4_mov32(15, 0x40800000)  # 4.0f
        program += vc4_float_op(smoke, FRCP, 16, 15, 15)
        store(program, 16, 13)

        program += vc4_float_op(smoke, FRSQRT, 17, 15, 15)
        store(program, 17, 14)

        program += vc4_float_op(smoke, FNMUL, 18, 15, 15)
        store(program, 18, 15)

        program += vc4_float_op(smoke, FCEIL, 19, 2, 2)
        store(program, 19, 16)

        program += vc4_float_op(smoke, FFLOOR, 21, 2, 2)
        store(program, 21, 17)

        program += vc4_float_op(smoke, FLOG2, 22, 15, 15)
        store(program, 22, 18)

        program += vc4_float_op(smoke, FEXP2, 23, 15, 15)
        store(program, 23, 19)

        program += smoke.vc4_mov32(24, DONE_VALUE)
        store(program, 24, DONE_SLOT)
        program += smoke.half(0x0000)

        if len(program) > handoff.BOOT_PAYLOAD_SIZE:
            raise AssertionError("scalar-float program exceeds boot payload")
        program += bytes(handoff.BOOT_PAYLOAD_SIZE - len(program))
        image = bytes(handoff.BOOT_ENTRY) + bytes(program)
        if len(image) != smoke.BOOT_FILE_SIZE:
            raise AssertionError("unexpected scalar-float bootcode size")
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

    handoff = load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    install_float_bootcode(smoke, handoff)

    with tempfile.TemporaryDirectory(prefix="vc4-scalar-float-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "float-sd.img"
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
                    f"scalar-float program did not complete: 0x{done:08x}"
                )

            actual = {
                slot: smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{RESULT_BASE + slot * 4:x}")
                )
                for slot in EXPECTED
            }
            mismatches = {
                slot: (EXPECTED[slot], actual[slot])
                for slot in EXPECTED
                if actual[slot] != EXPECTED[slot]
            }
            if mismatches:
                details = ", ".join(
                    f"slot{slot}=0x{got:08x}/expected-0x{want:08x}"
                    for slot, (want, got) in mismatches.items()
                )
                raise RuntimeError(f"scalar-float mismatch: {details}")

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if "unimplemented opcode" in diagnostics:
                raise RuntimeError(
                    "scalar-float program reached the illegal-opcode path"
                )

            print(
                "VideoCore IV scalar floating point passed: "
                "ops=fltu,ftrunc,floor,flts,fadd,fmul,fdiv,fcmp,"
                "fabs,frsb,fmax,fmin,frcp,frsqrt,fnmul,fceil,ffloor,"
                f"flog2,fexp2 results={len(EXPECTED)} "
                f"flags=0x{actual[2]:08x} predication=preserved"
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
