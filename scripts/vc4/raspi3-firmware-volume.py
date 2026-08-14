#!/usr/bin/env python3
"""Build and verify a multi-file FAT32 Raspberry Pi firmware volume.

The builder intentionally implements only the small, deterministic FAT32
subset needed by the Pi boot firmware: one MBR partition, two mirrored FATs,
a single root-directory cluster, and ordinary 8.3 files. It does not inject
any file into guest memory; bootcode.bin must read every later stage through
QEMU's emulated SD controller.
"""

from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path
import struct
import tempfile
from typing import Iterable

SECTOR_SIZE = 512
PARTITION_LBA = 2048
RESERVED_SECTORS = 32
FAT_COUNT = 2
SECTORS_PER_CLUSTER = 2
ROOT_CLUSTER = 2
EOC = 0x0FFFFFFF
FAT32_PARTITION_TYPE = 0x0C


@dataclasses.dataclass(frozen=True)
class InputFile:
    name: str
    path: Path
    data: bytes
    first_cluster: int = 0
    cluster_count: int = 0


@dataclasses.dataclass(frozen=True)
class Layout:
    total_sectors: int
    partition_sectors: int
    fat_sectors: int
    data_lba: int
    cluster_count: int
    cluster_bytes: int


def ceil_div(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def short_name(name: str) -> bytes:
    """Encode a strict uppercase FAT 8.3 name."""
    upper = name.upper()
    if upper.count(".") > 1:
        raise ValueError(f"not an 8.3 name: {name!r}")
    stem, _, ext = upper.partition(".")
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$%'-@~`!(){}^#&"
    if not stem or len(stem) > 8 or len(ext) > 3:
        raise ValueError(f"not an 8.3 name: {name!r}")
    if any(ch not in allowed for ch in stem + ext):
        raise ValueError(f"unsupported FAT character in {name!r}")
    return (
        stem.encode("ascii").ljust(8, b" ")
        + ext.encode("ascii").ljust(3, b" ")
    )


def compute_layout(size_mib: int) -> Layout:
    total_sectors = size_mib * 1024 * 1024 // SECTOR_SIZE
    partition_sectors = total_sectors - PARTITION_LBA
    if partition_sectors <= RESERVED_SECTORS:
        raise ValueError("image is too small")

    fat_sectors = 1
    while True:
        data_sectors = (
            partition_sectors - RESERVED_SECTORS - FAT_COUNT * fat_sectors
        )
        cluster_count = data_sectors // SECTORS_PER_CLUSTER
        required = ceil_div((cluster_count + 2) * 4, SECTOR_SIZE)
        if required == fat_sectors:
            break
        fat_sectors = required

    # FAT32 technically requires at least 65525 data clusters. Keeping this
    # invariant prevents firmware from selecting FAT16 semantics by count.
    if cluster_count < 65525:
        raise ValueError("image is too small for a conforming FAT32 volume")
    data_lba = (
        PARTITION_LBA + RESERVED_SECTORS + FAT_COUNT * fat_sectors
    )
    return Layout(
        total_sectors=total_sectors,
        partition_sectors=partition_sectors,
        fat_sectors=fat_sectors,
        data_lba=data_lba,
        cluster_count=cluster_count,
        cluster_bytes=SECTOR_SIZE * SECTORS_PER_CLUSTER,
    )


def allocate(files: Iterable[InputFile], layout: Layout) -> list[InputFile]:
    next_cluster = ROOT_CLUSTER + 1
    result: list[InputFile] = []
    for item in files:
        count = max(1, ceil_div(len(item.data), layout.cluster_bytes))
        end = next_cluster + count
        if end > layout.cluster_count + 2:
            raise ValueError(f"{item.name} does not fit in the FAT32 volume")
        result.append(
            dataclasses.replace(
                item,
                first_cluster=next_cluster,
                cluster_count=count,
            )
        )
        next_cluster = end
    if len(result) > layout.cluster_bytes // 32:
        raise ValueError("too many files for the single root-directory cluster")
    return result


def put_u16(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", buf, offset, value)


def put_u32(buf: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", buf, offset, value)


def create_boot_sector(layout: Layout) -> bytes:
    b = bytearray(SECTOR_SIZE)
    b[0:3] = b"\xeb\x58\x90"
    b[3:11] = b"MSWIN4.1"
    put_u16(b, 11, SECTOR_SIZE)
    b[13] = SECTORS_PER_CLUSTER
    put_u16(b, 14, RESERVED_SECTORS)
    b[16] = FAT_COUNT
    put_u16(b, 17, 0)
    put_u16(b, 19, 0)
    b[21] = 0xF8
    put_u16(b, 22, 0)
    put_u16(b, 24, 63)
    put_u16(b, 26, 255)
    put_u32(b, 28, PARTITION_LBA)
    put_u32(b, 32, layout.partition_sectors)
    put_u32(b, 36, layout.fat_sectors)
    put_u16(b, 40, 0)
    put_u16(b, 42, 0)
    put_u32(b, 44, ROOT_CLUSTER)
    put_u16(b, 48, 1)
    put_u16(b, 50, 6)
    b[64] = 0x80
    b[66] = 0x29
    put_u32(b, 67, 0x56433451)
    b[71:82] = b"VC4BOOT    "
    b[82:90] = b"FAT32   "
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def create_fsinfo(free_clusters: int, next_free: int) -> bytes:
    b = bytearray(SECTOR_SIZE)
    put_u32(b, 0, 0x41615252)
    put_u32(b, 484, 0x61417272)
    put_u32(b, 488, free_clusters)
    put_u32(b, 492, next_free)
    put_u32(b, 508, 0xAA550000)
    return bytes(b)


def create_mbr(layout: Layout) -> bytes:
    b = bytearray(SECTOR_SIZE)
    entry = 446
    b[entry] = 0x00
    b[entry + 1 : entry + 4] = b"\x00\x02\x00"
    b[entry + 4] = FAT32_PARTITION_TYPE
    b[entry + 5 : entry + 8] = b"\xfe\xff\xff"
    put_u32(b, entry + 8, PARTITION_LBA)
    put_u32(b, entry + 12, layout.partition_sectors)
    b[510:512] = b"\x55\xaa"
    return bytes(b)


def cluster_lba(layout: Layout, cluster: int) -> int:
    if cluster < 2:
        raise ValueError(f"invalid data cluster {cluster}")
    return layout.data_lba + (cluster - 2) * SECTORS_PER_CLUSTER


def make_fat(layout: Layout, files: list[InputFile]) -> bytes:
    fat = bytearray(layout.fat_sectors * SECTOR_SIZE)
    put_u32(fat, 0, 0x0FFFFFF8)
    put_u32(fat, 4, 0xFFFFFFFF)
    put_u32(fat, ROOT_CLUSTER * 4, EOC)
    for item in files:
        for index in range(item.cluster_count):
            cluster = item.first_cluster + index
            successor = (
                EOC if index + 1 == item.cluster_count else cluster + 1
            )
            put_u32(fat, cluster * 4, successor)
    return bytes(fat)


def directory_entry(item: InputFile) -> bytes:
    entry = bytearray(32)
    entry[0:11] = short_name(item.name)
    entry[11] = 0x20
    put_u16(entry, 20, item.first_cluster >> 16)
    put_u16(entry, 26, item.first_cluster & 0xFFFF)
    put_u32(entry, 28, len(item.data))
    return bytes(entry)


def build_image(
    output: Path,
    files: list[InputFile],
    size_mib: int,
) -> list[InputFile]:
    layout = compute_layout(size_mib)
    files = allocate(files, layout)
    output.parent.mkdir(parents=True, exist_ok=True)

    with output.open("wb") as image:
        image.truncate(layout.total_sectors * SECTOR_SIZE)
        image.seek(0)
        image.write(create_mbr(layout))

        boot = create_boot_sector(layout)
        used_clusters = 1 + sum(item.cluster_count for item in files)
        fsinfo = create_fsinfo(
            layout.cluster_count - used_clusters,
            ROOT_CLUSTER + 1 + sum(
                item.cluster_count for item in files
            ),
        )
        for relative_lba, data in (
            (0, boot),
            (1, fsinfo),
            (6, boot),
            (7, fsinfo),
        ):
            image.seek((PARTITION_LBA + relative_lba) * SECTOR_SIZE)
            image.write(data)

        fat = make_fat(layout, files)
        for index in range(FAT_COUNT):
            lba = (
                PARTITION_LBA
                + RESERVED_SECTORS
                + index * layout.fat_sectors
            )
            image.seek(lba * SECTOR_SIZE)
            image.write(fat)

        root = bytearray(layout.cluster_bytes)
        for index, item in enumerate(files):
            root[index * 32 : (index + 1) * 32] = directory_entry(item)
        image.seek(cluster_lba(layout, ROOT_CLUSTER) * SECTOR_SIZE)
        image.write(root)

        for item in files:
            image.seek(
                cluster_lba(layout, item.first_cluster) * SECTOR_SIZE
            )
            image.write(item.data)
    return files


def fat_entry(image, fat_lba: int, cluster: int) -> int:
    image.seek(fat_lba * SECTOR_SIZE + cluster * 4)
    return struct.unpack("<I", image.read(4))[0] & 0x0FFFFFFF


def verify_image(image_path: Path, expected: list[InputFile]) -> None:
    with image_path.open("rb") as image:
        mbr = image.read(SECTOR_SIZE)
        if mbr[510:512] != b"\x55\xaa":
            raise ValueError("bad MBR signature")
        partition_lba = struct.unpack_from("<I", mbr, 454)[0]
        if partition_lba != PARTITION_LBA:
            raise ValueError("unexpected partition start")

        image.seek(partition_lba * SECTOR_SIZE)
        boot = image.read(SECTOR_SIZE)
        if boot[82:90] != b"FAT32   ":
            raise ValueError("volume is not marked FAT32")
        spc = boot[13]
        reserved = struct.unpack_from("<H", boot, 14)[0]
        fats = boot[16]
        fat_sectors = struct.unpack_from("<I", boot, 36)[0]
        root_cluster = struct.unpack_from("<I", boot, 44)[0]
        data_lba = partition_lba + reserved + fats * fat_sectors
        cluster_bytes = spc * SECTOR_SIZE

        first_fat_lba = partition_lba + reserved
        image.seek(first_fat_lba * SECTOR_SIZE)
        fat0 = image.read(fat_sectors * SECTOR_SIZE)
        for index in range(1, fats):
            image.seek(
                (first_fat_lba + index * fat_sectors) * SECTOR_SIZE
            )
            if image.read(fat_sectors * SECTOR_SIZE) != fat0:
                raise ValueError("FAT copies differ")

        root_lba = data_lba + (root_cluster - 2) * spc
        image.seek(root_lba * SECTOR_SIZE)
        root = image.read(cluster_bytes)
        entries: dict[bytes, tuple[int, int]] = {}
        for offset in range(0, cluster_bytes, 32):
            entry = root[offset : offset + 32]
            if entry[0] in (0x00, 0xE5):
                if entry[0] == 0x00:
                    break
                continue
            if entry[11] == 0x0F:
                continue
            first = struct.unpack_from("<H", entry, 20)[0] << 16
            first |= struct.unpack_from("<H", entry, 26)[0]
            size = struct.unpack_from("<I", entry, 28)[0]
            entries[entry[0:11]] = (first, size)

        for item in expected:
            encoded = short_name(item.name)
            if encoded not in entries:
                raise ValueError(
                    f"missing directory entry for {item.name}"
                )
            cluster, size = entries[encoded]
            if size != len(item.data):
                raise ValueError(f"wrong size for {item.name}")
            result = bytearray()
            visited: set[int] = set()
            while cluster < 0x0FFFFFF8:
                if cluster < 2 or cluster in visited:
                    raise ValueError(f"bad FAT chain for {item.name}")
                visited.add(cluster)
                lba = data_lba + (cluster - 2) * spc
                image.seek(lba * SECTOR_SIZE)
                result.extend(image.read(cluster_bytes))
                cluster = fat_entry(image, first_fat_lba, cluster)
            if bytes(result[:size]) != item.data:
                raise ValueError(f"content mismatch for {item.name}")


def parse_inputs(values: list[str]) -> list[InputFile]:
    files: list[InputFile] = []
    seen: set[bytes] = set()
    for value in values:
        if "=" not in value:
            raise ValueError(f"expected NAME=PATH, got {value!r}")
        name, raw_path = value.split("=", 1)
        encoded = short_name(name)
        if encoded in seen:
            raise ValueError(f"duplicate FAT name {name!r}")
        seen.add(encoded)
        path = Path(raw_path)
        files.append(
            InputFile(name=name, path=path, data=path.read_bytes())
        )
    return files


def selftest() -> None:
    with tempfile.TemporaryDirectory(prefix="vc4-fat32-") as temp:
        root = Path(temp)
        payloads = {
            "BOOTCODE.BIN": bytes(
                (i * 17 + 3) & 0xFF for i in range(52624)
            ),
            "START.ELF": bytes(
                (i * 29 + 7) & 0xFF for i in range(3_150_123)
            ),
            "FIXUP.DAT": bytes(
                (i * 31 + 11) & 0xFF for i in range(8713)
            ),
            "CONFIG.TXT": b"arm_64bit=1\nkernel=kernel8.img\n",
            "KERNEL8.IMG": bytes(
                (i * 13 + 5) & 0xFF for i in range(65536)
            ),
        }
        files: list[InputFile] = []
        for name, data in payloads.items():
            path = root / name.lower()
            path.write_bytes(data)
            files.append(InputFile(name=name, path=path, data=data))
        image = root / "firmware.img"
        allocated = build_image(image, files, 128)
        verify_image(image, allocated)
        start = next(
            item for item in allocated if item.name == "START.ELF"
        )
        if start.cluster_count * 4 <= SECTOR_SIZE:
            raise AssertionError(
                "START.ELF did not cross a FAT-sector boundary"
            )
        print(
            "Raspberry Pi firmware FAT32 self-test passed: "
            f"files={len(allocated)} "
            f"start_clusters={start.cluster_count} "
            f"image_bytes={image.stat().st_size}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(
        dest="command",
        required=True,
    )

    build = subparsers.add_parser("build")
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--size-mib", type=int, default=128)
    build.add_argument("files", nargs="+")

    verify = subparsers.add_parser("verify")
    verify.add_argument("--image", type=Path, required=True)
    verify.add_argument("files", nargs="+")

    subparsers.add_parser("selftest")
    args = parser.parse_args()

    if args.command == "selftest":
        selftest()
        return 0
    files = parse_inputs(args.files)
    if args.command == "build":
        allocated = build_image(args.output, files, args.size_mib)
        verify_image(args.output, allocated)
        print(f"created {args.output} with {len(files)} files")
        return 0
    verify_image(args.image, files)
    print(f"verified {args.image} with {len(files)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
