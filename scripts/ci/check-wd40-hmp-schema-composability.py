#!/usr/bin/env python3
"""Validate single-owner HMP command and argument schemas."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def contents(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def exactly(path: str, marker: str, expected: int = 1) -> None:
    count = contents(path).count(marker)
    if count != expected:
        raise SystemExit(
            f"{path}: expected {expected} occurrence(s) of {marker!r}, found {count}"
        )


def main() -> None:
    qapi = "qapi/misc.json"
    exactly(qapi, "{ 'struct': 'HMPCommandInfo',")
    exactly(qapi, "{ 'command': 'query-hmp-commands',")
    exactly(qapi, "{ 'struct': 'HMPArgumentInfo',")
    exactly(qapi, "{ 'enum': 'HMPArgumentKind',")
    exactly(qapi, "'arguments': [ 'HMPArgumentInfo' ]")

    monitor = "monitor/hmp.c"
    exactly(monitor, "static char *hmp_command_canonical_component(")
    exactly(monitor, "static HMPCommandInfoList **hmp_command_info_collect(")
    exactly(monitor, "HMPCommandInfoList *qmp_query_hmp_commands(")
    exactly(monitor, "static HMPArgumentInfoList *hmp_argument_info_collect(")
    exactly(monitor, "info->arguments = hmp_argument_info_collect(")

    transform = "scripts/wd40/apply-hmp-command-introspection.py"
    exactly(transform, "owned_markers: tuple[str, ...] = ()")
    exactly(transform, '"{ \'struct\': \'HMPCommandInfo\',"')
    exactly(transform, '"{ \'command\': \'query-hmp-commands\',"')
    exactly(transform, '"static char *hmp_command_canonical_component("')
    exactly(transform, '"static HMPCommandInfoList **hmp_command_info_collect("')
    exactly(transform, '"HMPCommandInfoList *qmp_query_hmp_commands("')

    hardener = "scripts/wd40/apply-hmp-command-introspection-hardening.py"
    exactly(hardener, "owned_markers: tuple[str, ...] = ()")

    print(
        "WD40 HMP schema composability: single command/argument schema "
        "and collector validated"
    )


if __name__ == "__main__":
    main()
