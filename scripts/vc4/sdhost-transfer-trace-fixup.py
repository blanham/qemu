#!/usr/bin/env python3
"""Add bounded workflow-only tracing to the BCM2835 SDHOST model.

The trace records raw SDCMD writes with their argument and samples SDDATA FIFO
access.  It changes no guest-visible state and is never committed by the
companion workflow.  Its purpose is to distinguish FAT/command setup failures
from bulk-transfer or later relocation failures at the start.elf frontier.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "hw/sd/bcm2835_sdhost.c"
MARKER = "VC4_SDHOST_TRANSFER_TRACE"


def insert_case_trace(
    text: str,
    case_names: tuple[str, ...],
    body: str,
    what: str,
) -> str:
    alternatives = "|".join(re.escape(name) for name in case_names)
    match = re.search(
        rf"^(?P<indent>[ \t]*)case (?P<name>{alternatives}):[ \t]*$",
        text,
        re.MULTILINE,
    )
    if match is None:
        raise SystemExit(f"could not locate {what} case")
    line_end = text.find("\n", match.end())
    if line_end < 0:
        raise SystemExit(f"could not delimit {what} case")
    indentation = match.group("indent") + "    "
    rendered = "\n".join(
        indentation + line if line else "" for line in body.splitlines()
    )
    return text[: line_end + 1] + rendered + "\n" + text[line_end + 1 :]


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("SDHOST transfer trace is already materialized.")
        return 0

    include_end = text.find("\n\n", text.find("#include"))
    if include_end < 0:
        raise SystemExit("could not locate SDHOST include boundary")
    declarations = """

/* VC4_SDHOST_TRANSFER_TRACE: bounded workflow-only diagnostics. */
static unsigned vc4_sdhost_command_count;
static unsigned vc4_sdhost_data_read_count;
static unsigned vc4_sdhost_data_write_count;
"""
    text = text[:include_end] + declarations + text[include_end:]

    text = insert_case_trace(
        text,
        ("SDCMD", "SD_CMD"),
        """if (vc4_sdhost_command_count < 1024) {
    fprintf(stderr,
            "VC4_SDHOST_CMD seq=%u value=0x%08" PRIx64
            " arg=0x%08x\\n",
            vc4_sdhost_command_count, value, s->sdarg);
}
vc4_sdhost_command_count++;""",
        "SDCMD write",
    )

    # The first SDDATA case belongs to the read handler.  Re-run the search on
    # the remaining text to locate the write handler independently.
    data_pattern = re.compile(
        r"^(?P<indent>[ \t]*)case (?P<name>SDDATA|SD_DATA):[ \t]*$",
        re.MULTILINE,
    )
    matches = list(data_pattern.finditer(text))
    if len(matches) < 2:
        raise SystemExit("could not locate separate SDDATA read/write cases")

    def inject_at(match: re.Match[str], body: str, source: str) -> str:
        line_end = source.find("\n", match.end())
        indent = match.group("indent") + "    "
        rendered = "\n".join(indent + line for line in body.splitlines())
        return source[: line_end + 1] + rendered + "\n" + source[line_end + 1 :]

    read_match = matches[0]
    read_body = """if (vc4_sdhost_data_read_count < 128 ||
        !(vc4_sdhost_data_read_count & 0x3ff)) {
    fprintf(stderr, "VC4_SDHOST_DATA_READ seq=%u\\n",
            vc4_sdhost_data_read_count);
}
vc4_sdhost_data_read_count++;"""
    text = inject_at(read_match, read_body, text)

    # Recompute after insertion so the second match has the correct offset.
    matches = list(data_pattern.finditer(text))
    write_match = matches[1]
    write_body = """if (vc4_sdhost_data_write_count < 128 ||
        !(vc4_sdhost_data_write_count & 0x3ff)) {
    fprintf(stderr,
            "VC4_SDHOST_DATA_WRITE seq=%u value=0x%08" PRIx64 "\\n",
            vc4_sdhost_data_write_count, value);
}
vc4_sdhost_data_write_count++;"""
    text = inject_at(write_match, write_body, text)

    PATH.write_text(text, encoding="utf-8")
    print("Materialized bounded BCM2835 SDHOST transfer diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
