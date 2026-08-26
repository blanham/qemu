#!/usr/bin/env python3
"""Validate composition and replay invariants for WD40 source transforms."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require_count(path: str, needle: str, expected: int = 1) -> None:
    count = text(path).count(needle)
    if count != expected:
        raise SystemExit(
            f"{path}: expected {expected} occurrence(s) of {needle!r}, found {count}"
        )


def require_order(path: str, first: str, second: str) -> None:
    contents = text(path)
    first_pos = contents.find(first)
    second_pos = contents.find(second)
    if first_pos < 0 or second_pos < 0 or first_pos >= second_pos:
        raise SystemExit(
            f"{path}: expected {first!r} to appear exactly before {second!r}"
        )


def main() -> None:
    docs = "docs/devel/wd40-monitor-v2.rst"
    require_count(
        docs,
        "Structured log-category control\n-------------------------------\n",
    )
    require_count(
        docs,
        "The commands are available during preconfiguration.  Unknown category names\n"
        "are rejected atomically: no logging state changes unless every supplied name\n",
    )
    require_count(
        docs,
        "Each command also exposes an ``arguments`` array that decodes the internal\n",
    )

    transform = "scripts/wd40/apply-structured-log-control.py"
    require_count(transform, "if new.startswith(old):")
    require_count(transform, "generated suffix appears")
    require_count(transform, "generated prefix appears")

    hardening = "scripts/wd40/apply-structured-log-control-hardening.py"
    require_count(hardening, "if new.startswith(old):")
    require_count(hardening, "generated suffix appears")

    workflow = ".github/workflows/wd40-qol-integration.yml"
    require_order(
        workflow,
        "python3 scripts/wd40/apply-structured-log-control-composability.py",
        "python3 scripts/wd40/apply-structured-log-control.py",
    )
    require_order(
        workflow,
        "python3 scripts/ci/check-wd40-transform-composability.py",
        "python3 scripts/ci/check-wd40-structured-log-control.py",
    )

    print("WD40 transform composability: structured-log docs replay validated")


if __name__ == "__main__":
    main()
