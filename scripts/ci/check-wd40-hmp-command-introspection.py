#!/usr/bin/env python3
"""Validate WD40's structured HMP command discovery contract."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_FIELDS = {
    "path",
    "names",
    "args-type",
    "parameters",
    "help",
    "available",
    "implemented",
    "architecture-available",
    "phase-available",
    "preconfig",
    "coroutine",
    "has-subcommands",
    "arch-mask",
}


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(path: str, *needles: str) -> None:
    contents = text(path)
    for needle in needles:
        if needle not in contents:
            raise SystemExit(f"{path}: required marker missing: {needle!r}")


def run_qmp(binary: Path, *, preconfig: bool = False) -> list[dict[str, Any]]:
    command = [
        str(binary),
        "-machine",
        "none",
        "-display",
        "none",
        "-nodefaults",
        "-S",
        "-qmp",
        "stdio",
    ]
    if preconfig:
        command.insert(1, "--preconfig")

    payload = "\n".join(
        json.dumps(message)
        for message in (
            {"execute": "qmp_capabilities"},
            {"execute": "query-hmp-commands"},
            {"execute": "quit"},
        )
    ) + "\n"

    try:
        completed = subprocess.run(
            command,
            input=payload,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=45,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SystemExit(f"{binary.name}: QMP introspection timed out") from exc

    objects: list[dict[str, Any]] = []
    for line in completed.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(
                f"{binary.name}: invalid QMP JSON line: {line!r}"
            ) from exc
        if isinstance(value, dict):
            objects.append(value)

    errors = [obj["error"] for obj in objects if "error" in obj]
    if errors:
        raise SystemExit(
            f"{binary.name}: QMP returned errors: {errors!r}\n{completed.stderr}"
        )

    for obj in objects:
        result = obj.get("return")
        if isinstance(result, list):
            return result

    raise SystemExit(
        f"{binary.name}: query-hmp-commands response missing; "
        f"exit={completed.returncode}\nstdout={completed.stdout}\n"
        f"stderr={completed.stderr}"
    )


def validate_entries(
    label: str, entries: list[dict[str, Any]], *, ready: bool
) -> dict[str, dict[str, Any]]:
    if len(entries) < 50:
        raise SystemExit(f"{label}: suspiciously short HMP registry: {len(entries)}")

    by_path: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise SystemExit(f"{label}: entry {index} is not an object")
        missing = REQUIRED_FIELDS - entry.keys()
        if missing:
            raise SystemExit(f"{label}: entry {index} missing fields {sorted(missing)}")

        path = entry["path"]
        if not isinstance(path, str) or not path or "|" in path:
            raise SystemExit(f"{label}: invalid canonical path {path!r}")
        if path in by_path:
            raise SystemExit(f"{label}: duplicate canonical path {path!r}")
        by_path[path] = entry

        for field in ("names", "args-type", "parameters", "help"):
            if not isinstance(entry[field], str):
                raise SystemExit(f"{label}: {path}: {field} is not a string")
        for field in (
            "available",
            "implemented",
            "architecture-available",
            "phase-available",
            "preconfig",
            "coroutine",
            "has-subcommands",
        ):
            if not isinstance(entry[field], bool):
                raise SystemExit(f"{label}: {path}: {field} is not boolean")
        if not isinstance(entry["arch-mask"], int) or entry["arch-mask"] < 0:
            raise SystemExit(f"{label}: {path}: invalid architecture mask")

        expected_available = (
            entry["implemented"]
            and entry["architecture-available"]
            and entry["phase-available"]
        )
        if entry["available"] != expected_available:
            raise SystemExit(
                f"{label}: {path}: combined availability is inconsistent"
            )

    for path in ("help", "log", "info", "info registers", "cont"):
        if path not in by_path:
            raise SystemExit(f"{label}: required command path {path!r} missing")

    if not by_path["info"]["has-subcommands"]:
        raise SystemExit(f"{label}: info command did not expose its subtable")
    if not by_path["log"]["implemented"]:
        raise SystemExit(f"{label}: log command unexpectedly unimplemented")

    if ready:
        if not by_path["log"]["available"]:
            raise SystemExit(f"{label}: log command unavailable after initialization")
        if not by_path["cont"]["phase-available"]:
            raise SystemExit(f"{label}: cont remained phase-disabled after initialization")
    else:
        if by_path["cont"]["phase-available"]:
            raise SystemExit(f"{label}: cont unexpectedly available in preconfig")
        if by_path["cont"]["available"]:
            raise SystemExit(f"{label}: cont unexpectedly executable in preconfig")
        if not any(entry["preconfig"] for entry in entries):
            raise SystemExit(f"{label}: no preconfig-capable HMP commands reported")
        if not any(entry["phase-available"] for entry in entries):
            raise SystemExit(f"{label}: no HMP commands available in preconfig")

    return by_path


def validate_runtime(build_dir: Path) -> None:
    x86_binary = build_dir / "qemu-system-x86_64"
    arm_binary = build_dir / "qemu-system-aarch64"
    for binary in (x86_binary, arm_binary):
        if not binary.is_file():
            raise SystemExit(f"runtime binary missing: {binary}")

    x86_ready = validate_entries(
        "x86_64 ready", run_qmp(x86_binary), ready=True
    )
    arm_ready = validate_entries(
        "aarch64 ready", run_qmp(arm_binary), ready=True
    )
    x86_preconfig = validate_entries(
        "x86_64 preconfig", run_qmp(x86_binary, preconfig=True), ready=False
    )

    common = x86_ready.keys() & arm_ready.keys()
    if len(common) < 40:
        raise SystemExit(
            f"target command registries share too few paths: {len(common)}"
        )
    if x86_ready.keys() != x86_preconfig.keys():
        raise SystemExit("x86_64 command paths changed across initialization phase")

    print(
        "HMP command introspection: "
        f"x86_64={len(x86_ready)} aarch64={len(arm_ready)} "
        f"common={len(common)}"
    )


def main() -> None:
    require(
        "qapi/misc.json",
        "# @HMPCommandInfo:",
        "'architecture-available': 'bool'",
        "'phase-available': 'bool'",
        "'command': 'query-hmp-commands'",
        "'allow-preconfig': true",
    )
    require(
        "monitor/hmp.c",
        '#include "qapi/qapi-commands-misc.h"',
        "static bool cmd_architecture_available",
        "static bool cmd_phase_available",
        "static HMPCommandInfoList **hmp_command_info_collect",
        "HMPCommandInfoList *qmp_query_hmp_commands",
        "info->available = architecture_available && phase_available &&",
        "info->has_subcommands = cmd->sub_table != NULL;",
    )
    require(
        "docs/devel/index.rst",
        "   wd40-monitor-v2",
    )
    require(
        "docs/devel/wd40-monitor-v2.rst",
        "Structured HMP command discovery",
        "query-hmp-commands",
        "TTYphoon",
    )

    if len(sys.argv) > 2:
        raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
    if len(sys.argv) == 2:
        validate_runtime(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
