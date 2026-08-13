#!/usr/bin/env python3
"""Run unmodified Raspberry Pi firmware and report its first barrier."""

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
from typing import BinaryIO, Mapping, Sequence

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
FIRST_FILE_CLUSTER = 5
FIRST_BOOT_CLUSTER = FIRST_FILE_CLUSTER
BOOT_CACHE_SIZE = 128 * 1024
BOOT_ENTRY = 0x8000
FAT32_EOC = 0x0FFFFFFF
FAT32_EOC_MIN = 0x0FFFFFF8
FAT32_BAD_CLUSTER = 0x0FFFFFF7

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


def write_sectors(image: BinaryIO, lba: int, data: bytes) -> None:
    if len(data) % SECTOR_SIZE:
        raise ValueError(f"multi-sector write has {len(data)} bytes")
    image.seek(lba * SECTOR_SIZE)
    image.write(data)


def data_lba() -> int:
    return PARTITION_LBA + RESERVED_SECTORS + FAT_COUNT * FAT_SECTORS


def cluster_lba(cluster: int) -> int:
    if cluster < 2:
        raise ValueError(f"invalid FAT32 cluster {cluster}")
    return data_lba() + (cluster - 2) * SECTORS_PER_CLUSTER


def cluster_size() -> int:
    return SECTOR_SIZE * SECTORS_PER_CLUSTER


def clusters_for_size(size: int) -> int:
    if size < 0:
        raise ValueError(f"negative file size {size}")
    if size == 0:
        return 0
    size_per_cluster = cluster_size()
    return (size + size_per_cluster - 1) // size_per_cluster


def fat_short_name(name: str) -> bytes:
    """Encode a strict DOS 8.3 file name for a short directory entry."""
    if name.count(".") > 1:
        raise ValueError(f"not an 8.3 name: {name!r}")
    base, separator, extension = name.upper().partition(".")
    if not base or len(base) > 8 or len(extension) > 3:
        raise ValueError(f"not an 8.3 name: {name!r}")
    if separator == "" and extension:
        raise AssertionError("partition returned an extension without a dot")

    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$~!#%&'()-@^`{}"
    for character in base + extension:
        if character not in allowed:
            raise ValueError(
                f"unsupported character {character!r} in 8.3 name {name!r}"
            )
    return base.encode("ascii").ljust(8, b" ") + extension.encode(
        "ascii"
    ).ljust(3, b" ")


def display_short_name(raw_name: bytes) -> str:
    if len(raw_name) != 11:
        raise ValueError(f"short name has {len(raw_name)} bytes")
    base = raw_name[:8].decode("ascii").rstrip()
    extension = raw_name[8:].decode("ascii").rstrip()
    return f"{base}.{extension}" if extension else base


def normalize_files(
    files: Sequence[tuple[str, bytes]],
) -> list[tuple[str, bytes, bytes]]:
    if not files:
        raise ValueError("the FAT32 image must contain at least one file")

    normalized: list[tuple[str, bytes, bytes]] = []
    seen: set[bytes] = set()
    for name, content in files:
        if not isinstance(content, bytes):
            raise TypeError(f"{name!r} content is not bytes")
        raw_name = fat_short_name(name)
        if raw_name in seen:
            raise ValueError(f"duplicate FAT32 short name {name!r}")
        seen.add(raw_name)
        normalized.append((display_short_name(raw_name), raw_name, content))
    return normalized


def make_mbr() -> bytes:
    mbr = bytearray(SECTOR_SIZE)
    partition = memoryview(mbr)[446:462]
    partition[0] = 0x00
    partition[1:4] = b"\x00\x02\x00"
    partition[4] = 0x0C  # FAT32 LBA
    partition[5:8] = b"\xFE\xFF\xFF"
    struct.pack_into("<I", partition, 8, PARTITION_LBA)
    struct.pack_into("<I", partition, 12, PARTITION_SECTORS)
    mbr[510:512] = b"\x55\xAA"
    return bytes(mbr)


