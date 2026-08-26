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
         "'arguments': [ 'HMPArgumentInfo' ]", "# @filename:",
         "# @unknown:")
    need("monitor/hmp.c", "hmp_argument_kind", "hmp_argument_info_collect",
         "info->arguments = hmp_argument_info_collect")
    need("docs/devel/wd40-monitor-v2.rst", "Each command also exposes an ``arguments`` array")


def descriptors(row):
    return {(a.get("name"), a.get("kind"), a.get("optional", False),
             a.get("option"), a.get("takes-value"))
            for a in row.get("arguments", [])}


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

    by_path = {row.get("path"): row for row in rows}
    # Confirm the decoder is not merely returning empty arrays: require a
    # representative spread of semantic kinds from the live registry.
    kinds = {arg.get("kind") for row in rows for arg in row.get("arguments", [])}
    required = {"string", "integer", "option"}
    missing = required - kinds
    if missing:
        raise SystemExit(f"live HMP registry lacks decoded kinds {sorted(missing)}; got {sorted(kinds)}")

    # Every descriptor must retain the exact raw parser fragment and a name.
    for row in rows:
        for arg in row.get("arguments", []):
            if not arg.get("name") or not arg.get("raw-type"):
                raise SystemExit(f"malformed descriptor in {row.get('path')}: {arg!r}")
            if arg.get("kind") == "option":
                if not arg.get("option", "").startswith("-"):
                    raise SystemExit(f"short option missing spelling in {row.get('path')}: {arg!r}")
                if "takes-value" not in arg:
                    raise SystemExit(f"short option missing takes-value in {row.get('path')}: {arg!r}")

    log = by_path.get("log")
    if not log or not isinstance(log["arguments"], list) or not log["arguments"]:
        raise SystemExit("HMP log command did not expose decoded arguments")

    print(f"HMP argument introspection: commands={len(rows)} kinds={sorted(kinds)} validated")


validate_static()
if len(sys.argv) > 2:
    raise SystemExit(f"usage: {sys.argv[0]} [build-directory]")
if len(sys.argv) == 2:
    validate_runtime(Path(sys.argv[1]).resolve())
