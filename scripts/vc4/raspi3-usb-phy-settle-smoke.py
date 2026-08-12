#!/usr/bin/env python3
"""Exercise the BCM2835 USB PHY calibration-settle indication."""

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
RESULT_ADDR = 0x00048000
RESULT_MARKER = 0x5345544C

USB_ARM_BASE = 0x3F980000
USB_GPU_BASE = 0x7E980000
GMDIOCSR = 0x80
GMDIOGEN = 0x84

GMDIO_BUSY = 1 << 31
GMDIO_ENABLE = 1 << 18
MDIO_WRITE = 0x50020000
MDIO_READ = 0x60020000

PHY_DIVISOR_REG = 0x17
PHY_DIVISOR = 0x1632
PHY_STATUS_REG = 0x1B
PHY_SETTLE = 1 << 7

STOCK_WAIT_PC = 0x00009820
STOCK_WAIT_BYTES = bytes.fromhex("706c7c18")

DIVISOR_WRITE = MDIO_WRITE | (PHY_DIVISOR_REG << 18) | PHY_DIVISOR
DIVISOR_READ = MDIO_READ | (PHY_DIVISOR_REG << 18)
STATUS_READ = MDIO_READ | (PHY_STATUS_REG << 18)

EXPECTED = (
    GMDIO_ENABLE | PHY_DIVISOR,
    GMDIO_ENABLE | PHY_DIVISOR,
    GMDIO_ENABLE | PHY_SETTLE,
    GMDIO_ENABLE,
    GMDIO_ENABLE | PHY_SETTLE,
)

VC4_MOV = 0


def load_power_smoke() -> ModuleType:
    path = Path(__file__).with_name("raspi3-hetero-power-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_power_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(
            f"could not load heterogeneous smoke helpers: {path}"
        )
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


def emit_mdio_write(code: bytearray, base_reg: int,
                    command: int, value_reg: int = 1) -> None:
    code += vc4_mov32(value_reg, 0xFFFFFFFF)
    code += vc4_memory_offset(True, value_reg, base_reg, GMDIOGEN)
    code += vc4_mov32(value_reg, command)
    code += vc4_memory_offset(True, value_reg, base_reg, GMDIOGEN)
    code += vc4_mov32(value_reg, 0)
    code += vc4_memory_offset(True, value_reg, base_reg, GMDIOGEN)


def emit_mdio_read(code: bytearray, base_reg: int, command: int,
                   destination: int, value_reg: int = 1) -> None:
    code += vc4_mov32(value_reg, 0xFFFFFFFF)
    code += vc4_memory_offset(True, value_reg, base_reg, GMDIOGEN)
    code += vc4_mov32(value_reg, command)
    code += vc4_memory_offset(True, value_reg, base_reg, GMDIOGEN)
    code += vc4_memory_offset(False, destination, base_reg, GMDIOCSR)
    code += vc4_mov32(value_reg, 0)
    code += vc4_memory_offset(True, value_reg, base_reg, GMDIOGEN)


def verify_stock_wait_contract(bootcode: bytes) -> None:
    end = STOCK_WAIT_PC + len(STOCK_WAIT_BYTES)
    observed = bootcode[STOCK_WAIT_PC:end]
    if observed != STOCK_WAIT_BYTES:
        raise AssertionError(
            "pinned bootcode USB settle loop changed: "
            f"pc=0x{STOCK_WAIT_PC:08x} observed={observed.hex()} "
            f"expected={STOCK_WAIT_BYTES.hex()}"
        )

    # Exact pinned words: btst r0, 7; beq -8.
    btst, branch = struct.unpack("<HH", observed)
    op = ((btst >> 9) & 0xF) * 2
    rd = btst & 0xF
    immediate = (btst >> 4) & 0x1F
    if (op, rd, immediate) != (12, 0, 7):
        raise AssertionError(
            "unexpected stock bit-test decode: "
            f"word=0x{btst:04x} op={op} rd={rd} immediate={immediate}"
        )

    cond = (branch >> 7) & 0xF
    raw = branch & 0x7F
    offset = ((raw ^ 0x40) - 0x40) * 2
    if cond != 0 or offset != -8:
        raise AssertionError(
            f"unexpected stock settle loop: cond={cond} offset={offset}"
        )