def make_boot_sector() -> bytes:
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
    boot[71:82] = b"RPISTOCK   "
    boot[82:90] = b"FAT32   "
    boot[510:512] = b"\x55\xAA"
    return bytes(boot)


def make_fsinfo() -> bytes:
    fsinfo = bytearray(SECTOR_SIZE)
    struct.pack_into("<I", fsinfo, 0, 0x41615252)
    struct.pack_into("<I", fsinfo, 484, 0x61417272)
    struct.pack_into("<I", fsinfo, 488, 0xFFFFFFFF)
    struct.pack_into("<I", fsinfo, 492, 0xFFFFFFFF)
    struct.pack_into("<I", fsinfo, 508, 0xAA550000)
    return bytes(fsinfo)


def set_fat_entry(fat: bytearray, cluster: int, value: int) -> None:
    offset = cluster * 4
    if cluster < 0 or offset + 4 > len(fat):
        raise ValueError(f"FAT entry {cluster} is outside the allocated FAT")
    struct.pack_into("<I", fat, offset, value & 0x0FFFFFFF)


def link_cluster_chain(fat: bytearray, clusters: Sequence[int]) -> None:
    for index, current in enumerate(clusters):
        successor = FAT32_EOC if index + 1 == len(clusters) else clusters[index + 1]
        set_fat_entry(fat, current, successor)


def make_directory_entry(raw_name: bytes, first_cluster: int, size: int) -> bytes:
    if len(raw_name) != 11:
        raise ValueError(f"short name has {len(raw_name)} bytes")
    if not 0 <= size <= 0xFFFFFFFF:
        raise ValueError(f"file is too large for FAT32 directory entry: {size}")

    entry = bytearray(32)
    entry[0:11] = raw_name
    entry[11] = 0x20
    struct.pack_into("<H", entry, 20, first_cluster >> 16)
    struct.pack_into("<H", entry, 26, first_cluster & 0xFFFF)
    struct.pack_into("<I", entry, 28, size)
    return bytes(entry)


