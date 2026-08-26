#!/usr/bin/env python3
"""Validate WD40 structured HMP argument introspection."""

from pathlib import Path
import json
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def source(path):
    return (ROOT / path).read_text(encoding="utf-8")


def need(path, *markers):
    data = source(path)
    missing = [m for m in markers if m not in data]
    if missing:
        raise SystemExit(f"{path}: missing {missing!r}")


def validate_static():
    need("qapi/misc.json", "HMPArgumentKind", "HMPArgumentInfo",
         "'arguments': [ 'HMPArgumentInfo' ]")
    need("monitor/hmp.c", "hmp_argument_kind", "hmp_argument_info_collect",
         "info->arguments = hmp_argument_info_collect")
    need("docs/devel/wd40-monitor-v2.rst", "Each command also exposes an ``arguments`` array")


def validate_runtime(build):
    binary = build / "qemu-system-x86_64"
    messages = [
        {"execute":"qmp_capabilities","id":"cap"},
        {"execute":"query-hmp-commands","id":"query"},
        {"execute":"quit","id":"quit"},
    ]
    run = subprocess.run([str(binary), "-machine", "none", "-display", "none",
                          "-nodefaults", "-S", "-qmp", "stdio"],
                         input="\n".join(json.dumps(m) for m in messages)+"\n",
                         text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         timeout=45, check=False)
    reply = None
    for line in run.stdout.splitlines():
        if line.lstrip().startswith("{"):
            item = json.loads(line)
            if item.get("id") == "query":
                reply = item
    if not reply or "error" in reply:
        raise SystemExit(f"bad query-hmp-commands reply: {reply!r}; stderr={run.stderr}")
    rows = reply.get("return", [])
    if not rows or any("arguments" not in row for row in rows):
        raise SystemExit("query-hmp-commands did not expose arguments arrays")
    log = next((row for row in rows if row.get("path") == "log"), None)
    if not log:
        raise SystemExit("missing HMP log command")
    if not isinstance(log["arguments"], list):
        raise SystemExit("log arguments is not a list")
    print(f"HMP argument introspection: commands={len(rows)} validated")


validate_static()
if len(sys.argv) > 2:
    raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
if len(sys.argv) == 2:
    validate_runtime(Path(sys.argv[1]).resolve())
