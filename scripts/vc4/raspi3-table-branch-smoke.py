#!/usr/bin/env python3
"""Exercise VideoCore IV scalar TBB and TBH table branches."""

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
RESULT_ADDR = 0x00046000

EXACT_TBB_PC = 0x00004298
EXACT_TBB_TABLE = bytes.fromhex(
    "10 10 12 12 12 22 22 22 22 22 12 22 22 12 0e 0e"
)
EXACT_TBB_CASE = EXACT_TBB_PC + 2 + (EXACT_TBB_TABLE[0] << 1)

TBH_FORWARD_PC = 0x00004300
TBH_FORWARD_CASE = 0x00004380
TBB_BACKWARD_CASE = 0x000043A0
TBB_BACKWARD_PC = 0x000043C0
TBH_BACKWARD_CASE = 0x00004420
TBH_BACKWARD_PC = 0x00004460

MARKERS = (0x54424230, 0x54424831, 0x54424232, 0x54424833)


def load_helper(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load helper module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def table_entry(base: int, target: int, bits: int) -> int:
    delta = target - base
    if delta & 1:
        raise ValueError("table-branch target is not halfword aligned")
    value = delta // 2
    if not -(1 << (bits - 1)) <= value < (1 << (bits - 1)):
        raise ValueError(
            f"table-branch displacement {value} does not fit {bits} bits"
        )
    return value & ((1 << bits) - 1)


class ImageBuilder:
    def __init__(self, size: int) -> None:
        self.image = bytearray(size)
        self.ranges: list[tuple[int, int, str]] = []

    def place(self, offset: int, data: bytes, label: str) -> None:
        end = offset + len(data)
        if offset < 0 or end > len(self.image):
            raise ValueError(
                f"{label}: range 0x{offset:x}..0x{end:x} is outside image"
            )
        for old_start, old_end, old_label in self.ranges:
            if offset < old_end and old_start < end:
                raise ValueError(
                    f"{label}: overlaps {old_label} "
                    f"(0x{old_start:x}..0x{old_end:x})"
                )
        self.image[offset:end] = data
        self.ranges.append((offset, end, label))


def build_firmware(path: Path, vc4: ModuleType) -> bytes:
    b = ImageBuilder(0x4500)

    def marker_code(marker: int, slot: int) -> bytes:
        return (
            vc4.vc4_mov32(4, marker)
            + vc4.vc4_memory_offset(True, 4, 14, slot * 4)
        )

    entry = bytearray()
    entry += vc4.vc4_mov32(14, RESULT_ADDR)
    entry += vc4.vc4_mov32(0, 0)
    entry += vc4.vc4_branch32(len(entry), EXACT_TBB_PC)
    b.place(0, bytes(entry), "entry")

    b.place(EXACT_TBB_PC, vc4.half(0x0080), "stock TBB r0")
    b.place(EXACT_TBB_PC + 2, EXACT_TBB_TABLE, "stock TBB table")

    case0 = bytearray(marker_code(MARKERS[0], 0))
    case0 += vc4.vc4_mov32(1, 2)
    case0 += vc4.vc4_branch32(
        EXACT_TBB_CASE + len(case0), TBH_FORWARD_PC
    )
    b.place(EXACT_TBB_CASE, bytes(case0), "stock TBB case")

    tbh_forward = bytearray(8)
    struct.pack_into("<H", tbh_forward, 0, 0x00A1)  # TBH r1
    struct.pack_into("<H", tbh_forward, 2, 1)
    struct.pack_into("<H", tbh_forward, 4, 1)
    struct.pack_into(
        "<H", tbh_forward, 6,
        table_entry(TBH_FORWARD_PC + 2, TBH_FORWARD_CASE, 16),
    )
    b.place(TBH_FORWARD_PC, bytes(tbh_forward), "forward TBH and table")

    case1 = bytearray(marker_code(MARKERS[1], 1))
    case1 += vc4.vc4_mov32(2, 1)
    case1 += vc4.vc4_branch32(
        TBH_FORWARD_CASE + len(case1), TBB_BACKWARD_PC
    )
    b.place(TBH_FORWARD_CASE, bytes(case1), "forward TBH case")

    case2 = bytearray(marker_code(MARKERS[2], 2))
    case2 += vc4.vc4_mov32(3, 1)
    case2 += vc4.vc4_branch32(
        TBB_BACKWARD_CASE + len(case2), TBH_BACKWARD_PC
    )
    b.place(TBB_BACKWARD_CASE, bytes(case2), "backward TBB case")

    b.place(
        TBB_BACKWARD_PC,
        bytes((
            0x82, 0x00,  # TBB r2
            0x01,
            table_entry(TBB_BACKWARD_PC + 2, TBB_BACKWARD_CASE, 8),
            0x01,
            0x01,
        )),
        "backward TBB and table",
    )

    b.place(
        TBH_BACKWARD_CASE,
        marker_code(MARKERS[3], 3) + vc4.half(0x0000),
        "backward TBH case",
    )

    tbh_backward = bytearray(8)
    struct.pack_into("<H", tbh_backward, 0, 0x00A3)  # TBH r3
    struct.pack_into("<H", tbh_backward, 2, 1)
    struct.pack_into(
        "<H", tbh_backward, 4,
        table_entry(TBH_BACKWARD_PC + 2, TBH_BACKWARD_CASE, 16),
    )
    struct.pack_into("<H", tbh_backward, 6, 1)
    b.place(TBH_BACKWARD_PC, bytes(tbh_backward), "backward TBH and table")

    firmware = bytes(b.image)
    exact = vc4.half(0x0080) + EXACT_TBB_TABLE
    if firmware[EXACT_TBB_PC:EXACT_TBB_PC + len(exact)] != exact:
        raise AssertionError("exact stock TBB bytes changed")
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

    with tempfile.TemporaryDirectory(prefix="vc4-table-branch-") as tmp_s:
        tmp = Path(tmp_s)
        firmware_path = tmp / "table-branch.bin"
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

            exact = bytes(
                power.parse_qtest_value(
                    qtest.send_line(
                        f"readb 0x{VPU_ENTRY + EXACT_TBB_PC + index:x}"
                    )
                )
                for index in range(2 + len(EXACT_TBB_TABLE))
            )
            expected_exact = vc4.half(0x0080) + EXACT_TBB_TABLE
            if exact != expected_exact:
                raise RuntimeError(
                    "exact stock TBB fixture was not loaded: "
                    f"got {exact.hex()}, expected {expected_exact.hex()}"
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            observed = (0,) * len(MARKERS)
            while time.monotonic() < deadline:
                observed = tuple(
                    power.parse_qtest_value(
                        qtest.send_line(
                            f"readl 0x{RESULT_ADDR + index * 4:x}"
                        )
                    )
                    for index in range(len(MARKERS))
                )
                if observed == MARKERS:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if observed != MARKERS:
                raise RuntimeError(
                    "TBB/TBH marker mismatch: "
                    f"got {[f'0x{x:08x}' for x in observed]}, "
                    f"expected {[f'0x{x:08x}' for x in MARKERS]}"
                )

            print(
                "VideoCore IV scalar table branches passed: "
                f"cpus={len(qom_types)} arm={arm_count} vc4={vc4_count} "
                f"stock-pc=0x{EXACT_TBB_PC:08x} "
                f"stock-opcode=8000 stock-entry=0x{EXACT_TBB_TABLE[0]:02x} "
                f"stock-target=0x{EXACT_TBB_CASE:08x} "
                f"tbh-forward=0x{TBH_FORWARD_CASE:08x} "
                f"tbb-backward=0x{TBB_BACKWARD_CASE:08x} "
                f"tbh-backward=0x{TBH_BACKWARD_CASE:08x}"
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