def build_fat32_image(
    path: Path,
    files: Sequence[tuple[str, bytes]],
) -> dict[str, tuple[int, ...]]:
    """Build a mirrored FAT32 volume and return each file's cluster chain."""
    normalized = normalize_files(files)
    size_per_cluster = cluster_size()
    entries_per_cluster = size_per_cluster // 32
    root_clusters_needed = (
        len(normalized) + 1 + entries_per_cluster - 1
    ) // entries_per_cluster
    root_cluster_pool = tuple(range(ROOT_CLUSTER, FIRST_FILE_CLUSTER))
    if root_clusters_needed > len(root_cluster_pool):
        maximum = len(root_cluster_pool) * entries_per_cluster - 1
        raise ValueError(f"too many root files; maximum is {maximum}")
    root_chain = root_cluster_pool[:root_clusters_needed]

    data_sectors = (
        PARTITION_SECTORS - RESERVED_SECTORS - FAT_COUNT * FAT_SECTORS
    )
    data_cluster_count = data_sectors // SECTORS_PER_CLUSTER
    maximum_data_cluster = data_cluster_count + 1
    fat_entry_count = FAT_SECTORS * SECTOR_SIZE // 4
    if maximum_data_cluster >= fat_entry_count:
        raise ValueError("FAT is too small for the configured data region")

    next_cluster = FIRST_FILE_CLUSTER
    layouts: list[tuple[str, bytes, bytes, tuple[int, ...]]] = []
    for display_name, raw_name, content in normalized:
        count = clusters_for_size(len(content))
        chain = tuple(range(next_cluster, next_cluster + count))
        if chain and chain[-1] > maximum_data_cluster:
            raise ValueError(
                f"{display_name} exceeds the FAT32 data region at cluster "
                f"{chain[-1]}"
            )
        if chain:
            next_cluster = chain[-1] + 1
        layouts.append((display_name, raw_name, content, chain))

    fat = bytearray(FAT_SECTORS * SECTOR_SIZE)
    set_fat_entry(fat, 0, 0x0FFFFFF8)
    set_fat_entry(fat, 1, FAT32_EOC)
    link_cluster_chain(fat, root_chain)
    for _, _, _, chain in layouts:
        link_cluster_chain(fat, chain)

    root = bytearray(len(root_chain) * size_per_cluster)
    for index, (_, raw_name, content, chain) in enumerate(layouts):
        first_cluster = chain[0] if chain else 0
        root[index * 32:(index + 1) * 32] = make_directory_entry(
            raw_name,
            first_cluster,
            len(content),
        )

    with path.open("w+b") as image:
        image.truncate(IMAGE_SIZE)
        write_sector(image, 0, make_mbr())

        boot = make_boot_sector()
        fsinfo = make_fsinfo()
        write_sector(image, PARTITION_LBA, boot)
        write_sector(image, PARTITION_LBA + 1, fsinfo)
        write_sector(image, PARTITION_LBA + 6, boot)
        write_sector(image, PARTITION_LBA + 7, fsinfo)

        for fat_index in range(FAT_COUNT):
            fat_lba = (
                PARTITION_LBA + RESERVED_SECTORS + fat_index * FAT_SECTORS
            )
            write_sectors(image, fat_lba, bytes(fat))

        for index, cluster in enumerate(root_chain):
            start = index * size_per_cluster
            write_sectors(
                image,
                cluster_lba(cluster),
                bytes(root[start:start + size_per_cluster]),
            )

        for _, _, content, chain in layouts:
            for index, cluster in enumerate(chain):
                start = index * size_per_cluster
                chunk = content[start:start + size_per_cluster]
                write_sectors(
                    image,
                    cluster_lba(cluster),
                    chunk.ljust(size_per_cluster, b"\x00"),
                )

    expected = {display_name: content for display_name, _, content, _ in layouts}
    verified = verify_fat32_image(path, expected)
    intended = {
        display_name: chain for display_name, _, _, chain in layouts
    }
    if verified != intended:
        raise ValueError(
            f"verified FAT32 layout differs from writer layout: "
            f"expected={intended!r} verified={verified!r}"
        )
    return intended


def read_exact(image: BinaryIO, offset: int, size: int) -> bytes:
    image.seek(offset)
    data = image.read(size)
    if len(data) != size:
        raise ValueError(
            f"short image read at offset {offset}: wanted {size}, got {len(data)}"
        )
    return data


def fat_entry(fat: bytes, cluster: int) -> int:
    offset = cluster * 4
    if cluster < 0 or offset + 4 > len(fat):
        raise ValueError(f"FAT chain references out-of-range cluster {cluster}")
    return struct.unpack_from("<I", fat, offset)[0] & 0x0FFFFFFF


def walk_fat_chain(fat: bytes, first_cluster: int) -> tuple[int, ...]:
    if first_cluster == 0:
        return ()

    chain: list[int] = []
    seen: set[int] = set()
    cluster = first_cluster
    maximum_steps = len(fat) // 4
    while True:
        if cluster < 2:
            raise ValueError(f"FAT chain references reserved cluster {cluster}")
        if cluster in seen:
            raise ValueError(f"FAT chain contains a cycle at cluster {cluster}")
        if len(chain) >= maximum_steps:
            raise ValueError("FAT chain exceeds the FAT entry count")
        seen.add(cluster)
        chain.append(cluster)

        successor = fat_entry(fat, cluster)
        if successor >= FAT32_EOC_MIN:
            return tuple(chain)
        if successor == 0:
            raise ValueError(f"FAT chain terminates in a free entry at {cluster}")
        if successor == FAT32_BAD_CLUSTER:
            raise ValueError(f"FAT chain reaches a bad cluster after {cluster}")
        cluster = successor