def build_firmware(path: Path) -> bytes:
    code = bytearray()
    code += vc4_mov32(3, USB_GPU_BASE)
    code += vc4_mov32(1, GMDIO_BUSY | GMDIO_ENABLE)
    code += vc4_memory_offset(True, 1, 3, GMDIOCSR)

    emit_mdio_write(code, 3, DIVISOR_WRITE)
    code += vc4_memory_offset(False, 4, 3, GMDIOCSR)
    emit_mdio_read(code, 3, DIVISOR_READ, 5)
    emit_mdio_read(code, 3, STATUS_READ, 6)
    emit_mdio_read(code, 3, STATUS_READ, 7)

    # A new divisor write starts a fresh settle pulse.
    emit_mdio_write(code, 3, DIVISOR_WRITE)
    emit_mdio_read(code, 3, STATUS_READ, 8)

    code += vc4_mov32(10, RESULT_ADDR)
    for index, reg in enumerate((4, 5, 6, 7, 8)):
        code += vc4_memory_offset(True, reg, 10, index * 4)
    code += vc4_mov32(11, RESULT_MARKER)
    code += vc4_memory_offset(True, 11, 10, len(EXPECTED) * 4)
    code += half(0x0000)

    path.write_bytes(code)
    return bytes(code)


def readl(qtest: object, power: ModuleType, address: int) -> int:
    return power.parse_qtest_value(
        qtest.send_line(f"readl 0x{address:x}")
    )


def arm_mdio_write(qtest: object, power: ModuleType,
                   command: int) -> None:
    base = USB_ARM_BASE
    power.qtest_writel(qtest, base + GMDIOGEN, 0xFFFFFFFF)
    power.qtest_writel(qtest, base + GMDIOGEN, command)
    power.qtest_writel(qtest, base + GMDIOGEN, 0)


def arm_mdio_read(qtest: object, power: ModuleType,
                  command: int) -> int:
    base = USB_ARM_BASE
    power.qtest_writel(qtest, base + GMDIOGEN, 0xFFFFFFFF)
    power.qtest_writel(qtest, base + GMDIOGEN, command)
    value = readl(qtest, power, base + GMDIOCSR)
    power.qtest_writel(qtest, base + GMDIOGEN, 0)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument(
        "--bootcode",
        required=True,
        help="path to the pinned unmodified bootcode.bin",
    )
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    bootcode_path = Path(args.bootcode).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")
    if not bootcode_path.is_file():
        parser.error(f"not a file: {bootcode_path}")

    if STATUS_READ != 0x606E0000:
        raise AssertionError(
            f"stock MDIO status command changed: 0x{STATUS_READ:08x}"
        )
    verify_stock_wait_contract(bootcode_path.read_bytes())
    power = load_power_smoke()

    with tempfile.TemporaryDirectory(prefix="vc4-usb-settle-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "usb-settle.bin"
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
                    "USB-settle firmware was not loaded into VPU memory: "
                    f"got 0x{image0:08x}/0x{image1:08x}, "
                    f"expected 0x{expected0:08x}/0x{expected1:08x}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = (0,) * len(EXPECTED)
            marker = 0
            while time.monotonic() < deadline:
                marker = readl(
                    qtest, power, RESULT_ADDR + len(EXPECTED) * 4
                )
                if marker == RESULT_MARKER:
                    observed = tuple(
                        readl(qtest, power, RESULT_ADDR + index * 4)
                        for index in range(len(EXPECTED))
                    )
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if marker != RESULT_MARKER or observed != EXPECTED:
                raise RuntimeError(
                    "VideoCore USB settle sequence mismatch: "
                    f"marker=0x{marker:08x} "
                    f"observed={[f'0x{x:08x}' for x in observed]} "
                    f"expected={[f'0x{x:08x}' for x in EXPECTED]}"
                )

            # Repeat the same edge-sensitive contract through the ARM alias.
            power.qtest_writel(
                qtest, USB_ARM_BASE + GMDIOCSR,
                GMDIO_BUSY | GMDIO_ENABLE,
            )
            arm_mdio_write(qtest, power, DIVISOR_WRITE)
            arm_first = arm_mdio_read(qtest, power, STATUS_READ)
            arm_second = arm_mdio_read(qtest, power, STATUS_READ)
            if arm_first != (GMDIO_ENABLE | PHY_SETTLE):
                raise RuntimeError(
                    f"ARM first settle read mismatch: 0x{arm_first:08x}"
                )
            if arm_second != GMDIO_ENABLE:
                raise RuntimeError(
                    f"ARM consumed settle read mismatch: 0x{arm_second:08x}"
                )

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if (
                "Bad offset 0x80" in diagnostics
                or "Bad offset 0x84" in diagnostics
            ):
                raise RuntimeError(
                    "USB settle accesses still produced DWC2 diagnostics"
                )

            print(
                "BCM2835 USB PHY settle indication passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"divisor=0x{observed[1]:08x} "
                f"first=0x{observed[2]:08x} "
                f"settled=0x{observed[3]:08x} "
                f"retrigger=0x{observed[4]:08x} "
                f"arm-first=0x{arm_first:08x} "
                f"arm-settled=0x{arm_second:08x}"
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
