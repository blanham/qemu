#!/usr/bin/env python3
"""Validate WD40's C-only QEMU build contract.

This checks both source configuration and, when supplied, a configured build
directory. Rust sources may remain for upstream provenance, but no command in
the selected emulator binaries' actual Ninja dependency closure may invoke
Rust tooling or compile a .rs file.
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPRESENTATIVE_TARGETS = (
    "qemu-system-x86_64",
    "qemu-system-aarch64",
)


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
    require(
        "meson.build",
        "summary_info += {'Rust support':      false}",
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
        "summary_info += {'Rust target':",
        "summary_info += {'rustc':",
        "summary_info += {'rustc version':",
        "summary_info += {'rustdoc':",
        "summary_info += {'bindgen':",
        "summary_info += {'bindgen version':",
    )
    forbid("Kconfig.host", "config HAVE_RUST")
    forbid("hw/char/Kconfig", "HAVE_RUST", "X_PL011_RUST")
    forbid("hw/timer/Kconfig", "HAVE_RUST", "X_HPET_RUST")
    forbid("include/qemu/log.h", "rust_fwrite")
    forbid("util/log.c", "rust_fwrite", "CONFIG_HAVE_RUST")


def match_context(text: str, pattern: str, *, limit: int = 8) -> list[str]:
    """Return compact, line-numbered context for the first matching lines."""
    regex = re.compile(pattern)
    matches: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if regex.search(line):
            matches.append(f"  {lineno}: {line[:500]}")
            if len(matches) == limit:
                break
    return matches


def ninja_commands(build_dir: Path) -> str:
    command = [
        "ninja",
        "-C",
        str(build_dir),
        "-t",
        "commands",
        *REPRESENTATIVE_TARGETS,
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise SystemExit(
            "failed to inspect the active Ninja command closure for "
            f"{', '.join(REPRESENTATIVE_TARGETS)}: {details}"
        )
    if not result.stdout.strip():
        raise SystemExit("Ninja returned an empty active command closure")
    return result.stdout


def check_build(build_dir: Path) -> None:
    if not build_dir.is_dir():
        raise SystemExit(f"build directory does not exist: {build_dir}")

    commands = ninja_commands(build_dir)
    forbidden_patterns = {
        "Rust compiler command": r"(?:^|[\s/])rustc(?:[\s\"']|$)",
        "Cargo command": r"(?:^|[\s/])cargo(?:[\s\"']|$)",
        "rustdoc command": r"(?:^|[\s/])rustdoc(?:[\s\"']|$)",
        "rustfmt command": r"(?:^|[\s/])rustfmt(?:[\s\"']|$)",
        "bindgen command": r"(?:^|[\s/])bindgen(?:[\s\"']|$)",
        "Rust source compilation": r"\.rs(?:[\s\"']|$)",
        "Rust source tree": r"(?:^|[\s\"'])rust/",
    }
    for label, pattern in forbidden_patterns.items():
        contexts = match_context(commands, pattern)
        if contexts:
            details = "\n".join(contexts)
            raise SystemExit(
                f"{build_dir}: {label} present in the active command closure\n"
                f"{details}"
            )

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
