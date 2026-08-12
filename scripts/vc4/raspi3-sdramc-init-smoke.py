#!/usr/bin/env python3
"""Exercise the BCM2835 SDRAM controller and PHY boot contract."""

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
RESULT_ADDR = 0x00044000

SDRAMC_GPU = 0x7EE00000
APHY_GPU = 0x7EE06000
DPHY_GPU = 0x7EE07000

SDRAMC_ARM = 0x3FE00000
APHY_ARM = 0x3FE06000
DPHY_ARM = 0x3FE07000

SD_CS = 0x00
SD_MR = 0x90
SD_CS_START = 0x00200042
SD_CS_READY = 0x00208042
SD_MR_DONE = 1 << 31
SD_MR_VENDOR = SD_MR_DONE | (0x06 << 16) | 5
SD_MR_CONFIG = SD_MR_DONE | (0x58 << 16) | 8

APHY_ADDR_DLL_RESET = 0x04
APHY_ADDR_DLL_LOCK_STATUS = 0x20
APHY_PLL_GLOBAL_RESET = 0x24
APHY_PLL_LOCK_STATUS = 0x48
APHY_PLL_POWERDOWN = 0x58
APHY_PVT_CTRL = 0x70
APHY_PVT_STATUS = 0x78

DPHY_DLL_RESET = 0x04
DPHY_DLL_LOCK_STATUS = 0x18
DPHY_PVT_CTRL = 0x54
DPHY_PVT_STATUS = 0x5C

