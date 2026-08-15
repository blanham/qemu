#!/usr/bin/env python3
"""Require repeated ARM-to-VC4 RR handoffs without qtest polling traffic."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from types import ModuleType


def load_module(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument(
        "--delay-us",
        type=int,
        default=1_000_000,
        help="VC4 delay; values above the 100 ms RR quantum require a return handoff",
    )
    parser.add_argument(
        "--quiescent-seconds",
        type=float,
        default=8.0,
        help="host interval with no qtest requests after connection",
    )
    args = parser.parse_args()

    if args.delay_us <= 100_000:
        parser.error("--delay-us must exceed the 100 ms RR kick period")
    if args.quiescent_seconds <= 0:
        parser.error("--quiescent-seconds must be positive")

    passive = load_module(
        "raspi3-rr-passive-smoke.py", "vc4_rr_passive_base"
    )
    original_load_module = passive.load_module

    def load_with_long_delay(filename: str, name: str) -> ModuleType:
        module = original_load_module(filename, name)
        if filename == "raspi3-systimer-delay-smoke.py":
            module.DELAY_US = args.delay_us
        return module

    passive.load_module = load_with_long_delay

    old_argv = sys.argv
    try:
        sys.argv = [
            str(Path(passive.__file__)),
            args.qemu,
            "--quiescent-seconds",
            str(args.quiescent_seconds),
        ]
        return int(passive.main())
    finally:
        sys.argv = old_argv


if __name__ == "__main__":
    raise SystemExit(main())
