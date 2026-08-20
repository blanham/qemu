#!/usr/bin/env python3
"""Add an optional raw-QEMU-log export to the stock-state probe.

The stock-state probe normally keeps QEMU stderr in a temporary directory and
prints only a bounded, flattened diagnostic tail.  Retry-flow analysis needs
the complete ordered trace, so this workflow-only transformer adds a
``--qemu-log`` output path and copies stderr there after QEMU has stopped.

The generated probe change must never be committed.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "scripts/vc4/raspi3-stock-bootcode-state.py"
MARKER = "VC4_RAW_QEMU_LOG_EXPORT"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label}: found {count} anchors")
    return text.replace(old, new, 1)


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("raw QEMU log export is already materialized")
        return 0

    parser_anchor = (
        '    parser.add_argument("--barrier-is-success", action="store_true")\n'
    )
    parser_replacement = parser_anchor + '''    # VC4_RAW_QEMU_LOG_EXPORT: workflow-only complete trace output.
    parser.add_argument(
        "--qemu-log",
        type=Path,
        help="copy complete QEMU stderr to this path before cleanup",
    )
'''
    text = replace_once(
        text,
        parser_anchor,
        parser_replacement,
        "stock-state parser option",
    )

    cleanup_anchor = "            stop_process(proc)\n"
    cleanup_replacement = cleanup_anchor + '''            if args.qemu_log is not None:
                qemu_log_path = args.qemu_log.expanduser().resolve()
                qemu_log_path.parent.mkdir(parents=True, exist_ok=True)
                qemu_log_path.write_bytes(stderr_path.read_bytes())
                print(
                    "STOCK_BOOTCODE_QEMU_LOG "
                    f"path={qemu_log_path} bytes={qemu_log_path.stat().st_size}"
                )
'''
    text = replace_once(
        text,
        cleanup_anchor,
        cleanup_replacement,
        "stock-state cleanup",
    )

    PATH.write_text(text, encoding="utf-8")
    print("materialized raw QEMU log export in stock-state probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
