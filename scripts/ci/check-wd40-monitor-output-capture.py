#!/usr/bin/env python3
"""Validate WD40 monitor text capture statically and against a built QEMU."""

from __future__ import annotations

import json
from pathlib import Path
import re
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


def validate_static() -> None:
    need(
        "qapi/misc.json",
        "'struct': 'WD40TextCapture'",
        "'command': 'x-wd40-capture-hmp'",
        "'*return-text': 'bool'",
        "'returns': 'WD40TextCapture'",
    )
    need(
        "monitor/qmp-cmds.c",
        "wd40_write_capture_file",
        "g_file_set_contents",
        "qemu_write_full",
        "wd40_capture_command_is_recursive",
        "nested WD40 output capture is not supported",
        "qmp_x_wd40_capture_hmp",
    )
    need(
        "monitor/hmp-cmds.c",
        "void hmp_capture_output",
        "qmp_x_wd40_capture_hmp",
        "qapi_free_WD40TextCapture",
        "captured %",
    )
    need(
        "hmp-commands.hx",
        "capture-output|save-output",
        "append:-a,quiet:-q,filename:F,command:S",
    )
    need(
        "docs/devel/wd40-monitor-v2.rst",
        "Text output capture",
        "x-wd40-capture-hmp",
        "consistent-replace or append mode",
    )
    exactly_once("qapi/misc.json", "'command': 'x-wd40-capture-hmp'")
    exactly_once(
        "monitor/qmp-cmds.c",
        "WD40TextCapture *qmp_x_wd40_capture_hmp",
    )
    exactly_once("monitor/hmp-cmds.c", "void hmp_capture_output")
    exactly_once(
        "hmp-commands.hx",
        '.name       = "capture-output|save-output"',
    )


def describe_bytes(data: bytes, limit: int = 160) -> str:
    preview = data[:limit]
    suffix = "" if len(data) <= limit else "..."
    return f"len={len(data)} data={preview!r}{suffix}"


def run_qmp(binary: Path, workdir: Path, messages: list[dict]) -> dict[str, dict]:
    command = [
        str(binary),
        "-machine", "none",
        "-display", "none",
        "-nodefaults",
        "-S",
        "-qmp", "stdio",
    ]
    run = subprocess.run(
        command,
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


def qmp_session(binary: Path, workdir: Path, *requests: dict) -> dict[str, dict]:
    return run_qmp(
        binary,
        workdir,
        [
            {"execute": "qmp_capabilities", "id": "cap"},
            *requests,
            {"execute": "quit", "id": "quit"},
        ],
    )


def capture_result(
    replies: dict[str, dict], identifier: str, *, expect_text: bool
) -> tuple[dict, bytes | None]:
    result = replies[identifier].get("return")
    if not isinstance(result, dict):
        raise SystemExit(f"{identifier}: malformed result: {replies[identifier]!r}")

    text = result.get("text")
    if expect_text:
        if not isinstance(text, str):
            raise SystemExit(f"{identifier}: missing returned text: {result!r}")
        text_bytes: bytes | None = text.encode("utf-8")
        if result.get("bytes") != len(text_bytes):
            raise SystemExit(f"{identifier}: byte count mismatch: {result!r}")
    else:
        if "text" in result:
            raise SystemExit(f"{identifier}: return-text=false was ignored: {result!r}")
        text_bytes = None
        if not isinstance(result.get("bytes"), int) or result["bytes"] < 0:
            raise SystemExit(f"{identifier}: invalid byte count: {result!r}")

    return result, text_bytes


def expect_error(replies: dict[str, dict], identifier: str, fragment: str) -> None:
    reply = replies[identifier]
    error = reply.get("error")
    if not isinstance(error, dict) or fragment not in error.get("desc", ""):
        raise SystemExit(
            f"{identifier}: expected error containing {fragment!r}: {reply!r}"
        )


def validate_qmp(binary: Path, workdir: Path) -> None:
    capture_path = workdir / "qmp-capture.txt"
    no_text_path = workdir / "qmp-no-text.txt"
    nested_path = workdir / "nested.txt"

    # Validate replacement before issuing another command so the test compares
    # the file against the exact response that produced it.
    capture_path.write_bytes(b"stale-output\n")
    replies = qmp_session(
        binary,
        workdir,
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {
                "command-line": "info version",
                "path": str(capture_path),
            },
            "id": "replace",
        },
    )
    first, first_bytes = capture_result(replies, "replace", expect_text=True)
    assert first_bytes is not None
    if first.get("path") != str(capture_path) or first.get("append") is not False:
        raise SystemExit(f"replace: path/append mismatch: {first!r}")
    actual = capture_path.read_bytes()
    if actual != first_bytes:
        raise SystemExit(
            "replace: file bytes do not match returned text; "
            f"file={describe_bytes(actual)}; "
            f"returned={describe_bytes(first_bytes)}"
        )

    # Return the second capture as well, avoiding any assumption that repeated
    # monitor commands must happen to produce byte-identical output.
    replies = qmp_session(
        binary,
        workdir,
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {
                "command-line": "info version",
                "path": str(capture_path),
                "append": True,
            },
            "id": "append",
        },
    )
    second, second_bytes = capture_result(replies, "append", expect_text=True)
    assert second_bytes is not None
    if second.get("path") != str(capture_path) or second.get("append") is not True:
        raise SystemExit(f"append: path/append mismatch: {second!r}")
    expected = first_bytes + second_bytes
    actual = capture_path.read_bytes()
    if actual != expected:
        raise SystemExit(
            "append: file bytes do not equal the two returned captures; "
            f"file={describe_bytes(actual)}; "
            f"expected={describe_bytes(expected)}"
        )

    # With returned text disabled, prove append preserved the existing prefix
    # and extended the file by exactly the API-reported byte count.
    replies = qmp_session(
        binary,
        workdir,
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {
                "command-line": "info version",
                "path": str(capture_path),
                "append": True,
                "return-text": False,
            },
            "id": "append-no-text",
        },
    )
    third, _ = capture_result(replies, "append-no-text", expect_text=False)
    if third.get("path") != str(capture_path) or third.get("append") is not True:
        raise SystemExit(f"append-no-text: path/append mismatch: {third!r}")
    actual = capture_path.read_bytes()
    if not actual.startswith(expected) or len(actual) != len(expected) + third["bytes"]:
        raise SystemExit(
            "append-no-text: existing bytes were not preserved or the file "
            "growth disagrees with the result; "
            f"before={describe_bytes(expected)}; "
            f"after={describe_bytes(actual)}; result={third!r}"
        )

    # Also exercise replacement where the file, rather than the QMP response,
    # is the sole output destination.
    no_text_path.write_bytes(b"stale-no-text\n")
    replies = qmp_session(
        binary,
        workdir,
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {
                "command-line": "info version",
                "path": str(no_text_path),
                "return-text": False,
            },
            "id": "replace-no-text",
        },
    )
    no_text, _ = capture_result(replies, "replace-no-text", expect_text=False)
    no_text_data = no_text_path.read_bytes()
    if no_text.get("path") != str(no_text_path) or no_text.get("append") is not False:
        raise SystemExit(f"replace-no-text: path/append mismatch: {no_text!r}")
    if len(no_text_data) != no_text["bytes"] or no_text_data.startswith(b"stale-"):
        raise SystemExit(
            "replace-no-text: replacement or byte count failed; "
            f"file={describe_bytes(no_text_data)}; result={no_text!r}"
        )

    replies = qmp_session(
        binary,
        workdir,
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {"command-line": "info version"},
            "id": "response-only",
        },
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {"command-line": "info version", "append": True},
            "id": "bad-append",
        },
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {
                "command-line": "info version",
                "return-text": False,
            },
            "id": "no-destination",
        },
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {"command-line": ""},
            "id": "empty",
        },
        {
            "execute": "x-wd40-capture-hmp",
            "arguments": {
                "command-line": f"capture-output {nested_path} info version"
            },
            "id": "nested",
        },
    )
    memory, memory_bytes = capture_result(
        replies, "response-only", expect_text=True
    )
    assert memory_bytes is not None
    if "path" in memory or memory.get("append") is not False:
        raise SystemExit(f"response-only: unexpected file metadata: {memory!r}")

    expect_error(replies, "bad-append", "append requires path")
    expect_error(replies, "no-destination", "at least one output destination")
    expect_error(replies, "empty", "command-line must not be empty")
    expect_error(replies, "nested", "nested WD40 output capture")
    if nested_path.exists():
        raise SystemExit("nested capture unexpectedly created a file")


