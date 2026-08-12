#!/usr/bin/env python3
"""Exercise the exact discard-only vector80 delay used by bootcode.bin."""

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
EXACT_PC = 0x00007754
RESULT_ADDR = 0x00047000

# Stock bootcode.bin at firmware commit 3d301dd924bcd758a4c8cb19fe8531031f033f43.
EXACT_INSN = bytes.fromhex("05 fc 38 e0 00 00 4c 0f 30 00")

MARKER = 0x56383044  # "V80D"
R0_SENTINEL = 0x00102030
R12_SENTINEL = 0x12121212
R13_SENTINEL = 0x13131313
EXPECTED = (MARKER, R0_SENTINEL, R12_SENTINEL, R13_SENTINEL)


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
        (14, RESULT_ADDR),
        (0, R0_SENTINEL),
        (12, R12_SENTINEL),
        (13, R13_SENTINEL),
    ):
        encoded = vc4.vc4_mov32(reg, value)
        place(image, pc, encoded, f"initialize r{reg}")
        pc += len(encoded)

    branch = vc4.vc4_branch32(pc, EXACT_PC)
    place(image, pc, branch, "branch to exact stock vector80 PC")

    place(image, EXACT_PC, EXACT_INSN, "exact stock vector80 delay")

    pc = EXACT_PC + len(EXACT_INSN)
    encoded = vc4.vc4_mov32(1, MARKER)
    place(image, pc, encoded, "completion marker")
    pc += len(encoded)

    for reg, offset in ((1, 0), (0, 4), (12, 8), (13, 12)):
        encoded = vc4.vc4_memory_offset(True, reg, 14, offset)
        place(image, pc, encoded, f"store r{reg}")
        pc += len(encoded)

    place(image, pc, vc4.half(0x0000), "development halt")

    if image[EXACT_PC:EXACT_PC + len(EXACT_INSN)] != EXACT_INSN:
        raise AssertionError("exact vector80 production bytes changed")

    firmware = bytes(image)
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

    with tempfile.TemporaryDirectory(prefix="vc4-vector80-delay-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "vector80-delay.bin"
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

            loaded = bytes(
                power.parse_qtest_value(
                    qtest.send_line(
                        f"readb 0x{VPU_ENTRY + EXACT_PC + index:x}"
                    )
                )
                for index in range(len(EXACT_INSN))
            )
            if loaded != EXACT_INSN:
                raise RuntimeError(
                    "exact stock vector80 fixture was not loaded: "
                    f"got {loaded.hex()}, expected {EXACT_INSN.hex()}"
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
                    "vector80 delay result mismatch: "
                    f"got {[f'0x{x:08x}' for x in observed]}, "
                    f"expected {[f'0x{x:08x}' for x in EXPECTED]}"
                )

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if "unimplemented opcode 0xfc05" in diagnostics:
                raise RuntimeError(
                    "exact stock vector80 delay still reached illegal decode"
                )

            print(
                "VideoCore IV discard-only vector80 delay passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"stock-pc=0x{EXACT_PC:08x} "
                f"bytes={EXACT_INSN.hex()} "
                "decode=v16mov-discard-rep32 "
                f"r0=0x{observed[1]:08x} "
                f"r12=0x{observed[2]:08x} "
                f"r13=0x{observed[3]:08x}"
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
