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
        "tests/unit/test-logging.c",
        "static void test_parse_log_mask(void)",
        'qemu_str_to_log_mask("all,-int")',
        'qemu_str_to_log_mask("all,-int,+int")',
        'qemu_str_to_log_mask("-all,guest_errors")',
        'g_test_add_func("/logging/parse_mask", test_parse_log_mask);',
    )


if __name__ == "__main__":
    main()
