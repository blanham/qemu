#!/usr/bin/env python3
"""Build and verify a Raspberry Pi firmware FAT32 volume.

The first-stage VideoCore firmware must discover every later boot artifact
through the emulated SD controller.  This module deliberately builds a normal
MBR-partitioned FAT32 image instead of copying firmware into guest RAM.

The implementation is intentionally small and deterministic, but it supports
all features required by the Pi 3 firmware chain:

* two complete, mirrored FATs;
* cluster chains spanning arbitrary FAT sectors;
* multiple 8.3 root-directory entries;
* byte-for-byte extraction and chain verification; and
* sparse-looking, reproducible images suitable for CI compression.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import struct
from typing import Iterable, Mapping

SECTOR_SIZE = 512
DEFAULT_IMAGE_SIZE = 64 * 1024 * 1024
DEFAULT_PARTITION_LBA = 2048
DEFAULT_SECTORS_PER_CLUSTER = 1
RESERVED_SECTORS = 32
FAT_COUNT = 2
ROOT_CLUSTER = 2
FAT32_EOC = 0x0FFFFFFF
FAT32_BAD = 0x0FFFFFF7
PARTITION_TYPE_FAT32_LBA = 0x0C


@dataclass(frozen=True)
class FileLayout:
    """Placement metadata for one file in the generated volume."""

    name: str
    size: int
    first_cluster: int
    cluster_count: int
    sha256: str


@dataclass(frozen=True)
class VolumeLayout:
    """Geometry and file placement returned by :func:`build_volume`."""

    image_size: int
    partition_lba: int
    partition_sectors: int
    sectors_per_cluster: int
    reserved_sectors: int
    fat_count: int
    fat_sectors: int
    first_data_sector: int
    cluster_count: int
    free_clusters: int
    files: tuple[FileLayout, ...]


def _u16(buffer: bytearray | bytes, offset: int, value: int | None = None) -> int:
    if value is None:
        return struct.unpack_from("<H", buffer, offset)[0]
    struct.pack_into("<H", buffer, offset, value)
    return value


def _u32(buffer: bytearray | bytes, offset: int, value: int | None = None) -> int:
    if value is None:
        return struct.unpack_from("<I", buffer, offset)[0]
    struct.pack_into("<I", buffer, offset, value)
    return value


def canonical_83(name: str) -> bytes:
    """Return an uppercase, space-padded FAT 8.3 directory name."""

    upper = name.upper()
    if upper.count(".") > 1:
        raise ValueError(f"not an 8.3 filename: {name!r}")
    if "." in upper:
        stem, suffix = upper.rsplit(".", 1)
    else:
        stem, suffix = upper, ""
    invalid = set('"*+,/:;<=>?[\\]|')
    if not stem or len(stem) > 8 or len(suffix) > 3:
        raise ValueError(f"not an 8.3 filename: {name!r}")
    if any(ch in invalid or ord(ch) < 0x20 for ch in stem + suffix):
        raise ValueError(f"invalid FAT filename: {name!r}")
    return stem.encode("ascii").ljust(8, b" ") + suffix.encode("ascii").ljust(3, b" ")


def display_83(raw: bytes) -> str:
    """Convert an eleven-byte directory name into conventional spelling."""

    if len(raw) != 11:
        raise ValueError("FAT 8.3 names are exactly eleven bytes")
    stem = raw[:8].decode("ascii").rstrip()
    suffix = raw[8:].decode("ascii").rstrip()
    return f"{stem}.{suffix}" if suffix else stem


def _fat_sectors(partition_sectors: int, sectors_per_cluster: int) -> tuple[int, int]:
    """Solve the mutually dependent FAT-size and data-cluster equations."""

    fat_sectors = 1
    while True:
        data_sectors = partition_sectors - RESERVED_SECTORS - FAT_COUNT * fat_sectors
        if data_sectors <= 0:
            raise ValueError("partition is too small for FAT32 metadata")
        clusters = data_sectors // sectors_per_cluster
        required = ((clusters + 2) * 4 + SECTOR_SIZE - 1) // SECTOR_SIZE
        if required == fat_sectors:
            return fat_sectors, clusters
        fat_sectors = required


def _cluster_offset(
    partition_lba: int,
    first_data_sector: int,
    sectors_per_cluster: int,
    cluster: int,
) -> int:
    if cluster < 2:
        raise ValueError(f"invalid data cluster {cluster}")
    sector = partition_lba + first_data_sector
    sector += (cluster - 2) * sectors_per_cluster
    return sector * SECTOR_SIZE


def _write_boot_records(
    image: bytearray,
    partition_lba: int,
    partition_sectors: int,
    sectors_per_cluster: int,
    fat_sectors: int,
    free_clusters: int,
    next_free: int,
) -> None:
    """Write MBR, primary/backup BPBs, and primary/backup FSInfo sectors."""

    mbr = memoryview(image)[:SECTOR_SIZE]
    entry = 446
    mbr[entry] = 0x00
    mbr[entry + 1 : entry + 4] = b"\x00\x02\x00"
    mbr[entry + 4] = PARTITION_TYPE_FAT32_LBA
    mbr[entry + 5 : entry + 8] = b"\xff\xff\xff"
    struct.pack_into("<II", mbr, entry + 8, partition_lba, partition_sectors)
    mbr[510:512] = b"\x55\xaa"

    boot = bytearray(SECTOR_SIZE)
    boot[0:3] = b"\xeb\x58\x90"
    boot[3:11] = b"VC4QEMU "
    _u16(boot, 11, SECTOR_SIZE)
    boot[13] = sectors_per_cluster
    _u16(boot, 14, RESERVED_SECTORS)
    boot[16] = FAT_COUNT
    _u16(boot, 17, 0)
    _u16(boot, 19, 0)
    boot[21] = 0xF8
    _u16(boot, 22, 0)
    _u16(boot, 24, 63)
    _u16(boot, 26, 255)
    _u32(boot, 28, partition_lba)
    _u32(boot, 32, partition_sectors)
    _u32(boot, 36, fat_sectors)
    _u16(boot, 40, 0)
    _u16(boot, 42, 0)
    _u32(boot, 44, ROOT_CLUSTER)
    _u16(boot, 48, 1)
    _u16(boot, 50, 6)
    boot[64] = 0x80
    boot[66] = 0x29
    _u32(boot, 67, 0x56433432)
    boot[71:82] = b"VC4BOOT    "
    boot[82:90] = b"FAT32   "
    boot[510:512] = b"\x55\xaa"

    fsinfo = bytearray(SECTOR_SIZE)
    _u32(fsinfo, 0, 0x41615252)
    _u32(fsinfo, 484, 0x61417272)
    _u32(fsinfo, 488, free_clusters)
    _u32(fsinfo, 492, next_free)
    _u32(fsinfo, 508, 0xAA550000)

    partition = partition_lba * SECTOR_SIZE
    image[partition : partition + SECTOR_SIZE] = boot
    image[partition + SECTOR_SIZE : partition + 2 * SECTOR_SIZE] = fsinfo
    image[partition + 6 * SECTOR_SIZE : partition + 7 * SECTOR_SIZE] = boot
    image[partition + 7 * SECTOR_SIZE : partition + 8 * SECTOR_SIZE] = fsinfo


def _directory_entry(name: str, first_cluster: int, size: int) -> bytes:
    entry = bytearray(32)
    entry[0:11] = canonical_83(name)
    entry[11] = 0x20
    # 1980-01-01, 00:00:00 keeps images reproducible and remains DOS-valid.
    _u16(entry, 14, 0)
    _u16(entry, 16, 0x0021)
    _u16(entry, 18, 0x0021)
    _u16(entry, 22, 0)
    _u16(entry, 24, 0x0021)
    _u16(entry, 20, (first_cluster >> 16) & 0xFFFF)
    _u16(entry, 26, first_cluster & 0xFFFF)
    _u32(entry, 28, size)
    return bytes(entry)


def build_volume(
    output: Path,
    files: Mapping[str, bytes],
    *,
    image_size: int = DEFAULT_IMAGE_SIZE,
    partition_lba: int = DEFAULT_PARTITION_LBA,
    sectors_per_cluster: int = DEFAULT_SECTORS_PER_CLUSTER,
) -> VolumeLayout:
    """Create an MBR-partitioned FAT32 image containing ``files``."""

    if image_size % SECTOR_SIZE:
        raise ValueError("image size must be sector aligned")
    if sectors_per_cluster <= 0 or sectors_per_cluster & (sectors_per_cluster - 1):
        raise ValueError("sectors per cluster must be a positive power of two")
    if sectors_per_cluster > 128:
        raise ValueError("FAT32 clusters may not exceed 64 KiB")

    total_sectors = image_size // SECTOR_SIZE
    partition_sectors = total_sectors - partition_lba
    if partition_sectors <= RESERVED_SECTORS:
        raise ValueError("partition starts beyond the usable image")

    normalized: list[tuple[str, bytes]] = []
    seen: set[bytes] = set()
    for name, data in files.items():
        raw_name = canonical_83(name)
        if raw_name in seen:
            raise ValueError(f"duplicate FAT filename: {name}")
        seen.add(raw_name)
        normalized.append((display_83(raw_name), bytes(data)))
    if not normalized:
        raise ValueError("at least one firmware file is required")
    if len(normalized) * 32 > sectors_per_cluster * SECTOR_SIZE:
        raise ValueError("root directory does not fit its initial cluster")

    fat_sectors, cluster_count = _fat_sectors(partition_sectors, sectors_per_cluster)
    # A volume labeled FAT32 must meet the FAT32 data-cluster threshold.
    if cluster_count < 65525:
        raise ValueError("geometry is too small to be an unambiguous FAT32 volume")
    first_data_sector = RESERVED_SECTORS + FAT_COUNT * fat_sectors
    cluster_size = sectors_per_cluster * SECTOR_SIZE

    next_cluster = ROOT_CLUSTER + 1
    placements: list[tuple[FileLayout, bytes]] = []
    for name, data in normalized:
        needed = max(1, (len(data) + cluster_size - 1) // cluster_size)
        last = next_cluster + needed - 1
        if last >= cluster_count + 2:
            raise ValueError(f"{name} does not fit in the volume")
        layout = FileLayout(
            name=name,
            size=len(data),
            first_cluster=next_cluster,
            cluster_count=needed,
            sha256=hashlib.sha256(data).hexdigest(),
        )
        placements.append((layout, data))
        next_cluster = last + 1

    used_clusters = next_cluster - 2
    free_clusters = cluster_count - used_clusters
    image = bytearray(image_size)
    _write_boot_records(
        image,
        partition_lba,
        partition_sectors,
        sectors_per_cluster,
        fat_sectors,
        free_clusters,
        next_cluster if free_clusters else 0xFFFFFFFF,
    )

    fat = bytearray(fat_sectors * SECTOR_SIZE)
    _u32(fat, 0, 0x0FFFFFF8)
    _u32(fat, 4, FAT32_EOC)
    _u32(fat, ROOT_CLUSTER * 4, FAT32_EOC)
    for layout, _data in placements:
        for index in range(layout.cluster_count):
            cluster = layout.first_cluster + index
            value = FAT32_EOC if index + 1 == layout.cluster_count else cluster + 1
            _u32(fat, cluster * 4, value)

    partition = partition_lba * SECTOR_SIZE
    for fat_index in range(FAT_COUNT):
        offset = partition + (RESERVED_SECTORS + fat_index * fat_sectors) * SECTOR_SIZE
        image[offset : offset + len(fat)] = fat

    root_offset = _cluster_offset(
        partition_lba,
        first_data_sector,
        sectors_per_cluster,
        ROOT_CLUSTER,
    )
    for index, (layout, _data) in enumerate(placements):
        offset = root_offset + index * 32
        image[offset : offset + 32] = _directory_entry(
            layout.name,
            layout.first_cluster,
            layout.size,
        )

    for layout, data in placements:
        offset = _cluster_offset(
            partition_lba,
            first_data_sector,
            sectors_per_cluster,
            layout.first_cluster,
        )
        image[offset : offset + len(data)] = data

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(image)
    return VolumeLayout(
        image_size=image_size,
        partition_lba=partition_lba,
        partition_sectors=partition_sectors,
        sectors_per_cluster=sectors_per_cluster,
        reserved_sectors=RESERVED_SECTORS,
        fat_count=FAT_COUNT,
        fat_sectors=fat_sectors,
        first_data_sector=first_data_sector,
        cluster_count=cluster_count,
        free_clusters=free_clusters,
        files=tuple(layout for layout, _data in placements),
    )


def _parse_geometry(image: bytes) -> dict[str, int]:
    if len(image) < SECTOR_SIZE or image[510:512] != b"\x55\xaa":
        raise ValueError("missing MBR signature")
    partition_lba, partition_sectors = struct.unpack_from("<II", image, 454)
    boot_offset = partition_lba * SECTOR_SIZE
    boot = image[boot_offset : boot_offset + SECTOR_SIZE]
    if len(boot) != SECTOR_SIZE or boot[510:512] != b"\x55\xaa":
        raise ValueError("missing FAT boot-sector signature")
    bytes_per_sector = _u16(boot, 11)
    if bytes_per_sector != SECTOR_SIZE:
        raise ValueError(f"unsupported FAT sector size {bytes_per_sector}")
    sectors_per_cluster = boot[13]
    reserved = _u16(boot, 14)
    fat_count = boot[16]
    fat_sectors = _u32(boot, 36)
    root_cluster = _u32(boot, 44)
    first_data_sector = reserved + fat_count * fat_sectors
    return {
        "partition_lba": partition_lba,
        "partition_sectors": partition_sectors,
        "sectors_per_cluster": sectors_per_cluster,
        "reserved": reserved,
        "fat_count": fat_count,
        "fat_sectors": fat_sectors,
        "root_cluster": root_cluster,
        "first_data_sector": first_data_sector,
    }


def _fat_copy(image: bytes, geometry: Mapping[str, int], index: int = 0) -> bytes:
    if index >= geometry["fat_count"]:
        raise ValueError("FAT copy index is out of range")
    sector = geometry["partition_lba"] + geometry["reserved"]
    sector += index * geometry["fat_sectors"]
    size = geometry["fat_sectors"] * SECTOR_SIZE
    return image[sector * SECTOR_SIZE : sector * SECTOR_SIZE + size]


def _chain(fat: bytes, first_cluster: int, maximum: int) -> list[int]:
    chain: list[int] = []
    seen: set[int] = set()
    cluster = first_cluster
    while True:
        if cluster < 2 or cluster in seen or len(chain) >= maximum:
            raise ValueError("invalid or cyclic FAT cluster chain")
        seen.add(cluster)
        chain.append(cluster)
        offset = cluster * 4
        if offset + 4 > len(fat):
            raise ValueError("cluster points beyond the FAT")
        value = _u32(fat, offset) & 0x0FFFFFFF
        if value >= 0x0FFFFFF8:
            return chain
        if value == 0 or value == FAT32_BAD:
            raise ValueError("prematurely terminated FAT cluster chain")
        cluster = value


def _read_chain(image: bytes, geometry: Mapping[str, int], chain: Iterable[int]) -> bytes:
    result = bytearray()
    for cluster in chain:
        offset = _cluster_offset(
            geometry["partition_lba"],
            geometry["first_data_sector"],
            geometry["sectors_per_cluster"],
            cluster,
        )
        size = geometry["sectors_per_cluster"] * SECTOR_SIZE
        result.extend(image[offset : offset + size])
    return bytes(result)


def extract_files(image_path: Path) -> dict[str, bytes]:
    """Extract all ordinary 8.3 files from the root directory."""

    image = image_path.read_bytes()
    geometry = _parse_geometry(image)
    fat = _fat_copy(image, geometry)
    root_chain = _chain(fat, geometry["root_cluster"], 65536)
    root = _read_chain(image, geometry, root_chain)
    extracted: dict[str, bytes] = {}
    for offset in range(0, len(root), 32):
        entry = root[offset : offset + 32]
        if not entry or entry[0] == 0x00:
            break
        if entry[0] == 0xE5 or entry[11] == 0x0F:
            continue
        if entry[11] & 0x18:
            continue
        name = display_83(entry[0:11])
        first = (_u16(entry, 20) << 16) | _u16(entry, 26)
        size = _u32(entry, 28)
        chain = _chain(fat, first, max(1, (size + SECTOR_SIZE - 1) // SECTOR_SIZE + 1))
        data = _read_chain(image, geometry, chain)[:size]
        extracted[name] = data
    return extracted


def verify_volume(image_path: Path, expected: Mapping[str, bytes]) -> dict[str, bytes]:
    """Validate FAT mirroring, directory entries, chains, and file contents."""

    image = image_path.read_bytes()
    geometry = _parse_geometry(image)
    primary = _fat_copy(image, geometry, 0)
    for index in range(1, geometry["fat_count"]):
        if _fat_copy(image, geometry, index) != primary:
            raise ValueError(f"FAT copy {index} differs from the primary FAT")
    actual = extract_files(image_path)
    normalized = {display_83(canonical_83(name)): bytes(data) for name, data in expected.items()}
    if set(actual) != set(normalized):
        raise ValueError(
            f"directory mismatch: expected {sorted(normalized)}, got {sorted(actual)}"
        )
    for name, data in normalized.items():
        if actual[name] != data:
            raise ValueError(f"content mismatch for {name}")
    return actual


def _parse_file_arguments(arguments: Iterable[str]) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    for argument in arguments:
        if "=" not in argument:
            raise ValueError(f"expected NAME=PATH, got {argument!r}")
        name, path_text = argument.split("=", 1)
        path = Path(path_text)
        if not path.is_file():
            raise ValueError(f"firmware file does not exist: {path}")
        files[name] = path.read_bytes()
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--file",
        action="append",
        default=[],
        metavar="NAME=PATH",
        help="add one 8.3 file to the FAT32 root directory",
    )
    parser.add_argument("--image-size-mib", type=int, default=64)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()

    files = _parse_file_arguments(args.file)
    layout = build_volume(
        args.output,
        files,
        image_size=args.image_size_mib * 1024 * 1024,
    )
    if args.verify:
        verify_volume(args.output, files)
    manifest = asdict(layout)
    rendered = json.dumps(manifest, indent=2, sort_keys=True)
    if args.manifest:
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
