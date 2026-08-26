#!/usr/bin/env python3
"""Validate the WD40 structured HMP completion service."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]


def source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def need(path: str, *markers: str) -> None:
    data = source(path)
    missing = [marker for marker in markers if marker not in data]
    if missing:
        raise SystemExit(f"{path}: missing {missing!r}")


def exactly_once(path: str, marker: str) -> None:
    count = source(path).count(marker)
    if count != 1:
        raise SystemExit(f"{path}: expected one {marker!r}, found {count}")


def completion_qapi_block() -> str:
    data = source("qapi/misc.json")
    start_marker = "##\n# @WD40HMPCompletion:\n"
    end_marker = "##\n# @LogCategoryInfo:\n"
    start = data.find(start_marker)
    end = data.find(end_marker, start + len(start_marker))
    if start < 0 or end < 0:
        raise SystemExit("qapi/misc.json: could not isolate completion block")
    return data[start:end]


def validate_qapi_doc_width() -> None:
    for offset, line in enumerate(completion_qapi_block().splitlines(), 1):
        if line.startswith("#") and len(line) > 70:
            raise SystemExit(
                "qapi/misc.json: WD40 completion documentation line "
                f"{offset} is {len(line)} columns: {line!r}"
            )


def validate_static() -> None:
    need(
        "qapi/misc.json",
        "'struct': 'WD40HMPCompletion'",
        "'command': 'x-wd40-complete-hmp'",
        "'replace-start': 'uint64'",
        "'capacity-reached': 'bool'",
        "'omitted-invalid-utf8': 'uint64'",
        "'allow-preconfig': true",
    )
    need(
        "monitor/hmp.c",
        "hmp_completion_discard_printf",
        "hmp_completion_clear",
        "qmp_x_wd40_complete_hmp",
        "READLINE_CMD_BUF_SIZE",
        "g_utf8_validate",
        "monitor_find_completion(hmp, command_prefix)",
        "READLINE_MAX_COMPLETIONS",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Context-sensitive HMP completion",
        "x-wd40-complete-hmp",
        "optional byte cursor",
        "fixed completion capacity",
    )
    exactly_once("qapi/misc.json", "'command': 'x-wd40-complete-hmp'")
    exactly_once(
        "monitor/hmp.c",
        "WD40HMPCompletion *qmp_x_wd40_complete_hmp",
    )
    exactly_once(
        "docs/devel/wd40-monitor-v2.rst",
        "Context-sensitive HMP completion",
    )
    validate_qapi_doc_width()


def run_qmp(binary: Path, workdir: Path, messages: list[dict]) -> dict[str, dict]:
    run = subprocess.run(
        [
            str(binary),
            "-machine", "none",
            "-display", "none",
            "-nodefaults",
            "-S",
            "-qmp", "stdio",
        ],
        cwd=workdir,
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    replies: dict[str, dict] = {}
    for line in run.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        identifier = item.get("id")
        if isinstance(identifier, str):
            replies[identifier] = item

    required = {
        message["id"]
        for message in messages
        if "id" in message and message["id"] != "quit"
    }
    missing = required.difference(replies)
    if missing:
        raise SystemExit(
            f"missing QMP replies {sorted(missing)!r}; rc={run.returncode}; "
            f"stdout={run.stdout!r}; stderr={run.stderr!r}"
        )
    return replies


def completion(replies: dict[str, dict], identifier: str) -> dict:
    reply = replies[identifier]
    result = reply.get("return")
    if not isinstance(result, dict):
        raise SystemExit(f"{identifier}: malformed completion reply: {reply!r}")
    candidates = result.get("candidates")
    if not isinstance(candidates, list) or not all(
        isinstance(candidate, str) for candidate in candidates
    ):
        raise SystemExit(f"{identifier}: malformed candidate list: {result!r}")
    if candidates != sorted(set(candidates)):
        raise SystemExit(f"{identifier}: candidates are not sorted and unique")
    if result.get("omitted-invalid-utf8") != 0:
        raise SystemExit(f"{identifier}: unexpected omitted candidates: {result!r}")
    if result.get("capacity-reached") is not False:
        raise SystemExit(f"{identifier}: unexpected capacity limit: {result!r}")
    return result


def expect_span(
    result: dict,
    *,
    cursor: int,
    replace_start: int,
    replace_length: int,
) -> None:
    expected = {
        "cursor": cursor,
        "replace-start": replace_start,
        "replace-length": replace_length,
    }
    actual = {key: result.get(key) for key in expected}
    if actual != expected:
        raise SystemExit(f"completion span mismatch: {actual!r} != {expected!r}")


def expect_error(replies: dict[str, dict], identifier: str, fragment: str) -> None:
    error = replies[identifier].get("error")
    if not isinstance(error, dict) or fragment not in error.get("desc", ""):
        raise SystemExit(
            f"{identifier}: expected error containing {fragment!r}: "
            f"{replies[identifier]!r}"
        )


def validate_runtime(build: Path) -> None:
    binary = build / "qemu-system-x86_64"
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")

    with tempfile.TemporaryDirectory(prefix="wd40-completion-") as temporary:
        workdir = Path(temporary)
        (workdir / "alpha.log").write_text("alpha\n", encoding="utf-8")
        (workdir / "alpine").mkdir()

        midline = "info reg ignored"
        midline_cursor = len("info reg".encode("utf-8"))
        too_long = "x" * 4096
        messages = [
            {"execute": "qmp_capabilities", "id": "cap"},
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": ""},
                "id": "empty",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": "inf"},
                "id": "root",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": "info reg"},
                "id": "subcommand",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {
                    "command-line": midline,
                    "cursor": midline_cursor,
                },
                "id": "midline",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": "logfile al"},
                "id": "file",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": "device_add vir"},
                "id": "device",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": "info", "cursor": 99},
                "id": "past-end",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": "help é", "cursor": 6},
                "id": "mid-codepoint",
            },
            {
                "execute": "x-wd40-complete-hmp",
                "arguments": {"command-line": too_long},
                "id": "too-long",
            },
            {"execute": "quit", "id": "quit"},
        ]
        replies = run_qmp(binary.resolve(), workdir, messages)

        empty = completion(replies, "empty")
        expect_span(empty, cursor=0, replace_start=0, replace_length=0)
        if "info" not in empty["candidates"]:
            raise SystemExit("empty: root command completion omitted 'info'")

        root = completion(replies, "root")
        expect_span(root, cursor=3, replace_start=0, replace_length=3)
        if "info" not in root["candidates"]:
            raise SystemExit("root: command completion omitted 'info'")

        subcommand = completion(replies, "subcommand")
        expect_span(subcommand, cursor=8, replace_start=5, replace_length=3)
        if "registers" not in subcommand["candidates"]:
            raise SystemExit(
                "subcommand: info completion omitted 'registers': "
                f"{subcommand!r}"
            )

        mid = completion(replies, "midline")
        expect_span(mid, cursor=8, replace_start=5, replace_length=3)
        if mid["candidates"] != subcommand["candidates"]:
            raise SystemExit("midline: suffix changed prefix completion results")

        file_result = completion(replies, "file")
        expect_span(file_result, cursor=10, replace_start=8, replace_length=2)
        for expected in ("alpha.log", "alpine/"):
            if expected not in file_result["candidates"]:
                raise SystemExit(
                    f"file: completion omitted {expected!r}: {file_result!r}"
                )

        device = completion(replies, "device")
        expect_span(device, cursor=14, replace_start=11, replace_length=3)
        if not any(candidate.startswith("virtio-") for candidate in device["candidates"]):
            raise SystemExit(
                "device: dynamic model completion returned no virtio candidate: "
                f"{device!r}"
            )

        expect_error(replies, "past-end", "exceeds command-line length")
        expect_error(replies, "mid-codepoint", "UTF-8 boundary")
        expect_error(replies, "too-long", "readline limit")

    print("WD40 structured HMP completion: runtime checks passed")


validate_static()
if len(sys.argv) > 2:
    raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
if len(sys.argv) == 2:
    validate_runtime(Path(sys.argv[1]).resolve())
