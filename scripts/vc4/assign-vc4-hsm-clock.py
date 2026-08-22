#!/usr/bin/env python3
"""Assign a diagnostic HDMI HSM clock rate in a compiled Raspberry Pi DTB."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile


def command(*args: str) -> str:
    return subprocess.check_output(args, text=True).strip()


def find_hdmi_path(dtb: Path) -> str:
    with tempfile.NamedTemporaryFile(suffix=".dts") as temporary:
        subprocess.run(
            [
                "dtc",
                "-q",
                "-I",
                "dtb",
                "-O",
                "dts",
                "-o",
                temporary.name,
                str(dtb),
            ],
            check=True,
        )
        source = Path(temporary.name).read_text()

    stack: list[str] = []
    hdmi_paths: list[str] = []
    node_re = re.compile(r"^\s*([A-Za-z0-9,._+@-]+)\s*\{\s*$")
    for line in source.splitlines():
        match = node_re.match(line)
        if match:
            name = match.group(1)
            if name == "/":
                stack = []
            else:
                stack.append(name)
                if name.startswith("hdmi@"):
                    hdmi_paths.append("/" + "/".join(stack))
            continue
        if line.strip() == "};" and stack:
            stack.pop()

    if len(hdmi_paths) != 1:
        raise RuntimeError(f"expected one HDMI node, found {hdmi_paths!r}")
    return hdmi_paths[0]


def assign_hsm_clock(dtb: Path, rate: int) -> dict[str, object]:
    hdmi_path = find_hdmi_path(dtb)
    names = command(
        "fdtget", "-t", "s", str(dtb), hdmi_path, "clock-names"
    ).split()
    cells = command(
        "fdtget", "-t", "x", str(dtb), hdmi_path, "clocks"
    ).split()
    if len(cells) != len(names) * 2:
        raise RuntimeError(
            f"unexpected HDMI clock layout: names={names!r}, cells={cells!r}"
        )
    try:
        index = names.index("hdmi")
    except ValueError as error:
        raise RuntimeError(f"HDMI HSM clock is absent: {names!r}") from error

    specifier = cells[index * 2:index * 2 + 2]
    subprocess.run(
        [
            "fdtput",
            "-t",
            "x",
            str(dtb),
            hdmi_path,
            "assigned-clocks",
            *specifier,
        ],
        check=True,
    )
    subprocess.run(
        [
            "fdtput",
            "-t",
            "x",
            str(dtb),
            hdmi_path,
            "assigned-clock-rates",
            str(rate),
        ],
        check=True,
    )
    return {
        "hdmi_path": hdmi_path,
        "clock_names": names,
        "clock_cells": cells,
        "assigned_specifier": specifier,
        "assigned_rate": rate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("--rate", type=int, default=250_000_000)
    parser.add_argument("--record", type=Path)
    args = parser.parse_args()

    dtb = args.dtb.resolve()
    if not dtb.is_file():
        parser.error(f"DTB does not exist: {dtb}")
    if args.rate <= 0 or args.rate > 1_000_000_000:
        parser.error(f"invalid HSM clock rate: {args.rate}")

    record = assign_hsm_clock(dtb, args.rate)
    if args.record:
        args.record.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
