#!/usr/bin/env python3
"""Run the stock bootcode probe with deterministic TCG virtual time."""

from __future__ import annotations

from pathlib import Path

PARSER_OLD = '''    parser.add_argument("--seconds", type=float, default=5.0)\n    parser.add_argument("--barrier-is-success", action="store_true")\n'''
PARSER_NEW = '''    parser.add_argument("--seconds", type=float, default=5.0)\n    parser.add_argument(\n        "--icount-shift",\n        type=int,\n        default=10,\n        help=(\n            "advance virtual time by 2^SHIFT nanoseconds per guest "\n            "instruction; this lets polling delays progress deterministically"\n        ),\n    )\n    parser.add_argument("--barrier-is-success", action="store_true")\n'''
COMMAND_OLD = '''            "-accel", "tcg,thread=single,one-insn-per-tb=on",\n            "-d", "unimp,guest_errors",\n'''
COMMAND_NEW = '''            "-accel", "tcg,thread=single,one-insn-per-tb=on",\n            "-icount",\n            f"shift={args.icount_shift},align=off,sleep=off",\n            "-d", "unimp,guest_errors",\n'''
MARKER = '"--icount-shift"'


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} anchor, found {count}")
    return text.replace(old, new)


def main() -> int:
    path = Path("scripts/vc4/raspi3-stock-bootcode-state.py")
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("Stock bootcode probe already uses deterministic virtual time.")
        return 0

    text = replace_once(text, PARSER_OLD, PARSER_NEW, "argument parser")
    text = replace_once(text, COMMAND_OLD, COMMAND_NEW, "QEMU command")
    path.write_text(text, encoding="utf-8")
    print("Enabled deterministic instruction-counted time for stock bootcode.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
