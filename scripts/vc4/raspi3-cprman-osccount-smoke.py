#!/usr/bin/env python3
"""Exercise the BCM2835 CPRMAN oscillator countdown through stock VC4 code."""

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
EXACT_PC = 0x000034B4
RESULT_ADDR = 0x00043100

CM_BASE_GPU = 0x7E101000
CM_OSCCOUNT_GPU = 0x7E101100
CM_OSCCOUNT_ARM = 0x3F101100
CM_OSCCOUNT_OFFSET = 0x100
CM_PASSWORD = 0x5A000000
TEST_COUNT = 2

# Exact stock loop at firmware commit 3d301dd924bcd758a4c8cb19fe8531031f033f43:
#     ld          r2, (r7)
#     addcmpbne   r2, 0, 0, 0x34b4
EXACT_LOOP = bytes.fromhex("72 08 02 81 ff c0")
MARKER = 0x4F534343  # "OSCC"
EXPECTED_RESULT = (MARKER, 0)


def load_helper(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load helper module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def place(image: bytearray, offset: int, data: bytes, label: str) -> None:
    end = offset + len(data)
    if offset < 0 or end > len(image):
        raise ValueError(
            f"{label}: range 0x{offset:x}..0x{end:x} is outside firmware"
        )
    if any(image[offset:end]):
        raise ValueError(f"{label}: firmware range already contains data")
    image[offset:end] = data


def build_firmware(path: Path, vc4: ModuleType) -> bytes:
    image = bytearray(EXACT_PC + 0x80)
    pc = 0

    for reg, value in (
        (5, CM_BASE_GPU),
        (7, CM_OSCCOUNT_GPU),
        (8, CM_PASSWORD | TEST_COUNT),
    ):
        encoded = vc4.vc4_mov32(reg, value)
        place(image, pc, encoded, f"initialize r{reg}")
        pc += len(encoded)

    encoded = vc4.vc4_memory_offset(
        True, 8, 5, CM_OSCCOUNT_OFFSET
    )
    place(image, pc, encoded, "load oscillator countdown")
    pc += len(encoded)

    encoded = vc4.vc4_mov32(4, RESULT_ADDR)
    place(image, pc, encoded, "initialize result pointer")
    pc += len(encoded)

    encoded = vc4.vc4_branch32(pc, EXACT_PC)
    place(image, pc, encoded, "branch to exact stock polling loop")

    place(image, EXACT_PC, EXACT_LOOP, "exact stock oscillator loop")

    pc = EXACT_PC + len(EXACT_LOOP)
    encoded = vc4.vc4_mov32(3, MARKER)
    place(image, pc, encoded, "completion marker")
    pc += len(encoded)

    for reg, offset in ((3, 0), (2, 4)):
        encoded = vc4.vc4_memory_offset(True, reg, 4, offset)
        place(image, pc, encoded, f"store r{reg}")
        pc += len(encoded)

    place(image, pc, vc4.half(0x0000), "development halt")

    if image[EXACT_PC:EXACT_PC + len(EXACT_LOOP)] != EXACT_LOOP:
        raise AssertionError("exact stock oscillator polling bytes changed")

    firmware = bytes(image)
    path.write_bytes(firmware)
    return firmware


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

    power = load_helper("raspi3-hetero-power-smoke.py", "vc4_power_smoke")
    vc4 = load_helper("raspi3-pcrel48-smoke.py", "vc4_pcrel_smoke")

    with tempfile.TemporaryDirectory(prefix="vc4-cprman-osccount-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "cprman-osccount.bin"
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

            if readl(qtest, power, CM_OSCCOUNT_ARM) != 0:
                raise RuntimeError("CM_OSCCOUNT did not reset to zero")

            loaded = bytes(
                power.parse_qtest_value(
                    qtest.send_line(
                        f"readb 0x{VPU_ENTRY + EXACT_PC + index:x}"
                    )
                )
                for index in range(len(EXACT_LOOP))
            )
            if loaded != EXACT_LOOP:
                raise RuntimeError(
                    "exact stock oscillator loop was not loaded: "
                    f"got {loaded.hex()}, expected {EXACT_LOOP.hex()}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = (0, 0)
            while time.monotonic() < deadline:
                observed = (
                    readl(qtest, power, RESULT_ADDR),
                    readl(qtest, power, RESULT_ADDR + 4),
                )
                if observed == EXPECTED_RESULT:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if observed != EXPECTED_RESULT:
                raise RuntimeError(
                    "stock oscillator polling loop did not complete: "
                    f"marker=0x{observed[0]:08x} r2=0x{observed[1]:08x}"
                )

            # A password-less write must remain ignored at zero.
            power.qtest_writel(qtest, CM_OSCCOUNT_ARM, TEST_COUNT)
            invalid_result = readl(qtest, power, CM_OSCCOUNT_ARM)
            if invalid_result != 0:
                raise RuntimeError(
                    "CM_OSCCOUNT accepted an invalid-password write: "
                    f"0x{invalid_result:08x}"
                )

            # The ARM view must expose the same deterministic countdown and
            # saturate rather than wrapping after zero.
            power.qtest_writel(
                qtest, CM_OSCCOUNT_ARM, CM_PASSWORD | TEST_COUNT
            )
            sequence = tuple(
                readl(qtest, power, CM_OSCCOUNT_ARM) for _ in range(4)
            )
            if sequence != (2, 1, 0, 0):
                raise RuntimeError(
                    "CM_OSCCOUNT sequence mismatch: "
                    f"got {sequence!r}, expected (2, 1, 0, 0)"
                )

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if "CM_OSCCOUNT" in diagnostics and "unimplemented" in diagnostics:
                raise RuntimeError(
                    "CM_OSCCOUNT still produced an unimplemented diagnostic"
                )

            print(
                "BCM2835 CPRMAN oscillator countdown passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"stock-pc=0x{EXACT_PC:08x} "
                f"stock-loop={EXACT_LOOP.hex()} "
                "guest-sequence=2->1->0 "
                f"arm-sequence={'->'.join(str(value) for value in sequence)} "
                "invalid-password=ignored saturation=zero"
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
