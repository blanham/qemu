#!/usr/bin/env python3
"""Exercise BCM2835 SDHOST timing/configuration latches and reset values."""

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
RESULT_ADDR = 0x00049000
RESULT_MARKER = 0x53444852

SDHOST_ARM_BASE = 0x3F202000
SDHOST_GPU_BASE = 0x7E202000
SDCMD = 0x00
SDARG = 0x04
SDTOUT = 0x08
SDCDIV = 0x0C
SDHCFG = 0x38
SDHBCT = 0x3C

SDTOUT_RESET = 0x00A00000
SDCDIV_RESET = 0x000001FB
SDHBCT_RESET = 0x00000400
SDCDIV_MASK = 0x000007FF

WRITTEN_ARG = 0x12345678
WRITTEN_TOUT = 0x89ABCDEF
WRITTEN_CDIV_RAW = 0xFFFFFFFF
WRITTEN_CDIV = WRITTEN_CDIV_RAW & SDCDIV_MASK
WRITTEN_HCFG = 0x00000411
WRITTEN_HBCT = 0x00000200

RESET_EXPECTED = (0, SDTOUT_RESET, SDCDIV_RESET, 0, SDHBCT_RESET)
WRITTEN_EXPECTED = (
    WRITTEN_ARG,
    WRITTEN_TOUT,
    WRITTEN_CDIV,
    WRITTEN_HCFG,
    WRITTEN_HBCT,
)
EXPECTED = RESET_EXPECTED + WRITTEN_EXPECTED

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
    code += vc4_mov32(3, SDHOST_GPU_BASE)

    # Capture documented hardware reset values through the GPU alias.
    for reg, offset in zip(range(4, 9), (SDARG, SDTOUT, SDCDIV, SDHCFG, SDHBCT)):
        code += vc4_memory_offset(False, reg, 3, offset)

    # Exercise every previously missing latch/readback path.
    for value, offset in (
        (WRITTEN_ARG, SDARG),
        (WRITTEN_TOUT, SDTOUT),
        (WRITTEN_CDIV_RAW, SDCDIV),
        (WRITTEN_HCFG, SDHCFG),
        (WRITTEN_HBCT, SDHBCT),
    ):
        code += vc4_mov32(1, value)
        code += vc4_memory_offset(True, 1, 3, offset)

    for reg, offset in zip(range(9, 14), (SDARG, SDTOUT, SDCDIV, SDHCFG, SDHBCT)):
        code += vc4_memory_offset(False, reg, 3, offset)

    code += vc4_mov32(14, RESULT_ADDR)
    for index, reg in enumerate(range(4, 14)):
        code += vc4_memory_offset(True, reg, 14, index * 4)
    code += vc4_mov32(15, RESULT_MARKER)
    code += vc4_memory_offset(True, 15, 14, len(EXPECTED) * 4)
    code += half(0x0000)

    path.write_bytes(code)
    return bytes(code)


def readl(qtest: object, power: ModuleType, address: int) -> int:
    return power.parse_qtest_value(qtest.send_line(f"readl 0x{address:x}"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    power = load_power_smoke()

    with tempfile.TemporaryDirectory(prefix="vc4-sdhost-registers-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "sdhost-registers.bin"
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
                    "SDHOST fixture was not loaded into VPU memory: "
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
                    "VideoCore SDHOST latch sequence mismatch: "
                    f"marker=0x{marker:08x} "
                    f"observed={[f'0x{x:08x}' for x in observed]} "
                    f"expected={[f'0x{x:08x}' for x in EXPECTED]}"
                )

            # Confirm the ARM peripheral alias observes all VC4 writes.
            for offset, expected in zip(
                (SDARG, SDTOUT, SDCDIV, SDHCFG, SDHBCT), WRITTEN_EXPECTED
            ):
                got = readl(qtest, power, SDHOST_ARM_BASE + offset)
                if got != expected:
                    raise RuntimeError(
                        f"ARM SDHOST alias mismatch at 0x{offset:02x}: "
                        f"0x{got:08x} != 0x{expected:08x}"
                    )

            # Exercise the same latches from the ARM side, including divider
            # masking, after the VC4 fixture has halted.
            arm_values = (
                (SDARG, 0xA5A5A5A5, 0xA5A5A5A5),
                (SDTOUT, 0x01020304, 0x01020304),
                (SDCDIV, 0x12345678, 0x00000678),
                (SDHCFG, 0x00000321, 0x00000321),
                (SDHBCT, 0x00000100, 0x00000100),
            )
            for offset, value, expected in arm_values:
                power.qtest_writel(qtest, SDHOST_ARM_BASE + offset, value)
                got = readl(qtest, power, SDHOST_ARM_BASE + offset)
                if got != expected:
                    raise RuntimeError(
                        f"ARM SDHOST write/read mismatch at 0x{offset:02x}: "
                        f"0x{got:08x} != 0x{expected:08x}"
                    )

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            forbidden = (
                "bcm2835_sdhost_read: Bad offset 4",
                "bcm2835_sdhost_read: Bad offset 8",
                "bcm2835_sdhost_read: Bad offset c",
                "bcm2835_sdhost_read: Bad offset 38",
            )
            found = [message for message in forbidden if message in diagnostics]
            if found:
                raise RuntimeError(
                    "SDHOST latch accesses still produced diagnostics: "
                    + ", ".join(found)
                )

            print(
                "BCM2835 SDHOST register latches passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"reset={[f'0x{x:08x}' for x in RESET_EXPECTED]} "
                f"vc4={[f'0x{x:08x}' for x in WRITTEN_EXPECTED]} "
                "arm-divider=0x00000678"
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
