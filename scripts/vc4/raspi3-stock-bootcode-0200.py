#!/usr/bin/env python3
"""Run the stock-state probe with the hardware boot entry at 0x200."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

BOOT_ENTRY = 0x200


def load_state_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-state.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_state", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock-state probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    state = load_state_probe()
    original_loader = state.load_probe_module

    def load_probe_module() -> ModuleType:
        probe = original_loader()
        probe.BOOT_ENTRY = BOOT_ENTRY
        return probe

    state.load_probe_module = load_probe_module
    return int(state.main())


if __name__ == "__main__":
    raise SystemExit(main())