EXPECTED = (
    0x00000000,  # DPHY DLL held in reset
    0x0000FFFF,  # all 16 DPHY master DLL lock bits
    0x00000000,  # APHY PLL held in reset
    0x00010000,  # APHY PLL lock
    0x00000000,  # APHY address DLL held in reset
    0x00000003,  # APHY address DLL lock
    0x00000002,  # APHY PVT calibration complete
    0x00000002,  # DPHY PVT calibration complete
    SD_MR_DONE,
    SD_MR_VENDOR,
    SD_MR_CONFIG,
    SD_CS_READY,
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


def emit_store_result(code: bytearray, result_reg: int,
                      result_index: int) -> None:
    code += vc4_memory_offset(
        True, result_reg, 14, result_index * 4
    )


def build_firmware(path: Path) -> bytes:
    code = bytearray()
    code += vc4_mov32(14, RESULT_ADDR)

    # Data PHY DLL reset and lock.
    code += vc4_mov32(0, DPHY_GPU)
    code += vc4_mov32(1, 1)
    code += vc4_memory_offset(True, 1, 0, DPHY_DLL_RESET)
    code += vc4_memory_offset(False, 2, 0, DPHY_DLL_LOCK_STATUS)
    emit_store_result(code, 2, 0)
    code += vc4_mov32(1, 0)
    code += vc4_memory_offset(True, 1, 0, DPHY_DLL_RESET)
    code += vc4_memory_offset(False, 2, 0, DPHY_DLL_LOCK_STATUS)
    emit_store_result(code, 2, 1)

    # Address PHY DDR PLL and address DLL.
    code += vc4_mov32(3, APHY_GPU)
    code += vc4_mov32(1, 0)
    code += vc4_memory_offset(True, 1, 3, APHY_PLL_POWERDOWN)
    code += vc4_memory_offset(True, 1, 3, APHY_PLL_GLOBAL_RESET)
    code += vc4_memory_offset(False, 4, 3, APHY_PLL_LOCK_STATUS)
    emit_store_result(code, 4, 2)
    code += vc4_mov32(1, 1)
    code += vc4_memory_offset(True, 1, 3, APHY_PLL_GLOBAL_RESET)
    code += vc4_memory_offset(False, 4, 3, APHY_PLL_LOCK_STATUS)
    emit_store_result(code, 4, 3)

    code += vc4_memory_offset(True, 1, 3, APHY_ADDR_DLL_RESET)
    code += vc4_memory_offset(False, 4, 3, APHY_ADDR_DLL_LOCK_STATUS)
    emit_store_result(code, 4, 4)
    code += vc4_mov32(1, 0)
    code += vc4_memory_offset(True, 1, 3, APHY_ADDR_DLL_RESET)
    code += vc4_memory_offset(False, 4, 3, APHY_ADDR_DLL_LOCK_STATUS)
    emit_store_result(code, 4, 5)

    # Address/data PVT calibration requests complete synchronously.
    code += vc4_mov32(1, 1)
    code += vc4_memory_offset(True, 1, 3, APHY_PVT_CTRL)
    code += vc4_memory_offset(False, 4, 3, APHY_PVT_STATUS)
    emit_store_result(code, 4, 6)
    code += vc4_memory_offset(True, 1, 0, DPHY_PVT_CTRL)
    code += vc4_memory_offset(False, 4, 0, DPHY_PVT_STATUS)
    emit_store_result(code, 4, 7)

    # LPDDR2 mode-register completion and fixed Pi 3 memory identity.
    code += vc4_mov32(5, SDRAMC_GPU)
    code += vc4_memory_offset(False, 6, 5, SD_MR)
    emit_store_result(code, 6, 8)
    code += vc4_mov32(1, 5)
    code += vc4_memory_offset(True, 1, 5, SD_MR)
    code += vc4_memory_offset(False, 6, 5, SD_MR)
    emit_store_result(code, 6, 9)
    code += vc4_mov32(1, 8)
    code += vc4_memory_offset(True, 1, 5, SD_MR)
    code += vc4_memory_offset(False, 6, 5, SD_MR)
    emit_store_result(code, 6, 10)

    # Enabling a non-quiescent controller raises SDUP.
    code += vc4_mov32(1, SD_CS_START)
    code += vc4_memory_offset(True, 1, 5, SD_CS)
    code += vc4_memory_offset(False, 6, 5, SD_CS)
    emit_store_result(code, 6, 11)

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

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-sdramc-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "sdramc-init.bin"
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
                    "SDRAMC firmware was not loaded into VPU memory: "
                    f"got 0x{image0:08x}/0x{image1:08x}, "
                    f"expected 0x{expected0:08x}/0x{expected1:08x}"
                )

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
                mismatch = [
                    f"{index}:0x{actual:08x}!=0x{expected:08x}"
                    for index, (actual, expected) in enumerate(
                        zip(observed, EXPECTED)
                    )
                    if actual != expected
                ]
                raise RuntimeError(
                    "SDRAM controller/PHY contract mismatch: "
                    + ", ".join(mismatch)
                )

            arm_checks = {
                DPHY_ARM + DPHY_DLL_LOCK_STATUS: 0x0000FFFF,
                APHY_ARM + APHY_PLL_LOCK_STATUS: 0x00010000,
                APHY_ARM + APHY_ADDR_DLL_LOCK_STATUS: 0x00000003,
                APHY_ARM + APHY_PVT_STATUS: 0x00000002,
                DPHY_ARM + DPHY_PVT_STATUS: 0x00000002,
                SDRAMC_ARM + SD_MR: SD_MR_CONFIG,
                SDRAMC_ARM + SD_CS: SD_CS_READY,
            }
            for address, expected in arm_checks.items():
                actual = readl(qtest, power, address)
                if actual != expected:
                    raise RuntimeError(
                        "ARM and VideoCore SDRAM views diverged at "
                        f"0x{address:08x}: got 0x{actual:08x}, "
                        f"expected 0x{expected:08x}"
                    )

            print(
                "BCM2835 SDRAM initialization contract passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"dphy-lock=0x{observed[1]:08x} "
                f"pll-lock=0x{observed[3]:08x} "
                f"aphy-lock=0x{observed[5]:08x} "
                f"pvt=0x{observed[6]:08x}/0x{observed[7]:08x} "
                f"mr5=0x{observed[9]:08x} "
                f"mr8=0x{observed[10]:08x} "
                f"sd-cs=0x{observed[11]:08x}"
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