def verify_fat32_image(
    path: Path,
    expected_files: Mapping[str, bytes],
) -> dict[str, tuple[int, ...]]:
    """Reconstruct every expected file through the generated FAT chains."""
    expected = normalize_files(list(expected_files.items()))
    expected_by_raw = {
        raw_name: (display_name, content)
        for display_name, raw_name, content in expected
    }

    with path.open("rb") as image:
        mbr = read_exact(image, 0, SECTOR_SIZE)
        if mbr[510:512] != b"\x55\xAA":
            raise ValueError("MBR signature is missing")
        partition = mbr[446:462]
        if partition[4] not in (0x0B, 0x0C):
            raise ValueError(f"partition type is not FAT32: 0x{partition[4]:02x}")
        partition_lba = struct.unpack_from("<I", partition, 8)[0]
        partition_sectors = struct.unpack_from("<I", partition, 12)[0]
        if partition_lba != PARTITION_LBA:
            raise ValueError(
                f"partition starts at {partition_lba}, expected {PARTITION_LBA}"
            )
        if partition_sectors != PARTITION_SECTORS:
            raise ValueError(
                f"partition has {partition_sectors} sectors, expected "
                f"{PARTITION_SECTORS}"
            )

        boot_offset = partition_lba * SECTOR_SIZE
        boot = read_exact(image, boot_offset, SECTOR_SIZE)
        if boot[510:512] != b"\x55\xAA":
            raise ValueError("FAT32 boot-sector signature is missing")
        bytes_per_sector = struct.unpack_from("<H", boot, 11)[0]
        sectors_per_cluster = boot[13]
        reserved_sectors = struct.unpack_from("<H", boot, 14)[0]
        fat_count = boot[16]
        total_sectors = struct.unpack_from("<I", boot, 32)[0]
        fat_sectors = struct.unpack_from("<I", boot, 36)[0]
        root_cluster = struct.unpack_from("<I", boot, 44)[0]
        fsinfo_sector = struct.unpack_from("<H", boot, 48)[0]
        backup_boot_sector = struct.unpack_from("<H", boot, 50)[0]

        geometry = (
            bytes_per_sector,
            sectors_per_cluster,
            reserved_sectors,
            fat_count,
            total_sectors,
            fat_sectors,
            root_cluster,
        )
        expected_geometry = (
            SECTOR_SIZE,
            SECTORS_PER_CLUSTER,
            RESERVED_SECTORS,
            FAT_COUNT,
            PARTITION_SECTORS,
            FAT_SECTORS,
            ROOT_CLUSTER,
        )
        if geometry != expected_geometry:
            raise ValueError(
                f"unexpected FAT32 geometry {geometry!r}; "
                f"expected {expected_geometry!r}"
            )

        backup_boot = read_exact(
            image,
            (partition_lba + backup_boot_sector) * SECTOR_SIZE,
            SECTOR_SIZE,
        )
        if backup_boot != boot:
            raise ValueError("primary and backup FAT32 boot sectors differ")
        fsinfo = read_exact(
            image,
            (partition_lba + fsinfo_sector) * SECTOR_SIZE,
            SECTOR_SIZE,
        )
        backup_fsinfo = read_exact(
            image,
            (partition_lba + backup_boot_sector + fsinfo_sector) * SECTOR_SIZE,
            SECTOR_SIZE,
        )
        if fsinfo != backup_fsinfo:
            raise ValueError("primary and backup FAT32 FSInfo sectors differ")
        if struct.unpack_from("<I", fsinfo, 0)[0] != 0x41615252:
            raise ValueError("FSInfo lead signature is invalid")
        if struct.unpack_from("<I", fsinfo, 484)[0] != 0x61417272:
            raise ValueError("FSInfo structure signature is invalid")
        if struct.unpack_from("<I", fsinfo, 508)[0] != 0xAA550000:
            raise ValueError("FSInfo trailing signature is invalid")

        fat_size_bytes = fat_sectors * bytes_per_sector
        fat_start_lba = partition_lba + reserved_sectors
        fats = [
            read_exact(
                image,
                (fat_start_lba + index * fat_sectors) * bytes_per_sector,
                fat_size_bytes,
            )
            for index in range(fat_count)
        ]
        if any(candidate != fats[0] for candidate in fats[1:]):
            raise ValueError("mirrored FAT32 allocation tables differ")
        fat = fats[0]

        cluster_bytes = bytes_per_sector * sectors_per_cluster
        data_start_lba = fat_start_lba + fat_count * fat_sectors

        def read_cluster(cluster: int) -> bytes:
            if cluster < 2:
                raise ValueError(f"attempted to read reserved cluster {cluster}")
            lba = data_start_lba + (cluster - 2) * sectors_per_cluster
            return read_exact(image, lba * bytes_per_sector, cluster_bytes)

        root_chain = walk_fat_chain(fat, root_cluster)
        root_data = b"".join(read_cluster(cluster) for cluster in root_chain)
        directory: dict[bytes, tuple[int, int]] = {}
        for offset in range(0, len(root_data), 32):
            entry = root_data[offset:offset + 32]
            marker = entry[0]
            if marker == 0x00:
                break
            if marker == 0xE5:
                continue
            attributes = entry[11]
            if attributes == 0x0F or attributes & 0x08:
                continue
            raw_name = entry[0:11]
            if raw_name in directory:
                raise ValueError(
                    f"duplicate root entry {display_short_name(raw_name)}"
                )
            first_cluster = (
                struct.unpack_from("<H", entry, 20)[0] << 16
            ) | struct.unpack_from("<H", entry, 26)[0]
            size = struct.unpack_from("<I", entry, 28)[0]
            directory[raw_name] = (first_cluster, size)

        if set(directory) != set(expected_by_raw):
            actual_names = sorted(display_short_name(name) for name in directory)
            expected_names = sorted(
                display_short_name(name) for name in expected_by_raw
            )
            raise ValueError(
                f"root directory files differ: actual={actual_names!r} "
                f"expected={expected_names!r}"
            )

        used_clusters: set[int] = set(root_chain)
        verified: dict[str, tuple[int, ...]] = {}
        for raw_name, (display_name, expected_content) in expected_by_raw.items():
            first_cluster, recorded_size = directory[raw_name]
            if recorded_size != len(expected_content):
                raise ValueError(
                    f"{display_name} records {recorded_size} bytes, expected "
                    f"{len(expected_content)}"
                )
            chain = walk_fat_chain(fat, first_cluster)
            expected_cluster_count = clusters_for_size(recorded_size)
            if len(chain) != expected_cluster_count:
                raise ValueError(
                    f"{display_name} uses {len(chain)} clusters, expected "
                    f"{expected_cluster_count}"
                )
            overlap = used_clusters.intersection(chain)
            if overlap:
                raise ValueError(
                    f"{display_name} reuses allocated clusters {sorted(overlap)!r}"
                )
            used_clusters.update(chain)

            allocated = b"".join(read_cluster(cluster) for cluster in chain)
            content = allocated[:recorded_size]
            padding = allocated[recorded_size:]
            if content != expected_content:
                raise ValueError(f"{display_name} content differs after FAT walk")
            if any(padding):
                raise ValueError(f"{display_name} has non-zero cluster padding")
            verified[display_name] = chain

        nonzero_entries = {
            index
            for index in range(len(fat) // 4)
            if fat_entry(fat, index) != 0
        }
        expected_nonzero = {0, 1}.union(used_clusters)
        if nonzero_entries != expected_nonzero:
            unexpected = sorted(nonzero_entries - expected_nonzero)
            missing = sorted(expected_nonzero - nonzero_entries)
            raise ValueError(
                f"FAT allocation set differs: unexpected={unexpected[:16]!r} "
                f"missing={missing[:16]!r}"
            )

    return verified


def build_sd_image(
    path: Path,
    bootcode: bytes,
    firmware_files: Mapping[str, bytes] | None = None,
) -> tuple[int, int]:
    """Build the stock boot volume while preserving the historical return API."""
    if not bootcode:
        raise ValueError("bootcode.bin is empty")
    if len(bootcode) > BOOT_CACHE_SIZE:
        raise ValueError(
            f"bootcode.bin has {len(bootcode)} bytes; "
            f"VPU boot cache has {BOOT_CACHE_SIZE}"
        )
    if len(bootcode) <= BOOT_ENTRY:
        raise ValueError(
            f"bootcode.bin has no bytes at VPU entry 0x{BOOT_ENTRY:x}"
        )

    files: list[tuple[str, bytes]] = [("BOOTCODE.BIN", bootcode)]
    for name, content in (firmware_files or {}).items():
        if fat_short_name(name) == fat_short_name("BOOTCODE.BIN"):
            raise ValueError("firmware_files must not replace BOOTCODE.BIN")
        files.append((name, content))

    layouts = build_fat32_image(path, files)
    boot_chain = layouts["BOOTCODE.BIN"]
    if not boot_chain:
        raise AssertionError("non-empty BOOTCODE.BIN received an empty chain")
    return len(boot_chain), boot_chain[-1]


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


def read_optional_firmware(
    parser: argparse.ArgumentParser,
    start_elf: str | None,
    fixup_dat: str | None,
) -> dict[str, bytes]:
    if (start_elf is None) != (fixup_dat is None):
        parser.error("--start-elf and --fixup-dat must be supplied together")
    if start_elf is None:
        return {}

    result: dict[str, bytes] = {}
    for name, supplied_path in (
        ("START.ELF", start_elf),
        ("FIXUP.DAT", fixup_dat),
    ):
        assert supplied_path is not None
        path = Path(supplied_path).resolve()
        if not path.is_file():
            parser.error(f"not a file: {path}")
        content = path.read_bytes()
        if not content:
            parser.error(f"firmware file is empty: {path}")
        result[name] = content
    return result


def firmware_summary(
    bootcode: bytes,
    firmware_files: Mapping[str, bytes],
) -> str:
    files = [("BOOTCODE.BIN", bootcode), *firmware_files.items()]
    return ",".join(
        f"{name}:{len(content)}:{hashlib.sha256(content).hexdigest()}"
        for name, content in files
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument("bootcode", help="unmodified bootcode.bin")
    parser.add_argument("--start-elf", help="matching unmodified start.elf")
    parser.add_argument("--fixup-dat", help="matching unmodified fixup.dat")
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
    firmware_files = read_optional_firmware(
        parser,
        args.start_elf,
        args.fixup_dat,
    )
    digest = hashlib.sha256(bootcode).hexdigest()
    first_nonzero = next(
        (index for index, value in enumerate(bootcode) if value),
        None,
    )
    if first_nonzero is None:
        raise ValueError("bootcode.bin contains only zero bytes")

    with tempfile.TemporaryDirectory(prefix="vc4-stock-bootcode-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "stock-bootcode.img"
        stderr_path = tmp / "qemu.stderr"
        cluster_count, last_cluster = build_sd_image(
            image_path,
            bootcode,
            firmware_files,
        )

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
            match = match or ILLEGAL_RE.search(log)

        print(
            "Official bootcode probe: "
            f"bytes={len(bootcode)} sha256={digest} "
            f"first-nonzero=0x{first_nonzero:08x} "
            f"entry=0x{BOOT_ENTRY:08x} "
            f"entry-context={context_bytes(bootcode, BOOT_ENTRY, 24)} "
            f"clusters={FIRST_BOOT_CLUSTER}->{last_cluster} "
            f"cluster-count={cluster_count} "
            f"firmware={firmware_summary(bootcode, firmware_files)}"
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
