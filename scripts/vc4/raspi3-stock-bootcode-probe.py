#!/usr/bin/env python3
"""Run unmodified Raspberry Pi bootcode.bin and report its first barrier."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
from typing import BinaryIO

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
FIRST_BOOT_CLUSTER = 5
BOOT_CACHE_SIZE = 128 * 1024

ILLEGAL_RE = re.compile(
    r"VideoCore IV: unimplemented opcode 0x([0-9a-fA-F]+) "
    r"at 0x([0-9a-fA-F]+)"
)
ILLEGAL_FINAL_RE = re.compile(
    r"VideoCore IV: illegal instruction at 0x([0-9a-fA-F]+)"
)


def write_sector(image: BinaryIO, lba: int, data: bytes) -> None:
    if len(data) != SECTOR_SIZE:
        raise ValueError(f"sector write has {len(data)} bytes")
    image.seek(lba * SECTOR_SIZE)
    image.write(data)


def cluster_lba(cluster: int) -> int:
    data_lba = (PARTITION_LBA + RESERVED_SECTORS +
                FAT_COUNT * FAT_SECTORS)
    return data_lba + (cluster - 2) * SECTORS_PER_CLUSTER


def build_sd_image(path: Path, bootcode: bytes) -> tuple[int, int]:
    if not bootcode:
        raise ValueError("bootcode.bin is empty")
    if len(bootcode) > BOOT_CACHE_SIZE:
        raise ValueError(
            f"bootcode.bin has {len(bootcode)} bytes; "
            f"VPU boot cache has {BOOT_CACHE_SIZE}"
        )

    cluster_count = (len(bootcode) + SECTOR_SIZE - 1) // SECTOR_SIZE
    last_cluster = FIRST_BOOT_CLUSTER + cluster_count - 1
    if last_cluster * 4 >= SECTOR_SIZE:
        raise ValueError("stock bootcode no longer fits the compact test FAT")

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
        boot[21] = 0xF8
        struct.pack_into("<H", boot, 24, 63)
        struct.pack_into("<H", boot, 26, 255)
        struct.pack_into("<I", boot, 28, PARTITION_LBA)
        struct.pack_into("<I", boot, 32, PARTITION_SECTORS)
        struct.pack_into("<I", boot, 36, FAT_SECTORS)
        struct.pack_into("<I", boot, 44, ROOT_CLUSTER)
        struct.pack_into("<H", boot, 48, 1)
        struct.pack_into("<H", boot, 50, 6)
        boot[64] = 0x80
        boot[66] = 0x29
        struct.pack_into("<I", boot, 67, 0xB007C0DE)
        boot[71:82] = b"RPISTOCK   "
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
        struct.pack_into("<I", fat, 0 * 4, 0x0FFFFFF8)
        struct.pack_into("<I", fat, 1 * 4, 0xFFFFFFFF)
        struct.pack_into("<I", fat, ROOT_CLUSTER * 4, 0x0FFFFFFF)
        for cluster in range(FIRST_BOOT_CLUSTER, last_cluster + 1):
            next_cluster = (0x0FFFFFFF if cluster == last_cluster
                            else cluster + 1)
            struct.pack_into("<I", fat, cluster * 4, next_cluster)
        for fat_index in range(FAT_COUNT):
            fat_lba = (PARTITION_LBA + RESERVED_SECTORS +
                       fat_index * FAT_SECTORS)
            write_sector(image, fat_lba, bytes(fat))

        root = bytearray(SECTOR_SIZE)
        root[0:11] = b"BOOTCODEBIN"
        root[11] = 0x20
        struct.pack_into("<H", root, 20, FIRST_BOOT_CLUSTER >> 16)
        struct.pack_into("<H", root, 26, FIRST_BOOT_CLUSTER & 0xFFFF)
        struct.pack_into("<I", root, 28, len(bootcode))
        write_sector(image, cluster_lba(ROOT_CLUSTER), bytes(root))

        for index in range(cluster_count):
            cluster = FIRST_BOOT_CLUSTER + index
            chunk = bootcode[index * SECTOR_SIZE:(index + 1) * SECTOR_SIZE]
            write_sector(image, cluster_lba(cluster),
                         chunk.ljust(SECTOR_SIZE, b"\x00"))

    return cluster_count, last_cluster


def context_bytes(bootcode: bytes, pc: int, radius: int = 16) -> str:
    start = max(0, pc - radius)
    end = min(len(bootcode), pc + radius)
    return f"0x{start:08x}:" + bootcode[start:end].hex()


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument("bootcode", help="unmodified bootcode.bin")
    parser.add_argument("--seconds", type=float, default=5.0,
                        help="maximum execution time before reporting a stall")
    parser.add_argument(
        "--barrier-is-success",
        action="store_true",
        help="return success when the first illegal opcode is captured",
    )
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    bootcode_path = Path(args.bootcode).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")
    if not bootcode_path.is_file():
        parser.error(f"not a file: {bootcode_path}")

    bootcode = bootcode_path.read_bytes()
    digest = hashlib.sha256(bootcode).hexdigest()

    with tempfile.TemporaryDirectory(prefix="vc4-stock-bootcode-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "stock-bootcode.img"
        stderr_path = tmp / "qemu.stderr"
        cluster_count, last_cluster = build_sd_image(image_path, bootcode)

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
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        deadline = time.monotonic() + args.seconds
        match: re.Match[str] | None = None
        log = ""
        try:
            while time.monotonic() < deadline:
                log = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                match = ILLEGAL_RE.search(log)
                if match:
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.02)
        finally:
            stop_process(proc)
            log = stderr_path.read_text(encoding="utf-8", errors="replace")

        print(
            "Official bootcode probe: "
            f"bytes={len(bootcode)} sha256={digest} "
            f"clusters={FIRST_BOOT_CLUSTER}->{last_cluster} "
            f"cluster-count={cluster_count}"
        )

        if match:
            opcode = int(match.group(1), 16)
            pc = int(match.group(2), 16)
            final = ILLEGAL_FINAL_RE.search(log)
            print(
                "STOCK_BOOTCODE_BARRIER "
                f"kind=illegal-opcode opcode=0x{opcode:04x} pc=0x{pc:08x} "
                f"final-pc={final.group(1) if final else 'unknown'} "
                f"context={context_bytes(bootcode, pc)}"
            )
            if log:
                print("--- qemu stderr ---", file=os.sys.stderr)
                print(log, file=os.sys.stderr)
            return 0 if args.barrier_is_success else 2

        if proc.returncode not in (None, 0, -15):
            print(f"QEMU exited with status {proc.returncode}", file=os.sys.stderr)
        print(
            "STOCK_BOOTCODE_BARRIER kind=stall-or-unmodeled-mmio "
            f"seconds={args.seconds:g}",
            file=os.sys.stderr,
        )
        if log:
            print("--- qemu stderr ---", file=os.sys.stderr)
            print(log[-16384:], file=os.sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
