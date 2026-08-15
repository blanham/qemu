#!/usr/bin/env python3
"""Exercise the VideoCore-visible CPRMAN SDRAM-clock handshake."""

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
RESULT_ADDR = 0x00043000
CM_BASE_GPU = 0x7E101000
CM_SDCCTL_OFFSET = 0x1A8
CM_SDCCTL_ARM = 0x3F1011A8
CM_PASSWORD = 0x5A000000
CM_SDCCTL_UPDATE = 1 << 17
CM_SDCCTL_ACCPT = 1 << 16

BEGIN_VALUE = 0x00024091
BEGIN_ACCEPTED = BEGIN_VALUE | CM_SDCCTL_ACCPT
END_VALUE = 0x00004011
END_ACCEPTED = 0x00004091  # existing mux model reflects ENABLE in BUSY

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


def build_firmware(path: Path) -> bytes:
    code = bytearray()
    code += vc4_mov32(0, CM_BASE_GPU)

    # clkman_update_begin(): assert UPDATE and wait for ACCPT.
    code += vc4_mov32(1, CM_PASSWORD | BEGIN_VALUE)
    code += vc4_memory_offset(True, 1, 0, CM_SDCCTL_OFFSET)
    code += vc4_memory_offset(False, 2, 0, CM_SDCCTL_OFFSET)

    # clkman_update_end(): clear UPDATE and wait for ACCPT to clear.
    code += vc4_mov32(1, CM_PASSWORD | END_VALUE)
    code += vc4_memory_offset(True, 1, 0, CM_SDCCTL_OFFSET)
    code += vc4_memory_offset(False, 3, 0, CM_SDCCTL_OFFSET)

    code += vc4_mov32(4, RESULT_ADDR)
    code += vc4_memory_offset(True, 2, 4, 0)
    code += vc4_memory_offset(True, 3, 4, 4)
    code += half(0x0000)  # development HALT
    path.write_bytes(code)
    return bytes(code)


def readl(qtest: object, power: ModuleType, address: int) -> int:
    return power.parse_qtest_value(
        qtest.send_line(f"readl 0x{address:x}")
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    power = load_power_smoke()

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-sdcclk-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "sdc-clock-handshake.bin"
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
            image0 = readl(qtest, power, VPU_ENTRY)
            image1 = readl(qtest, power, VPU_ENTRY + 4)
            if (image0, image1) != (expected0, expected1):
                raise RuntimeError(
                    "SDC-clock firmware was not loaded into VPU memory: "
                    f"got 0x{image0:08x}/0x{image1:08x}, "
                    f"expected 0x{expected0:08x}/0x{expected1:08x}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            begin = end = 0
            while time.monotonic() < deadline:
                begin = readl(qtest, power, RESULT_ADDR)
                end = readl(qtest, power, RESULT_ADDR + 4)
                if (begin, end) == (BEGIN_ACCEPTED, END_ACCEPTED):
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if (begin, end) != (BEGIN_ACCEPTED, END_ACCEPTED):
                raise RuntimeError(
                    "VPU SDC-clock handshake did not complete: "
                    f"begin=0x{begin:08x}, end=0x{end:08x}; "
                    f"expected 0x{BEGIN_ACCEPTED:08x}/0x{END_ACCEPTED:08x}"
                )

            arm_view = readl(qtest, power, CM_SDCCTL_ARM)
            if arm_view != END_ACCEPTED:
                raise RuntimeError(
                    "ARM and VideoCore CPRMAN views diverged: "
                    f"ARM read 0x{arm_view:08x}, expected 0x{END_ACCEPTED:08x}"
                )

            # A password-less write must remain ignored after the VPU sequence.
            power.qtest_writel(qtest, CM_SDCCTL_ARM, BEGIN_VALUE)
            after_invalid = readl(qtest, power, CM_SDCCTL_ARM)
            if after_invalid != END_ACCEPTED:
                raise RuntimeError(
                    "CPRMAN accepted an invalid-password SDCCTL write: "
                    f"0x{after_invalid:08x}"
                )

            # Exercise the same two edges through the ARM peripheral view.
            power.qtest_writel(
                qtest, CM_SDCCTL_ARM, CM_PASSWORD | BEGIN_VALUE
            )
            arm_begin = readl(qtest, power, CM_SDCCTL_ARM)
            power.qtest_writel(
                qtest, CM_SDCCTL_ARM, CM_PASSWORD | END_VALUE
            )
            arm_end = readl(qtest, power, CM_SDCCTL_ARM)
            if (arm_begin, arm_end) != (BEGIN_ACCEPTED, END_ACCEPTED):
                raise RuntimeError(
                    "ARM SDC-clock handshake mismatch: "
                    f"0x{arm_begin:08x}/0x{arm_end:08x}"
                )

            print(
                "BCM2835 SDRAM clock handshake passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"update=0x{begin:08x} accepted=1 "
                f"clear=0x{end:08x} accepted=0 "
                f"arm-view=0x{arm_end:08x}"
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
