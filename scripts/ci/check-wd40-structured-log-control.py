#!/usr/bin/env python3
"""Validate WD40's structured QMP log-category API."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_CATEGORY_FIELDS = {"name", "help", "enabled", "sticky"}
REQUIRED_CATEGORIES = {"int", "guest_errors", "unimp", "tid"}


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(path: str, *needles: str) -> None:
    contents = text(path)
    for needle in needles:
        if needle not in contents:
            raise SystemExit(f"{path}: required marker missing: {needle!r}")


def qmp_messages() -> list[dict[str, Any]]:
    return [
        {"execute": "qmp_capabilities", "id": "capabilities"},
        {"execute": "query-log-categories", "id": "initial"},
        {
            "execute": "set-log-categories",
            "arguments": {
                "action": "replace",
                "categories": ["guest_errors", "int"],
            },
            "id": "replace",
        },
        {"execute": "query-log-categories", "id": "after-replace"},
        {
            "execute": "set-log-categories",
            "arguments": {"action": "disable", "categories": ["int"]},
            "id": "disable",
        },
        {"execute": "query-log-categories", "id": "after-disable"},
        {
            "execute": "set-log-categories",
            "arguments": {"action": "enable", "categories": ["unimp"]},
            "id": "enable",
        },
        {"execute": "query-log-categories", "id": "after-enable"},
        {
            "execute": "set-log-categories",
            "arguments": {
                "action": "replace",
                "categories": ["definitely-not-a-log-category"],
            },
            "id": "invalid",
        },
        {"execute": "query-log-categories", "id": "after-invalid"},
        {
            "execute": "set-log-categories",
            "arguments": {"action": "replace", "categories": []},
            "id": "reset",
        },
        {"execute": "query-log-categories", "id": "after-reset"},
        {"execute": "quit", "id": "quit"},
    ]


def run_qmp(binary: Path, *, preconfig: bool = False) -> dict[str, dict[str, Any]]:
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

    payload = "\n".join(json.dumps(message) for message in qmp_messages()) + "\n"
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
        raise SystemExit(f"{binary.name}: structured logging QMP timed out") from exc

    responses: dict[str, dict[str, Any]] = {}
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
        if not isinstance(value, dict) or "id" not in value:
            continue
        responses[str(value["id"])] = value

    required = {
        "capabilities",
        "initial",
        "replace",
        "after-replace",
        "disable",
        "after-disable",
        "enable",
        "after-enable",
        "invalid",
        "after-invalid",
        "reset",
        "after-reset",
    }
    missing = required - responses.keys()
    if missing:
        raise SystemExit(
            f"{binary.name}: QMP responses missing {sorted(missing)}; "
            f"exit={completed.returncode}\nstdout={completed.stdout}\n"
            f"stderr={completed.stderr}"
        )
    return responses


def response_categories(
    label: str, response: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    if "error" in response:
        raise SystemExit(f"{label}: unexpected QMP error: {response['error']!r}")
    result = response.get("return")
    if not isinstance(result, list) or len(result) < 10:
        raise SystemExit(f"{label}: invalid category registry: {result!r}")

    categories: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(result):
        if not isinstance(entry, dict):
            raise SystemExit(f"{label}: category {index} is not an object")
        missing = REQUIRED_CATEGORY_FIELDS - entry.keys()
        if missing:
            raise SystemExit(
                f"{label}: category {index} missing fields {sorted(missing)}"
            )
        name = entry["name"]
        if not isinstance(name, str) or not name:
            raise SystemExit(f"{label}: invalid category name {name!r}")
        if name in categories:
            raise SystemExit(f"{label}: duplicate category {name!r}")
        if not isinstance(entry["help"], str) or not entry["help"]:
            raise SystemExit(f"{label}: {name}: missing help text")
        if not isinstance(entry["enabled"], bool):
            raise SystemExit(f"{label}: {name}: enabled is not boolean")
        if not isinstance(entry["sticky"], bool):
            raise SystemExit(f"{label}: {name}: sticky is not boolean")
        categories[name] = entry

    missing_required = REQUIRED_CATEGORIES - categories.keys()
    if missing_required:
        raise SystemExit(
            f"{label}: required categories missing {sorted(missing_required)}"
        )
    sticky = {name for name, entry in categories.items() if entry["sticky"]}
    if sticky != {"tid"}:
        raise SystemExit(f"{label}: unexpected sticky categories {sorted(sticky)}")
    return categories


def enabled_names(categories: dict[str, dict[str, Any]]) -> set[str]:
    return {name for name, entry in categories.items() if entry["enabled"]}


def validate_session(label: str, responses: dict[str, dict[str, Any]]) -> set[str]:
    initial = response_categories(label + " initial", responses["initial"])
    if enabled_names(initial):
        raise SystemExit(f"{label}: ordinary categories enabled at startup")

    replace = response_categories(label + " replace", responses["replace"])
    after_replace = response_categories(
        label + " after replace", responses["after-replace"]
    )
    if enabled_names(replace) != {"guest_errors", "int"}:
        raise SystemExit(f"{label}: replace action produced wrong state")
    if enabled_names(after_replace) != enabled_names(replace):
        raise SystemExit(f"{label}: replace response disagrees with query")

    disable = response_categories(label + " disable", responses["disable"])
    after_disable = response_categories(
        label + " after disable", responses["after-disable"]
    )
    if enabled_names(disable) != {"guest_errors"}:
        raise SystemExit(f"{label}: disable action produced wrong state")
    if enabled_names(after_disable) != enabled_names(disable):
        raise SystemExit(f"{label}: disable response disagrees with query")

    enable = response_categories(label + " enable", responses["enable"])
    after_enable = response_categories(
        label + " after enable", responses["after-enable"]
    )
    expected_enabled = {"guest_errors", "unimp"}
    if enabled_names(enable) != expected_enabled:
        raise SystemExit(f"{label}: enable action produced wrong state")
    if enabled_names(after_enable) != expected_enabled:
        raise SystemExit(f"{label}: enable response disagrees with query")

    invalid = responses["invalid"]
    error = invalid.get("error")
    if not isinstance(error, dict) or error.get("class") != "GenericError":
        raise SystemExit(f"{label}: invalid category was not rejected: {invalid!r}")
    after_invalid = response_categories(
        label + " after invalid", responses["after-invalid"]
    )
    if enabled_names(after_invalid) != expected_enabled:
        raise SystemExit(f"{label}: invalid request changed logging state")

    reset = response_categories(label + " reset", responses["reset"])
    after_reset = response_categories(
        label + " after reset", responses["after-reset"]
    )
    if enabled_names(reset) or enabled_names(after_reset):
        raise SystemExit(f"{label}: empty replacement did not disable logging")

    return set(initial)


def validate_runtime(build_dir: Path) -> None:
    x86_binary = build_dir / "qemu-system-x86_64"
    arm_binary = build_dir / "qemu-system-aarch64"
    for binary in (x86_binary, arm_binary):
        if not binary.is_file():
            raise SystemExit(f"runtime binary missing: {binary}")

    x86_names = validate_session("x86_64 ready", run_qmp(x86_binary))
    arm_names = validate_session("aarch64 ready", run_qmp(arm_binary))
    preconfig_names = validate_session(
        "x86_64 preconfig", run_qmp(x86_binary, preconfig=True)
    )

    if x86_names != arm_names:
        raise SystemExit("x86_64 and AArch64 log registries differ")
    if x86_names != preconfig_names:
        raise SystemExit("x86_64 log registry changed across initialization phase")

    print(
        "Structured log control: "
        f"categories={len(x86_names)} targets=2 preconfig=validated"
    )


def main() -> None:
    require(
        "qapi/misc.json",
        "# @LogCategoryInfo:",
        "# @LogCategoryAction:",
        "'command': 'query-log-categories'",
        "'command': 'set-log-categories'",
        "'allow-preconfig': true",
    )
    require(
        "include/qemu/log.h",
        "unsigned qemu_get_log_mask(void);",
    )
    require(
        "util/log.c",
        "unsigned qemu_get_log_mask(void)",
        "mask |= LOG_PER_THREAD;",
    )
    require(
        "monitor/qmp-cmds.c",
        '#include "qemu/log.h"',
        "static LogCategoryInfoList *qmp_log_category_info_list(void)",
        "LogCategoryInfoList *qmp_query_log_categories(Error **errp)",
        "LogCategoryInfoList *qmp_set_log_categories",
        "Unknown log category '%s'",
        "cannot be disabled once set",
    )
    require(
        "docs/devel/wd40-monitor-v2.rst",
        "Structured log-category control",
        "query-log-categories",
        "set-log-categories",
        "reported as sticky",
    )

    if len(sys.argv) > 2:
        raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
    if len(sys.argv) == 2:
        validate_runtime(Path(sys.argv[1]).resolve())


if __name__ == "__main__":
    main()
