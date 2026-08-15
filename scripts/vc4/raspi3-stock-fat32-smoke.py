#!/usr/bin/env python3
"""Exercise the stock-firmware FAT32 writer without building QEMU."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
from types import ModuleType


def load_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock probe module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def patterned_bytes(label: bytes, size: int) -> bytes:
    if not label:
        raise ValueError("payload label must not be empty")
    repetitions = (size + len(label) - 1) // len(label)
    return (label * repetitions)[:size]


def main() -> int:
    probe = load_probe()
    bootcode = patterned_bytes(b"BOOTCODE-VC4-", 52624)
    # Match the exact pinned firmware sizes used by CI.  START.ELF is large
    # enough to force its chain through dozens of FAT sectors.
    start_elf = patterned_bytes(
        b"START-ELF-CROSSES-FAT-SECTORS-",
        3_022_336,
    )
    fixup_dat = patterned_bytes(b"FIXUP-DAT-", 7381)
    expected = {
        "BOOTCODE.BIN": bootcode,
        "START.ELF": start_elf,
        "FIXUP.DAT": fixup_dat,
    }

    with tempfile.TemporaryDirectory(prefix="vc4-fat32-smoke-") as tmp_s:
        image_path = Path(tmp_s) / "stock-firmware.img"
        boot_cluster_count, boot_last_cluster = probe.build_sd_image(
            image_path,
            bootcode,
            {
                "START.ELF": start_elf,
                "FIXUP.DAT": fixup_dat,
            },
        )
        chains = probe.verify_fat32_image(image_path, expected)

        boot_chain = chains["BOOTCODE.BIN"]
        start_chain = chains["START.ELF"]
        fixup_chain = chains["FIXUP.DAT"]
        if len(boot_chain) != boot_cluster_count:
            raise AssertionError(
                f"build API returned {boot_cluster_count} boot clusters; "
                f"verifier found {len(boot_chain)}"
            )
        if boot_chain[-1] != boot_last_cluster:
            raise AssertionError(
                f"build API returned last boot cluster {boot_last_cluster}; "
                f"verifier found {boot_chain[-1]}"
            )

        expected_lengths = {
            "BOOTCODE.BIN": 103,
            "START.ELF": 5_903,
            "FIXUP.DAT": 15,
        }
        actual_lengths = {
            "BOOTCODE.BIN": len(boot_chain),
            "START.ELF": len(start_chain),
            "FIXUP.DAT": len(fixup_chain),
        }
        if actual_lengths != expected_lengths:
            raise AssertionError(
                f"unexpected pinned-firmware chain lengths: {actual_lengths!r}"
            )
        if start_chain[0] != boot_chain[-1] + 1:
            raise AssertionError("START.ELF is not adjacent to BOOTCODE.BIN")
        if fixup_chain[0] != start_chain[-1] + 1:
            raise AssertionError("FIXUP.DAT is not adjacent to START.ELF")

        fat_sectors_touched = {
            cluster * 4 // probe.SECTOR_SIZE for cluster in start_chain
        }
        if len(fat_sectors_touched) != 47:
            raise AssertionError(
                "START.ELF should span 47 FAT sectors at the pinned size; "
                f"found {len(fat_sectors_touched)}"
            )

        second_fat_lba = (
            probe.PARTITION_LBA
            + probe.RESERVED_SECTORS
            + probe.FAT_SECTORS
        )
        corruption_offset = (
            second_fat_lba * probe.SECTOR_SIZE + start_chain[0] * 4
        )
        with image_path.open("r+b") as image:
            image.seek(corruption_offset)
            original = image.read(1)
            if len(original) != 1:
                raise AssertionError(
                    "could not read the second FAT for corruption"
                )
            image.seek(corruption_offset)
            image.write(bytes([original[0] ^ 0x01]))

        detected = False
        try:
            probe.verify_fat32_image(image_path, expected)
        except ValueError as exc:
            if "mirrored FAT32 allocation tables differ" not in str(exc):
                raise
            detected = True
        if not detected:
            raise AssertionError("FAT mirror corruption was not detected")

        with image_path.open("r+b") as image:
            image.seek(corruption_offset)
            image.write(original)
        probe.verify_fat32_image(image_path, expected)

        content_offset = (
            probe.cluster_lba(start_chain[len(start_chain) // 2])
            * probe.SECTOR_SIZE
        )
        with image_path.open("r+b") as image:
            image.seek(content_offset)
            original_content = image.read(1)
            if len(original_content) != 1:
                raise AssertionError("could not read START.ELF for corruption")
            image.seek(content_offset)
            image.write(bytes([original_content[0] ^ 0x80]))

        detected = False
        try:
            probe.verify_fat32_image(image_path, expected)
        except ValueError as exc:
            if "START.ELF content differs after FAT walk" not in str(exc):
                raise
            detected = True
        if not detected:
            raise AssertionError("START.ELF content corruption was not detected")

        with image_path.open("r+b") as image:
            image.seek(content_offset)
            image.write(original_content)
        probe.verify_fat32_image(image_path, expected)

        print(
            "VC4_STOCK_FAT32_SMOKE "
            f"boot-clusters={len(boot_chain)} "
            f"start-clusters={len(start_chain)} "
            f"fixup-clusters={len(fixup_chain)} "
            f"fat-sectors-touched={len(fat_sectors_touched)} "
            "mirror-corruption-detected=true "
            "content-corruption-detected=true"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
