#!/usr/bin/env python3
"""Validate a Pi 3 firmware volume containing start.elf and a kernel fixture.

With no arguments this test uses deterministic synthetic payloads.  CI also
runs it with the official, commit-pinned BOOTCODE.BIN, START.ELF, and FIXUP.DAT
files.  In both modes every file is reconstructed through the generated FAT
chains and compared byte for byte.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import struct
import tempfile

from firmware_fat32_volume import (
    SECTOR_SIZE,
    _chain,
    _fat_copy,
    _parse_geometry,
    build_volume,
    canonical_83,
    display_83,
    verify_volume,
)

CONFIG_TXT = (
    "arm_64bit=1\n"
    "kernel=kernel8.img\n"
    "device_tree=\n"
    "enable_uart=1\n"
    "disable_commandline_tags=1\n"
).encode("ascii")

ARM_MARKER = 0x4A11C0DE


def _pattern(size: int, seed: int) -> bytes:
    """Generate deterministic, incompressible-enough fixture bytes."""

    result = bytearray(size)
    state = seed & 0xFFFFFFFF
    for index in range(size):
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        result[index] = (state >> 24) ^ (index & 0xFF)
    return bytes(result)


def marker_kernel() -> bytes:
    """Return a tiny AArch64 kernel fixture that publishes a RAM marker."""

    instructions = (
        0xD2820000,  # movz x0, #0x1000
        0x52981BC1,  # movz w1, #0xc0de
        0x72A94221,  # movk w1, #0x4a11, lsl #16
        0xB9000001,  # str  w1, [x0]
        0x14000000,  # b    .
    )
    return b"".join(struct.pack("<I", instruction) for instruction in instructions)


def _synthetic_files() -> dict[str, bytes]:
    bootcode = bytearray(52_624)
    bootcode[0x200:] = _pattern(len(bootcode) - 0x200, 0xB007C0DE)
    return {
        "BOOTCODE.BIN": bytes(bootcode),
        # Force a cluster chain across many FAT sectors.
        "START.ELF": _pattern(3_145_987, 0x51A47E1F),
        "FIXUP.DAT": _pattern(9_173, 0xF17ED47A),
        "CONFIG.TXT": CONFIG_TXT,
        "KERNEL8.IMG": marker_kernel(),
    }


def _load_official(args: argparse.Namespace) -> dict[str, bytes]:
    required = (args.bootcode, args.start_elf, args.fixup)
    if all(path is None for path in required):
        return _synthetic_files()
    if any(path is None for path in required):
        raise ValueError(
            "--bootcode, --start-elf, and --fixup must be supplied together"
        )
    files = {
        "BOOTCODE.BIN": args.bootcode.read_bytes(),
        "START.ELF": args.start_elf.read_bytes(),
        "FIXUP.DAT": args.fixup.read_bytes(),
        "CONFIG.TXT": CONFIG_TXT,
        "KERNEL8.IMG": marker_kernel(),
    }
    return files


def _directory_layouts(layout) -> dict[str, object]:
    return {file.name: file for file in layout.files}


def validate_image(image_path: Path, files: dict[str, bytes]) -> str:
    layout = build_volume(image_path, files)
    actual = verify_volume(image_path, files)
    by_name = _directory_layouts(layout)

    if layout.fat_sectors <= 1:
        raise RuntimeError("test volume did not create a multi-sector FAT")
    start = by_name["START.ELF"]
    if start.cluster_count <= SECTOR_SIZE // 4:
        raise RuntimeError("START.ELF does not cross a FAT-sector boundary")

    image = image_path.read_bytes()
    geometry = _parse_geometry(image)
    fat = _fat_copy(image, geometry)
    chain = _chain(fat, start.first_cluster, start.cluster_count + 1)
    if len(chain) != start.cluster_count:
        raise RuntimeError(
            f"START.ELF chain has {len(chain)} clusters; "
            f"expected {start.cluster_count}"
        )
    if chain[-1] - chain[0] + 1 != len(chain):
        raise RuntimeError("deterministic START.ELF allocation is not contiguous")

    expected_names = {
        display_83(canonical_83(name)) for name in files
    }
    if set(actual) != expected_names:
        raise RuntimeError(
            f"root directory mismatch: {sorted(actual)} != {sorted(expected_names)}"
        )
    if struct.unpack_from("<I", files["KERNEL8.IMG"], 16)[0] != 0x14000000:
        raise RuntimeError("kernel fixture does not end in its expected branch loop")

    hashes = " ".join(
        f"{name.lower()}={hashlib.sha256(data).hexdigest()[:16]}"
        for name, data in sorted(files.items())
    )
    return (
        "STARTELF_VOLUME_READY "
        f"image={layout.image_size} fat_sectors={layout.fat_sectors} "
        f"start_clusters={start.cluster_count} "
        f"start_chain={chain[0]}->{chain[-1]} "
        f"kernel_marker=0x{ARM_MARKER:08x} {hashes}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootcode", type=Path)
    parser.add_argument("--start-elf", type=Path)
    parser.add_argument("--fixup", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    files = _load_official(args)
    if args.output:
        print(validate_image(args.output, files))
    else:
        with tempfile.TemporaryDirectory(prefix="vc4-startelf-volume-") as temp:
            print(validate_image(Path(temp) / "firmware.img", files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
