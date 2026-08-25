#!/usr/bin/env python3
"""Validate WD40's C-only QEMU build contract.

This checks both source configuration and, when supplied, a configured build
directory.  Rust sources may remain for upstream provenance, but no active build
rule may invoke Rust tooling or compile a .rs file.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def require(path: str, needle: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    if needle not in text:
        raise SystemExit(f"{path}: required marker missing: {needle!r}")


def forbid(path: str, *needles: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    for needle in needles:
        if needle in text:
            raise SystemExit(f"{path}: forbidden active Rust integration: {needle!r}")


def check_source() -> None:
    require("meson.build", "have_rust = false")
    require(
        "meson.build",
        "Rust support is intentionally disabled in the WD40 fork",
    )
    require("hw/char/Kconfig", "select PL011_C")
    require("hw/timer/Kconfig", "select HPET_C")

    forbid(
        "meson.build",
        "rust = import('rust')",
        "have_rust = add_languages('rust'",
        "RUST_BACKTRACE=1",
        "subdir('rust')",
        "config_host_data.set('CONFIG_HAVE_RUST'",
        "['CONFIG_HAVE_RUST=y']",
        "rust_root_crate = find_program",
    )
    forbid("Kconfig.host", "config HAVE_RUST")
    forbid("hw/char/Kconfig", "HAVE_RUST", "X_PL011_RUST")
    forbid("hw/timer/Kconfig", "HAVE_RUST", "X_HPET_RUST")
    forbid("include/qemu/log.h", "rust_fwrite")
    forbid("util/log.c", "rust_fwrite", "CONFIG_HAVE_RUST")


def check_build(build_dir: Path) -> None:
    if not build_dir.is_dir():
        raise SystemExit(f"build directory does not exist: {build_dir}")

    candidates = [
        build_dir / "build.ninja",
        build_dir / "compile_commands.json",
    ]
    text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in candidates
        if path.exists()
    )
    if not text:
        raise SystemExit(f"no build metadata found below {build_dir}")

    forbidden_patterns = {
        "Rust compiler command": r"(?:^|[\s/])rustc(?:[\s\"']|$)",
        "Cargo command": r"(?:^|[\s/])cargo(?:[\s\"']|$)",
        "bindgen command": r"(?:^|[\s/])bindgen(?:[\s\"']|$)",
        "Rust source compilation": r"\.rs(?:[\s\"']|$)",
        "Rust source tree": r"(?:^|[\s\"'])rust/",
    }
    for label, pattern in forbidden_patterns.items():
        if re.search(pattern, text, flags=re.MULTILINE):
            raise SystemExit(f"{build_dir}: {label} present in active build graph")

    device_configs = list(build_dir.glob("*-config-devices.mak"))
    if not device_configs:
        raise SystemExit(f"{build_dir}: no generated device configurations found")
    combined = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in device_configs
    )
    if "CONFIG_HPET_C=y" not in combined:
        raise SystemExit("C HPET implementation was not selected")
    if "CONFIG_PL011_C=y" not in combined:
        raise SystemExit("C PL011 implementation was not selected")
    if "_RUST=y" in combined or "CONFIG_HAVE_RUST" in combined:
        raise SystemExit("generated device configuration still selects Rust")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "build_dir",
        nargs="?",
        type=Path,
        help="optional configured QEMU build directory",
    )
    args = parser.parse_args()

    check_source()
    if args.build_dir is not None:
        check_build(args.build_dir.resolve())


if __name__ == "__main__":
    main()
