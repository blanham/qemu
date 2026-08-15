#!/usr/bin/env python3
"""Exercise BCM2835 USB power and DWC2 PHY sideband registers."""

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
RESULT_ADDR = 0x00047000
RESULT_MARKER = 0x55534250

PM_ARM_BASE = 0x3F100000
PM_GPU_BASE = 0x7E100000
PM_USB = 0x5C
PM_PASSWORD = 0x5A000000

USB_ARM_BASE = 0x3F980000
USB_GPU_BASE = 0x7E980000
GMDIOCSR = 0x80
GMDIOGEN = 0x84
GVBUSDRV = 0x88
GMDIO_RSVD = 0x8C

GMDIO_BUSY = 1 << 31
GMDIO_ENABLE = 1 << 18
MDIO_WRITE = 0x50020000
MDIO_READ = 0x60020000
GVBUSDRV_MASK = 0x000FFFFF

PHY_REG = 0x15
PHY_VALUE = 0x0110
PHY_WRITE_COMMAND = MDIO_WRITE | (PHY_REG << 18) | PHY_VALUE
PHY_READ_COMMAND = MDIO_READ | (PHY_REG << 18)
PHY_SETTLE_COMMAND = MDIO_READ | (0x1B << 18)

EXPECTED = (
    1,
    GMDIO_ENABLE,
    GMDIO_ENABLE | PHY_VALUE,
    GMDIO_ENABLE | PHY_VALUE,
    GMDIO_ENABLE,
    GVBUSDRV_MASK,
    0,
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


def build_firmware(path: Path) -> bytes:
    code = bytearray()

    # Exercise PM_USB through the VideoCore peripheral alias.
    code += vc4_mov32(0, PM_GPU_BASE)
    code += vc4_mov32(1, PM_PASSWORD | 1)
    code += vc4_memory_offset(True, 1, 0, PM_USB)
    code += vc4_memory_offset(False, 2, 0, PM_USB)

    # Exercise each Broadcom DWC2 sideband register through the GPU bus.
    code += vc4_mov32(3, USB_GPU_BASE)
    code += vc4_mov32(1, GMDIO_BUSY | GMDIO_ENABLE)
    code += vc4_memory_offset(True, 1, 3, GMDIOCSR)
    code += vc4_memory_offset(False, 4, 3, GMDIOCSR)

    code += vc4_mov32(1, 0xFFFFFFFF)
    code += vc4_memory_offset(True, 1, 3, GMDIOGEN)
    code += vc4_mov32(1, PHY_WRITE_COMMAND)
    code += vc4_memory_offset(True, 1, 3, GMDIOGEN)
    code += vc4_memory_offset(False, 5, 3, GMDIOCSR)
    code += vc4_mov32(1, 0)
    code += vc4_memory_offset(True, 1, 3, GMDIOGEN)

    code += vc4_mov32(1, PHY_READ_COMMAND)
    code += vc4_memory_offset(True, 1, 3, GMDIOGEN)
    code += vc4_memory_offset(False, 6, 3, GMDIOCSR)

    code += vc4_mov32(1, PHY_SETTLE_COMMAND)
    code += vc4_memory_offset(True, 1, 3, GMDIOGEN)
    code += vc4_memory_offset(False, 7, 3, GMDIOCSR)

    code += vc4_mov32(1, 0xFFFFFFFF)
    code += vc4_memory_offset(True, 1, 3, GVBUSDRV)
    code += vc4_memory_offset(False, 8, 3, GVBUSDRV)
    code += vc4_memory_offset(True, 1, 3, GMDIO_RSVD)
    code += vc4_memory_offset(False, 9, 3, GMDIO_RSVD)

    # Publish every observed state to RAM, then publish a final marker.
    code += vc4_mov32(10, RESULT_ADDR)
    for index, reg in enumerate((2, 4, 5, 6, 7, 8, 9)):
        code += vc4_memory_offset(True, reg, 10, index * 4)
    code += vc4_mov32(11, RESULT_MARKER)
    code += vc4_memory_offset(True, 11, 10, len(EXPECTED) * 4)
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

    if PHY_SETTLE_COMMAND != 0x606E0000:
        raise AssertionError(
            f"stock MDIO command changed: 0x{PHY_SETTLE_COMMAND:08x}"
        )

    power = load_power_smoke()

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-usb-phy-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "usb-phy.bin"
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
                    "USB-sideband firmware was not loaded into VPU memory: "
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
                    "VideoCore USB sideband sequence mismatch: "
                    f"marker=0x{marker:08x} "
                    f"observed={[f'0x{x:08x}' for x in observed]} "
                    f"expected={[f'0x{x:08x}' for x in EXPECTED]}"
                )

            pm_arm = PM_ARM_BASE + PM_USB
            csr_arm = USB_ARM_BASE + GMDIOCSR
            gen_arm = USB_ARM_BASE + GMDIOGEN
            vbus_arm = USB_ARM_BASE + GVBUSDRV
            reserved_arm = USB_ARM_BASE + GMDIO_RSVD

            # Confirm the ARM alias sees VideoCore writes.
            if readl(qtest, power, pm_arm) != 1:
                raise RuntimeError(
                    "ARM PM_USB alias did not observe VC4 enable"
                )
            if readl(qtest, power, csr_arm) != GMDIO_ENABLE:
                raise RuntimeError(
                    "ARM GMDIOCSR alias diverged after VC4 MDIO"
                )
            if readl(qtest, power, gen_arm) != PHY_SETTLE_COMMAND:
                raise RuntimeError(
                    "ARM GMDIOGEN alias did not observe VC4 command"
                )
            if readl(qtest, power, vbus_arm) != GVBUSDRV_MASK:
                raise RuntimeError(
                    "ARM GVBUSDRV alias did not observe VC4 write"
                )
            if readl(qtest, power, reserved_arm) != 0:
                raise RuntimeError(
                    "reserved USB sideband register retained state"
                )

            # Password-less PM writes remain ignored; valid writes may
            # clear the latch.
            power.qtest_writel(qtest, pm_arm, 0)
            if readl(qtest, power, pm_arm) != 1:
                raise RuntimeError("PM_USB accepted a write without password")
            power.qtest_writel(qtest, pm_arm, PM_PASSWORD)
            if readl(qtest, power, pm_arm) != 0:
                raise RuntimeError("PM_USB valid disable did not latch")

            # Exercise an independent MDIO transaction through the ARM alias.
            arm_reg = 0x16
            arm_value = 0xBEEF
            arm_write = MDIO_WRITE | (arm_reg << 18) | arm_value
            arm_read = MDIO_READ | (arm_reg << 18)
            power.qtest_writel(qtest, csr_arm, GMDIO_BUSY | GMDIO_ENABLE)
            power.qtest_writel(qtest, gen_arm, 0xFFFFFFFF)
            power.qtest_writel(qtest, gen_arm, arm_write)
            power.qtest_writel(qtest, gen_arm, 0)
            power.qtest_writel(qtest, gen_arm, arm_read)
            arm_csr = readl(qtest, power, csr_arm)
            if arm_csr != (GMDIO_ENABLE | arm_value):
                raise RuntimeError(
                    f"ARM MDIO readback mismatch: 0x{arm_csr:08x}"
                )

            vbus = (GVBUSDRV_MASK & 0xFFF0FFFF) | 0x000D0000
            vbus &= ~(1 << 7)
            power.qtest_writel(qtest, vbus_arm, vbus)
            if readl(qtest, power, vbus_arm) != vbus:
                raise RuntimeError("ARM GVBUSDRV update did not latch")
            power.qtest_writel(qtest, reserved_arm, 0xFFFFFFFF)
            if readl(qtest, power, reserved_arm) != 0:
                raise RuntimeError("reserved sideband write was not ignored")

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            unexpected = (
                "Unknown offset 0x0000005c",
                "Bad offset 0x80",
                "Bad offset 0x84",
                "Bad offset 0x88",
                "Bad offset 0x8c",
            )
            found = [entry for entry in unexpected if entry in diagnostics]
            if found:
                raise RuntimeError(
                    "USB sideband accesses still produced diagnostics: "
                    + ", ".join(found)
                )

            print(
                "BCM2835 USB power and PHY sideband passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"vc4-mdio=0x{observed[2]:08x} "
                f"settle=0x{observed[4]:08x} "
                f"arm-mdio=0x{arm_csr:08x} "
                f"vbus=0x{vbus:08x} reserved=0x00000000"
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
