#!/usr/bin/env python3
"""Exercise the BCM2835 IMAGE and GRAFX power-domain handshakes."""

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
RESULT_ADDR = 0x00045000

PM_BASE_GPU = 0x7E100000
PM_IMAGE_OFFSET = 0x108
PM_GRAFX_OFFSET = 0x10C
PM_IMAGE_ARM = 0x3F100108
PM_GRAFX_ARM = 0x3F10010C
PM_PASSWORD = 0x5A000000

PM_POWUP = 1 << 0
PM_POWOK = 1 << 1
PM_ISPOW = 1 << 2
PM_MEMREP = 1 << 3
PM_MRDONE = 1 << 4
PM_ISFUNC = 1 << 5
PM_PERIRSTN = 1 << 6
PM_V3DRSTN = 1 << 6
PM_H264RSTN = 1 << 7
PM_ISPRSTN = 1 << 8
PM_ENAB = 1 << 12
PM_INRUSH_20_MA = 3 << 13

IMAGE_STAGE1 = PM_INRUSH_20_MA | PM_POWUP
IMAGE_STAGE2 = IMAGE_STAGE1 | PM_ISPOW
IMAGE_STAGE3 = IMAGE_STAGE2 | PM_MEMREP
IMAGE_FINAL = (
    IMAGE_STAGE3
    | PM_ISFUNC
    | PM_PERIRSTN
    | PM_H264RSTN
    | PM_ISPRSTN
    | PM_ENAB
)
GRAFX_FINAL = (
    PM_INRUSH_20_MA
    | PM_ENAB
    | PM_V3DRSTN
    | PM_ISFUNC
    | PM_MEMREP
    | PM_ISPOW
    | PM_POWUP
)

EXPECTED = (
    IMAGE_STAGE1 | PM_POWOK,
    IMAGE_STAGE2 | PM_POWOK,
    IMAGE_STAGE3 | PM_POWOK | PM_MRDONE,
    IMAGE_FINAL | PM_POWOK | PM_MRDONE,
    GRAFX_FINAL | PM_POWOK | PM_MRDONE,
)

MOV = 0


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
    return vc4_alu_imm32(MOV, rd, value)


def vc4_memory_offset(store: bool, rd: int, rb: int,
                      offset: int, fmt: int = 0) -> bytes:
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | ((fmt & 3) << 6)
    i1 |= rd & 0x1F
    if raw & 0x800:
        i1 |= 0x100
    i2 = ((rb & 0x1F) << 11) | (raw & 0x7FF)
    return half(i1) + half(i2)


def append_domain_stage(code: bytearray, *, register_offset: int,
                        requested: int, result_offset: int) -> None:
    code += vc4_mov32(1, PM_PASSWORD | requested)
    code += vc4_memory_offset(True, 1, 0, register_offset)
    code += vc4_memory_offset(False, 2, 0, register_offset)
    code += vc4_memory_offset(True, 2, 4, result_offset)


def build_firmware(path: Path) -> bytes:
    code = bytearray()
    code += vc4_mov32(0, PM_BASE_GPU)
    code += vc4_mov32(4, RESULT_ADDR)

    for index, requested in enumerate((
        IMAGE_STAGE1,
        IMAGE_STAGE2,
        IMAGE_STAGE3,
        IMAGE_FINAL,
    )):
        append_domain_stage(
            code,
            register_offset=PM_IMAGE_OFFSET,
            requested=requested,
            result_offset=index * 4,
        )

    append_domain_stage(
        code,
        register_offset=PM_GRAFX_OFFSET,
        requested=GRAFX_FINAL,
        result_offset=16,
    )

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

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-image-power-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "image-grafx-power.bin"
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

            if readl(qtest, power, PM_IMAGE_ARM) != 0:
                raise RuntimeError("PM_IMAGE did not reset to zero")
            if readl(qtest, power, PM_GRAFX_ARM) != 0:
                raise RuntimeError("PM_GRAFX did not reset to zero")

            expected0 = int.from_bytes(firmware[0:4], "little")
            expected1 = int.from_bytes(firmware[4:8], "little")
            image0 = readl(qtest, power, VPU_ENTRY)
            image1 = readl(qtest, power, VPU_ENTRY + 4)
            if (image0, image1) != (expected0, expected1):
                raise RuntimeError(
                    "power-domain firmware was not loaded into VPU memory: "
                    f"got 0x{image0:08x}/0x{image1:08x}, "
                    f"expected 0x{expected0:08x}/0x{expected1:08x}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = tuple(0 for _ in EXPECTED)
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
                got = "/".join(f"0x{value:08x}" for value in observed)
                wanted = "/".join(f"0x{value:08x}" for value in EXPECTED)
                raise RuntimeError(
                    "VPU IMAGE/GRAFX power sequence did not complete: "
                    f"got {got}; expected {wanted}"
                )

            image_arm = readl(qtest, power, PM_IMAGE_ARM)
            grafx_arm = readl(qtest, power, PM_GRAFX_ARM)
            if (image_arm, grafx_arm) != (EXPECTED[3], EXPECTED[4]):
                raise RuntimeError(
                    "ARM and VideoCore power-domain views diverged: "
                    f"image=0x{image_arm:08x} grafx=0x{grafx_arm:08x}"
                )

            # Password-less writes must not modify either domain.
            power.qtest_writel(qtest, PM_IMAGE_ARM, 0)
            power.qtest_writel(qtest, PM_GRAFX_ARM, 0)
            if readl(qtest, power, PM_IMAGE_ARM) != EXPECTED[3]:
                raise RuntimeError("PM_IMAGE accepted an invalid-password write")
            if readl(qtest, power, PM_GRAFX_ARM) != EXPECTED[4]:
                raise RuntimeError("PM_GRAFX accepted an invalid-password write")

            # Powering down clears POWOK/MRDONE when POWUP is removed while
            # preserving independently writable reset/configuration fields.
            image_off_request = IMAGE_FINAL & ~(
                PM_ISFUNC | PM_ISPOW | PM_POWUP
            )
            grafx_off_request = GRAFX_FINAL & ~(
                PM_ISFUNC | PM_ISPOW | PM_POWUP
            )
            power.qtest_writel(
                qtest, PM_IMAGE_ARM, PM_PASSWORD | image_off_request
            )
            power.qtest_writel(
                qtest, PM_GRAFX_ARM, PM_PASSWORD | grafx_off_request
            )
            image_off = readl(qtest, power, PM_IMAGE_ARM)
            grafx_off = readl(qtest, power, PM_GRAFX_ARM)
            if image_off != image_off_request:
                raise RuntimeError(
                    f"PM_IMAGE power-down mismatch: 0x{image_off:08x}"
                )
            if grafx_off != grafx_off_request:
                raise RuntimeError(
                    f"PM_GRAFX power-down mismatch: 0x{grafx_off:08x}"
                )

            print(
                "BCM2835 IMAGE/GRAFX power domains passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"image=0x{EXPECTED[3]:08x} "
                f"grafx=0x{EXPECTED[4]:08x} "
                f"image-off=0x{image_off:08x} "
                f"grafx-off=0x{grafx_off:08x}"
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
