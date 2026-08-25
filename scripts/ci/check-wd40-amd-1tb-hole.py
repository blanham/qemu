#!/usr/bin/env python3
"""Validate WD40's explicit AMD 1 TiB HyperTransport-hole control."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AMD_ABOVE_1TB_START = 1 << 40
RAM_ABOVE_4G = re.compile(
    r"^\s*([0-9a-fA-F]+)-[0-9a-fA-F]+\s+.*:\s+"
    r"(?:alias\s+)?ram-above-4g(?:\s|$)",
    re.MULTILINE,
)


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(path: str, *needles: str) -> None:
    contents = text(path)
    for needle in needles:
        if needle not in contents:
            raise SystemExit(f"{path}: required marker missing: {needle!r}")


def forbid(path: str, *needles: str) -> None:
    contents = text(path)
    for needle in needles:
        if needle in contents:
            raise SystemExit(f"{path}: forbidden stale marker present: {needle!r}")


def check_source() -> None:
    require(
        "include/hw/i386/pc.h",
        "OnOffAuto amd_1tb_hole;",
        '#define PC_MACHINE_AMD_1TB_HOLE     "amd-1tb-hole"',
    )
    require(
        "hw/i386/pc.c",
        "static bool pc_machine_amd_1tb_hole_enabled",
        "pcms->amd_1tb_hole == ON_OFF_AUTO_AUTO",
        "return pcmc->enforce_amd_1tb_hole;",
        "return pcms->amd_1tb_hole == ON_OFF_AUTO_ON;",
        "pc_machine_amd_1tb_hole_enabled(pcms)",
        "pcms->amd_1tb_hole = ON_OFF_AUTO_AUTO;",
        "pc_machine_get_amd_1tb_hole",
        "pc_machine_set_amd_1tb_hole",
        'object_class_property_add(oc, PC_MACHINE_AMD_1TB_HOLE, "OnOffAuto"',
    )
    forbid(
        "hw/i386/pc.c",
        "IS_AMD_CPU(&cpu->env) && pcmc->enforce_amd_1tb_hole",
    )
    require(
        "hw/i386/pc_piix.c",
        "pcmc->enforce_amd_1tb_hole = false;",
    )
    require(
        "hw/i386/pc_q35.c",
        "pcmc->enforce_amd_1tb_hole = false;",
    )
    require(
        "docs/system/target-i386.rst",
        "i386/wd40-qol",
    )
    require(
        "docs/system/i386/wd40-qol.rst",
        "amd-1tb-hole",
        "``auto``",
        "``on``",
        "``off``",
    )


def run_machine(binary: Path, machine: str) -> tuple[int, str]:
    command = [
        str(binary),
        "-accel", "tcg",
        "-machine", machine,
        "-cpu", "EPYC,phys-bits=48",
        "-m", "size=3G,maxmem=1T,slots=1",
        "-S",
        "-display", "none",
        "-nodefaults",
        "-monitor", "stdio",
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            input="info mtree\nquit\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=60,
        )
    except subprocess.TimeoutExpired as exc:
        output = exc.stdout or ""
        raise SystemExit(f"{machine}: QEMU monitor test timed out\n{output}") from exc

    if result.returncode != 0:
        raise SystemExit(
            f"{machine}: QEMU exited with {result.returncode}\n{result.stdout}"
        )

    matches = [int(value, 16) for value in RAM_ABOVE_4G.findall(result.stdout)]
    if not matches:
        raise SystemExit(
            f"{machine}: could not locate ram-above-4g in info mtree\n"
            f"{result.stdout}"
        )
    return min(matches), result.stdout


def check_runtime(build_dir: Path) -> None:
    binary = build_dir / "qemu-system-x86_64"
    if not binary.is_file():
        raise SystemExit(f"missing x86_64 system emulator: {binary}")

    cases = (
        ("pc-q35-7.1,amd-1tb-hole=auto", True),
        ("pc-q35-7.1,amd-1tb-hole=off", False),
        ("pc-q35-7.0,amd-1tb-hole=auto", False),
        ("pc-q35-7.0,amd-1tb-hole=on", True),
    )
    for machine, expect_above_1tb in cases:
        start, output = run_machine(binary, machine)
        actual_above_1tb = start >= AMD_ABOVE_1TB_START
        if actual_above_1tb != expect_above_1tb:
            expectation = "at or above" if expect_above_1tb else "below"
            raise SystemExit(
                f"{machine}: ram-above-4g starts at 0x{start:x}; "
                f"expected {expectation} 1 TiB\n{output}"
            )
        print(f"{machine}: ram-above-4g=0x{start:x}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "build_dir",
        nargs="?",
        type=Path,
        help="optional configured QEMU build directory for runtime checks",
    )
    args = parser.parse_args()

    check_source()
    if args.build_dir is not None:
        check_runtime(args.build_dir.resolve())


if __name__ == "__main__":
    main()
