#!/usr/bin/env python3
"""Run the FAT boot-ROM regression with the hardware 0x200 handoff."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

BOOT_ENTRY = 0x200
BOOT_PAYLOAD_SIZE = 1300


def load_legacy_smoke() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-fat-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_fat_smoke", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load FAT smoke test from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def install_real_handoff(smoke: ModuleType) -> None:
    smoke.BOOT_ENTRY = BOOT_ENTRY
    smoke.BOOT_PAYLOAD_SIZE = BOOT_PAYLOAD_SIZE
    smoke.BOOT_FILE_SIZE = BOOT_ENTRY + BOOT_PAYLOAD_SIZE
    smoke.BOOT_CLUSTER_COUNT = (
        smoke.BOOT_FILE_SIZE + smoke.SECTOR_SIZE - 1
    ) // smoke.SECTOR_SIZE
    smoke.BOOT_CLUSTERS = (5, 9, 7) + tuple(
        range(10, 10 + smoke.BOOT_CLUSTER_COUNT - 3)
    )

    def build_bootcode() -> bytes:
        program = bytearray()
        program += smoke.vc4_mov32(0, smoke.VPU_MARKER_ADDR)
        program += smoke.vc4_mov32(1, smoke.VPU_MARKER_VALUE)
        program += smoke.vc4_memory_offset(True, 1, 0, 0)

        program += smoke.vc4_mov32(0, smoke.PM_PROC_GPU & ~0xFFF)
        for requested in (0x01, 0x05, 0x0D, 0x2D, 0x6D):
            program += smoke.vc4_mov32(1, 0x5A000000 | requested)
            program += smoke.vc4_memory_offset(
                True, 1, 0, smoke.PM_PROC_GPU & 0xFFF
            )

        program += smoke.half(0x0000)
        if len(program) > BOOT_PAYLOAD_SIZE:
            raise AssertionError("test program exceeds synthetic payload")
        program += bytes(
            (index * 37 + 11) & 0xFF
            for index in range(BOOT_PAYLOAD_SIZE - len(program))
        )
        image = bytes(BOOT_ENTRY) + bytes(program)
        if len(image) != smoke.BOOT_FILE_SIZE:
            raise AssertionError("unexpected synthetic bootcode size")
        return image

    smoke.build_bootcode = build_bootcode


def main() -> int:
    smoke = load_legacy_smoke()
    install_real_handoff(smoke)
    return int(smoke.main())


if __name__ == "__main__":
    raise SystemExit(main())
