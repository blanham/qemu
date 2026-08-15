#!/usr/bin/env python3
"""Compact the VC4 failure history so it survives the probe's 48-line tail."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

SOURCE = Path("target/vc4/op_helper.c")


def block_end(text: str, start: int) -> int:
    depth = 0
    opened = False
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                end = index + 1
                if end < len(text) and text[end] == "\n":
                    end += 1
                return end
    raise SystemExit("unterminated C block while compacting failure history")


def main() -> None:
    subprocess.run(
        [sys.executable, "scripts/vc4/instrument-failure-history.py"],
        check=True,
    )

    text = SOURCE.read_text(encoding="utf-8")
    replacements = {
        "#define VC4_TRANSFER_HISTORY_CAPACITY 256":
            "#define VC4_TRANSFER_HISTORY_CAPACITY 32",
        "#define VC4_R0_HISTORY_CAPACITY       256":
            "#define VC4_R0_HISTORY_CAPACITY       12",
    }
    for old, new in replacements.items():
        if text.count(old) != 1:
            raise SystemExit(f"unexpected compact-history anchor: {old!r}")
        text = text.replace(old, new, 1)

    transfer_marker = (
        "    for (i = 0; i < vc4_transfer_history_count; i++) {\n"
    )
    r0_marker = "    for (i = 0; i < vc4_r0_history_count; i++) {\n"
    transfer_start = text.index(transfer_marker)
    transfer_end = block_end(text, transfer_start)
    r0_start = text.index(r0_marker, transfer_end)
    r0_end = block_end(text, r0_start)

    transfer_block = text[transfer_start:transfer_end]
    r0_block = text[r0_start:r0_end]
    between = text[transfer_end:r0_start]
    if between.strip():
        raise SystemExit("unexpected code between transfer and r0 history loops")

    text = (
        text[:transfer_start]
        + r0_block
        + "\n"
        + transfer_block
        + text[r0_end:]
    )
    SOURCE.write_text(text, encoding="utf-8")
    print("compacted VC4 failure history to the probe's 48-line window")


if __name__ == "__main__":
    main()