def validate_hmp(binary: Path, workdir: Path) -> None:
    capture_path = workdir / "hmp-capture.txt"
    nested_path = workdir / "hmp-nested.txt"
    commands = "\n".join(
        [
            f"capture-output -q {capture_path} info version",
            f"capture-output -a -q {capture_path} info version",
            (
                f"capture-output -q {nested_path} "
                f"capture-output {nested_path} info version"
            ),
            "quit",
            "",
        ]
    )
    run = subprocess.run(
        [
            str(binary),
            "-machine", "none",
            "-display", "none",
            "-nodefaults",
            "-S",
            "-monitor", "stdio",
        ],
        cwd=workdir,
        input=commands,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    matches = re.findall(
        r"captured (\d+) bytes to '.*?'(?: \(append\))?",
        run.stdout,
    )
    if len(matches) != 2:
        raise SystemExit(
            f"HMP: expected two successful summaries; rc={run.returncode}; "
            f"stdout={run.stdout!r}; stderr={run.stderr!r}"
        )
    counts = [int(value) for value in matches]
    data = capture_path.read_bytes()
    if len(data) != sum(counts):
        raise SystemExit(
            f"HMP: byte counts {counts!r} do not match file size {len(data)}"
        )
    if counts[0] != counts[1] or data[: counts[0]] != data[counts[0] :]:
        raise SystemExit("HMP: append did not preserve two identical captures")
    if "nested WD40 output capture is not supported" not in run.stderr:
        raise SystemExit(
            f"HMP: nested capture was not rejected: stderr={run.stderr!r}"
        )
    if nested_path.exists():
        raise SystemExit("HMP: nested capture unexpectedly created a file")


def validate_runtime(build: Path) -> None:
    binary = build / "qemu-system-x86_64"
    if not binary.is_file():
        raise SystemExit(f"missing built emulator: {binary}")
    with tempfile.TemporaryDirectory(prefix="wd40-capture-") as temporary:
        workdir = Path(temporary)
        validate_qmp(binary.resolve(), workdir)
        validate_hmp(binary.resolve(), workdir)
    print("WD40 monitor output capture: QMP and HMP runtime checks passed")


validate_static()
if len(sys.argv) > 2:
    raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
if len(sys.argv) == 2:
    validate_runtime(Path(sys.argv[1]).resolve())
