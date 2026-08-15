#!/usr/bin/env python3
"""Run the stock VideoCore boot probe with ordered DWC2 MMIO tracing."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import shutil
import sys
from types import ModuleType
from typing import Any


TRACE_EVENTS = (
    "usb_dwc2_glbreg_read",
    "usb_dwc2_glbreg_write",
    "usb_dwc2_hreg0_read",
    "usb_dwc2_hreg0_write",
    "usb_dwc2_pcgreg_read",
    "usb_dwc2_pcgreg_write",
)


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PersistentTemporaryDirectory:
    """A TemporaryDirectory-shaped context manager that preserves its files."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __enter__(self) -> str:
        shutil.rmtree(self.path, ignore_errors=True)
        self.path.mkdir(parents=True)
        return str(self.path)

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> bool:
        return False


class TempfileProxy:
    def __init__(self, module: ModuleType, run_dir: Path) -> None:
        self._module = module
        self._run_dir = run_dir

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    def TemporaryDirectory(self, *args: Any, **kwargs: Any) -> PersistentTemporaryDirectory:
        return PersistentTemporaryDirectory(self._run_dir)


class SubprocessProxy:
    def __init__(self, module: ModuleType, events_path: Path) -> None:
        self._module = module
        self._events_path = events_path

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)

    def Popen(self, command: Any, *args: Any, **kwargs: Any) -> Any:
        traced_command = list(command)
        traced_command.extend(["-trace", f"events={self._events_path}"])
        return self._module.Popen(traced_command, *args, **kwargs)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("--start-elf", type=Path, required=True)
    parser.add_argument("--fixup-dat", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--icount-shift", type=int, default=10)
    parser.add_argument("--one-insn-per-tb", action="store_true")
    parser.add_argument("--deterministic", action="store_true")
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    bootcode = args.bootcode.resolve()
    start_elf = args.start_elf.resolve()
    fixup_dat = args.fixup_dat.resolve()
    for path in (qemu, bootcode, start_elf, fixup_dat):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    run_dir = out_dir / "run"
    events_path = out_dir / "dwc2-events"
    raw_log = out_dir / "qemu-mmio.log"
    events_path.write_text("\n".join(TRACE_EVENTS) + "\n")
    raw_log.unlink(missing_ok=True)

    script_dir = Path(__file__).resolve().parent
    boot_probe = load_module(
        script_dir / "raspi3-stock-bootcode-0200.py",
        "vc4_stock_bootcode_0200_mmio",
    )
    original_load_state_probe = boot_probe.load_state_probe
    loaded_states: list[ModuleType] = []

    def load_state_probe() -> ModuleType:
        state = original_load_state_probe()
        state.tempfile = TempfileProxy(state.tempfile, run_dir)
        state.subprocess = SubprocessProxy(state.subprocess, events_path)
        loaded_states.append(state)
        return state

    boot_probe.load_state_probe = load_state_probe

    probe_argv = [
        str(script_dir / "raspi3-stock-bootcode-0200.py"),
        str(qemu),
        str(bootcode),
        "--start-elf", str(start_elf),
        "--fixup-dat", str(fixup_dat),
        "--seconds", str(args.seconds),
        "--icount-shift", str(args.icount_shift),
        "--barrier-is-success",
    ]
    if args.one_insn_per_tb:
        probe_argv.append("--one-insn-per-tb")
    if not args.deterministic:
        probe_argv.append("--fast-tcg")

    old_argv = sys.argv
    status = 1
    try:
        sys.argv = probe_argv
        status = int(boot_probe.main())
    finally:
        sys.argv = old_argv
        stderr_path = run_dir / "qemu.stderr"
        if stderr_path.is_file():
            shutil.copyfile(stderr_path, raw_log)

    if not loaded_states:
        raise RuntimeError("stock-state probe was never loaded")
    if not raw_log.is_file():
        raise RuntimeError(f"stock-state probe did not produce {raw_log}")

    print(f"VC4_MMIO_TRACE events={events_path}")
    print(f"VC4_MMIO_TRACE stderr={raw_log}")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
