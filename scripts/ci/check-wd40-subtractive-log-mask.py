#!/usr/bin/env python3
"""Validate WD40's additive and subtractive QEMU ``-d`` log masks."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(path: str, *needles: str) -> None:
    contents = text(path)
    for needle in needles:
        if needle not in contents:
            raise SystemExit(f"{path}: required marker missing: {needle!r}")


def require_count(path: str, needle: str, expected: int = 1) -> None:
    count = text(path).count(needle)
    if count != expected:
        raise SystemExit(
            f"{path}: expected {expected} copies of {needle!r}, found {count}"
        )


def forbid(path: str, *needles: str) -> None:
    contents = text(path)
    for needle in needles:
        if needle in contents:
            raise SystemExit(f"{path}: forbidden stale marker present: {needle!r}")


def main() -> None:
    require(
        "util/log.c",
        "const char *part = *tmp;",
        "subtract = *part == '-';",
        "mask &= ~item_mask;",
        "mask |= item_mask;",
        "Items are processed left-to-right; prefix '+' to add and ",
        "\"'-' to remove.\\n\"",
    )
    forbid(
        "util/log.c",
        'if (g_str_equal(*tmp, "all"))',
        "goto found;",
    )
    require(
        "qemu-options.hx",
        "prefix items with '-' to exclude them",
        "Items are processed from left to right.",
        "-d all,-tid,-int,-exec,-cpu",
    )
    require(
        "hmp-commands.hx",
        "set logging items; prefix '-' to exclude",
        "(qemu) log all,-tid,-int,-exec,-cpu",
        "Use ``log none`` to disable logging.",
    )
    require(
        "linux-user/main.c",
        "enable logging; prefix '-' to exclude an item",
    )
    require(
        "bsd-user/main.c",
        "-d item1[,...]    enable logging; prefix '-' to exclude an item",
    )
    require(
        "tests/unit/test-logging.c",
        "static int all_log_items_mask(void)",
        "static void test_parse_log_mask(void)",
        'int int_mask = qemu_str_to_log_mask("int");',
        'qemu_str_to_log_mask("all,-int")',
        'qemu_str_to_log_mask("all,-int,+int")',
        'qemu_str_to_log_mask("-all,guest_errors")',
        'g_test_add_func("/logging/parse_mask", test_parse_log_mask);',
    )
    require_count("tests/unit/test-logging.c", "static int all_log_items_mask(void)")
    require_count("tests/unit/test-logging.c", "static void test_parse_log_mask(void)")
    require_count(
        "tests/unit/test-logging.c",
        'g_test_add_func("/logging/parse_mask", test_parse_log_mask);',
    )
    require(
        "scripts/wd40/apply-subtractive-log-mask.py",
        "def ensure_parse_mask_tests() -> None:",
        "transformed replacement appears",
        "Preserve the canonical block and any one-copy downstream extension.",
    )


if __name__ == "__main__":
    main()
