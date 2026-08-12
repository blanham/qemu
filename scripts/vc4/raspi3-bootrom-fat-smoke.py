#!/usr/bin/env python3
"""Exercise the Pi boot-ROM FAT path through VC4 and into Cortex-A53."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any, BinaryIO

SECTOR_SIZE = 512
IMAGE_SIZE = 128 * 1024 * 1024
TOTAL_SECTORS = IMAGE_SIZE // SECTOR_SIZE
PARTITION_LBA = 2048
PARTITION_SECTORS = TOTAL_SECTORS - PARTITION_LBA
RESERVED_SECTORS = 32
FAT_COUNT = 2
FAT_SECTORS = 2048
SECTORS_PER_CLUSTER = 1
ROOT_CLUSTER = 2
BOOT_CLUSTERS = (5, 9, 7)
BOOT_FILE_SIZE = 1300

PM_PROC_ARM = 0x3F100110
PM_PROC_GPU = 0x7E100110
PM_PROC_READY = 0x0000007F
VPU_MARKER_ADDR = 0x00040000
VPU_MARKER_VALUE = 0xB007F47E
ARM_MARKER_ADDR = 0x00001000
ARM_MARKER_VALUE = 0xA53F47E0

MOV = 0


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


def a64_movz(rd: int, imm16: int, shift: int = 0, *, sf: bool = True) -> int:
    base = 0xD2800000 if sf else 0x52800000
    return base | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def a64_movk(rd: int, imm16: int, shift: int = 0, *, sf: bool = True) -> int:
    base = 0xF2800000 if sf else 0x72800000
    return base | ((shift // 16) << 21) | ((imm16 & 0xFFFF) << 5) | rd


def build_bootcode() -> bytes:
    bootcode = bytearray()

    # Prove that code is executing from the VPU-private cache while accesses
    # above 128 KiB still reach shared SDRAM through the GPU bus.
    bootcode += vc4_mov32(0, VPU_MARKER_ADDR)
    bootcode += vc4_mov32(1, VPU_MARKER_VALUE)
    bootcode += vc4_memory_offset(True, 1, 0, 0)

    bootcode += vc4_mov32(0, PM_PROC_GPU & ~0xFFF)
    for requested in (0x01, 0x05, 0x0D, 0x2D, 0x6D):
        bootcode += vc4_mov32(1, 0x5A000000 | requested)
        bootcode += vc4_memory_offset(True, 1, 0,
                                      PM_PROC_GPU & 0xFFF)

    bootcode += half(0x0000)  # development HALT
    if len(bootcode) > BOOT_FILE_SIZE:
        raise AssertionError("test bootcode no longer fits synthetic file")

    # The useful program is in cluster 5, but the declared file crosses the
    # deliberately fragmented 5 -> 9 -> 7 chain.  Machine initialization must
    # traverse and read the entire chain before the VPU is allowed to run.
    bootcode += bytes((index * 37 + 11) & 0xFF
                      for index in range(BOOT_FILE_SIZE - len(bootcode)))
    return bytes(bootcode)


def build_arm_payload() -> list[int]:
    return [
        a64_movz(0, ARM_MARKER_ADDR, sf=True),
        a64_movz(1, ARM_MARKER_VALUE & 0xFFFF, sf=False),
        a64_movk(1, ARM_MARKER_VALUE >> 16, shift=16, sf=False),
        0xB9000001,  # str w1, [x0]
        0x14000000,  # b .
    ]


def write_sector(image: BinaryIO, lba: int, data: bytes) -> None:
    if len(data) != SECTOR_SIZE:
        raise ValueError(f"sector write has {len(data)} bytes")
    image.seek(lba * SECTOR_SIZE)
    image.write(data)


def cluster_lba(cluster: int) -> int:
    data_lba = (PARTITION_LBA + RESERVED_SECTORS +
                FAT_COUNT * FAT_SECTORS)
    return data_lba + (cluster - 2) * SECTORS_PER_CLUSTER


def build_sd_image(path: Path, bootcode: bytes) -> None:
    if len(bootcode) != BOOT_FILE_SIZE:
        raise ValueError("unexpected synthetic bootcode size")

    with path.open("w+b") as image:
        image.truncate(IMAGE_SIZE)

        mbr = bytearray(SECTOR_SIZE)
        partition = memoryview(mbr)[446:462]
        partition[0] = 0x00
        partition[1:4] = b"\x00\x02\x00"
        partition[4] = 0x0C  # FAT32 LBA
        partition[5:8] = b"\xFE\xFF\xFF"
        struct.pack_into("<I", partition, 8, PARTITION_LBA)
        struct.pack_into("<I", partition, 12, PARTITION_SECTORS)
        mbr[510:512] = b"\x55\xAA"
        write_sector(image, 0, bytes(mbr))

        boot = bytearray(SECTOR_SIZE)
        boot[0:3] = b"\xEB\x58\x90"
        boot[3:11] = b"MSWIN4.1"
        struct.pack_into("<H", boot, 11, SECTOR_SIZE)
        boot[13] = SECTORS_PER_CLUSTER
        struct.pack_into("<H", boot, 14, RESERVED_SECTORS)
        boot[16] = FAT_COUNT
        struct.pack_into("<H", boot, 17, 0)
        struct.pack_into("<H", boot, 19, 0)
        boot[21] = 0xF8
        struct.pack_into("<H", boot, 22, 0)
        struct.pack_into("<H", boot, 24, 63)
        struct.pack_into("<H", boot, 26, 255)
        struct.pack_into("<I", boot, 28, PARTITION_LBA)
        struct.pack_into("<I", boot, 32, PARTITION_SECTORS)
        struct.pack_into("<I", boot, 36, FAT_SECTORS)
        struct.pack_into("<H", boot, 40, 0)
        struct.pack_into("<H", boot, 42, 0)
        struct.pack_into("<I", boot, 44, ROOT_CLUSTER)
        struct.pack_into("<H", boot, 48, 1)
        struct.pack_into("<H", boot, 50, 6)
        boot[64] = 0x80
        boot[66] = 0x29
        struct.pack_into("<I", boot, 67, 0xB007C0DE)
        boot[71:82] = b"RPIBOOT    "
        boot[82:90] = b"FAT32   "
        boot[510:512] = b"\x55\xAA"
        write_sector(image, PARTITION_LBA, bytes(boot))
        write_sector(image, PARTITION_LBA + 6, bytes(boot))

        fsinfo = bytearray(SECTOR_SIZE)
        struct.pack_into("<I", fsinfo, 0, 0x41615252)
        struct.pack_into("<I", fsinfo, 484, 0x61417272)
        struct.pack_into("<I", fsinfo, 488, 0xFFFFFFFF)
        struct.pack_into("<I", fsinfo, 492, 0xFFFFFFFF)
        struct.pack_into("<I", fsinfo, 508, 0xAA550000)
        write_sector(image, PARTITION_LBA + 1, bytes(fsinfo))

        fat = bytearray(SECTOR_SIZE)
        fat_entries = {
            0: 0x0FFFFFF8,
            1: 0xFFFFFFFF,
            ROOT_CLUSTER: 0x0FFFFFFF,
            BOOT_CLUSTERS[0]: BOOT_CLUSTERS[1],
            BOOT_CLUSTERS[1]: BOOT_CLUSTERS[2],
            BOOT_CLUSTERS[2]: 0x0FFFFFFF,
        }
        for cluster, value in fat_entries.items():
            struct.pack_into("<I", fat, cluster * 4, value)
        for fat_index in range(FAT_COUNT):
            fat_lba = (PARTITION_LBA + RESERVED_SECTORS +
                       fat_index * FAT_SECTORS)
            write_sector(image, fat_lba, bytes(fat))

        root = bytearray(SECTOR_SIZE)
        root[0:11] = b"BOOTCODEBIN"
        root[11] = 0x20
        struct.pack_into("<H", root, 20, BOOT_CLUSTERS[0] >> 16)
        struct.pack_into("<H", root, 26, BOOT_CLUSTERS[0] & 0xFFFF)
        struct.pack_into("<I", root, 28, len(bootcode))
        write_sector(image, cluster_lba(ROOT_CLUSTER), bytes(root))

        for index, cluster in enumerate(BOOT_CLUSTERS):
            chunk = bootcode[index * SECTOR_SIZE:(index + 1) * SECTOR_SIZE]
            sector = chunk.ljust(SECTOR_SIZE, b"\x00")
            write_sector(image, cluster_lba(cluster), sector)


class LineSocket:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def send_line(self, line: str) -> str:
        self.file.write(line.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"socket closed while waiting for {line!r}")
        return reply.decode("ascii", errors="replace").strip()

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QMP:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"invalid QMP greeting: {greeting}")
        self.execute("qmp_capabilities")

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP socket closed")
            message = json.loads(line)
            if "event" not in message:
                return message

    def execute(self, command: str) -> Any:
        payload = json.dumps({"execute": command}).encode("utf-8") + b"\n"
        self.file.write(payload)
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_socket(path: Path, proc: subprocess.Popen[bytes],
                    timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        time.sleep(0.01)
    raise TimeoutError(f"socket did not appear: {path}")


def parse_qtest_value(reply: str) -> int:
    fields = reply.split()
    if len(fields) != 2 or fields[0] != "OK":
        raise RuntimeError(f"unexpected qtest reply: {reply!r}")
    return int(fields[1], 0)


def qtest_writel(qtest: LineSocket, address: int, value: int) -> None:
    reply = qtest.send_line(f"writel 0x{address:x} 0x{value & 0xffffffff:x}")
    if reply != "OK":
        raise RuntimeError(f"unexpected qtest write reply: {reply!r}")


def validate_cpu_topology(cpus: Any) -> list[str]:
    if not isinstance(cpus, list) or len(cpus) != 5:
        raise RuntimeError(f"expected four A53s and one VPU, got {cpus!r}")

    qom_types = [str(cpu.get("qom-type", "")) for cpu in cpus
                 if isinstance(cpu, dict)]
    arm_count = sum("cortex-a53" in qom_type for qom_type in qom_types)
    vc4_count = sum("vc4" in qom_type for qom_type in qom_types)
    if arm_count != 4 or vc4_count != 1:
        raise RuntimeError(
            f"unexpected heterogeneous topology: qom-types={qom_types!r}"
        )
    return qom_types


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-bootrom-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "virgin-sd.img"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        build_sd_image(image_path, build_bootcode())

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
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

        qmp: QMP | None = None
        qtest: LineSocket | None = None
        try:
            wait_for_socket(qmp_path, proc, 10.0)
            wait_for_socket(qtest_path, proc, 10.0)
            qmp = QMP(qmp_path)
            qtest = LineSocket(qtest_path)
            qom_types = validate_cpu_topology(qmp.execute("query-cpus-fast"))

            proc_before = parse_qtest_value(
                qtest.send_line(f"readl 0x{PM_PROC_ARM:x}")
            )
            if proc_before != 0:
                raise RuntimeError(
                    f"PM_PROC did not reset to zero: 0x{proc_before:08x}"
                )

            for index, instruction in enumerate(build_arm_payload()):
                qtest_writel(qtest, index * 4, instruction)

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            vpu_marker = 0
            arm_marker = 0
            proc_state = 0
            while time.monotonic() < deadline:
                vpu_marker = parse_qtest_value(
                    qtest.send_line(f"readl 0x{VPU_MARKER_ADDR:x}")
                )
                arm_marker = parse_qtest_value(
                    qtest.send_line(f"readl 0x{ARM_MARKER_ADDR:x}")
                )
                proc_state = parse_qtest_value(
                    qtest.send_line(f"readl 0x{PM_PROC_ARM:x}")
                )
                if (vpu_marker == VPU_MARKER_VALUE and
                        arm_marker == ARM_MARKER_VALUE and
                        proc_state == PM_PROC_READY):
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            if (vpu_marker != VPU_MARKER_VALUE or
                    arm_marker != ARM_MARKER_VALUE or
                    proc_state != PM_PROC_READY):
                raise RuntimeError(
                    "SD boot-ROM chain did not complete: "
                    f"vpu-marker=0x{vpu_marker:08x} "
                    f"arm-marker=0x{arm_marker:08x} "
                    f"PM_PROC=0x{proc_state:08x}"
                )

            print(
                "Raspberry Pi FAT32 boot-ROM chain passed: "
                f"cpus={len(qom_types)} partition-lba={PARTITION_LBA} "
                f"clusters={'->'.join(str(c) for c in BOOT_CLUSTERS)} "
                f"boot-bytes={BOOT_FILE_SIZE} "
                f"vpu-marker=0x{vpu_marker:08x} "
                f"pm-proc=0x{proc_state:08x} "
                f"arm-marker=0x{arm_marker:08x}"
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
