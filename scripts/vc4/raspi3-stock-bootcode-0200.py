#!/usr/bin/env python3
"""Run the stock-state probe with the hardware boot entry at 0x200."""

from __future__ import annotations

from collections import Counter
import importlib.util
from pathlib import Path
import re
import time
from types import ModuleType
from typing import Any

BOOT_ENTRY = 0x200
TRACE_SAMPLES = 12
TRACE_INTERVAL = 0.05
REG_RE = re.compile(r"\br(\d+)\s*=\s*([0-9a-fA-F]{1,8})\b")


def load_state_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-state.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_state", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock-state probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_snapshot(state: ModuleType, text: str) -> dict[str, Any]:
    pc_match = state.PC_RE.search(text)
    sr_match = state.SR_RE.search(text)
    registers = {
        int(match.group(1)): int(match.group(2), 16)
        for match in REG_RE.finditer(text)
    }
    return {
        "pc": int(pc_match.group(1), 16) if pc_match else -1,
        "sr": int(sr_match.group(1), 16) if sr_match else 0,
        "registers": registers,
    }


def format_snapshot(index: int, snapshot: dict[str, Any]) -> str:
    registers = snapshot["registers"]
    fields = [
        f"{index}:pc=0x{snapshot['pc']:08x}",
        f"sr=0x{snapshot['sr']:08x}",
    ]
    for reg in (0, 1, 2, 3, 4, 5, 26):
        if reg in registers:
            fields.append(f"r{reg}=0x{registers[reg]:08x}")
    return "/".join(fields)


def compact_transitions(values: list[int]) -> list[int]:
    result: list[int] = []
    for value in values:
        if not result or result[-1] != value:
            result.append(value)
    return result


def install_trace_qmp(state: ModuleType) -> None:
    base_qmp = state.QMP

    class TracedQMP(base_qmp):  # type: ignore[misc, valid-type]
        def __init__(self, path: Path) -> None:
            super().__init__(path)
            self._stock_trace_complete = False

        def _set_running(self, running: bool) -> None:
            status = super().execute("query-status")
            current = bool(status.get("running", False)) if isinstance(
                status, dict
            ) else False
            if current != running:
                super().execute("cont" if running else "stop")

            for _ in range(100):
                status = super().execute("query-status")
                current = bool(status.get("running", False)) if isinstance(
                    status, dict
                ) else False
                if current == running:
                    return
                time.sleep(0.005)
            raise TimeoutError(
                f"QEMU did not enter {'running' if running else 'stopped'} state"
            )

        def _capture_stock_trace(self) -> None:
            cpus = super().execute("query-cpus-fast")
            vpu_index, _ = state.find_vc4_cpu(cpus)
            snapshots: list[dict[str, Any]] = []

            for index in range(TRACE_SAMPLES):
                self._set_running(False)
                registers = self.hmp("info registers", cpu_index=vpu_index)
                snapshots.append(parse_snapshot(state, registers))
                if index + 1 != TRACE_SAMPLES:
                    self._set_running(True)
                    time.sleep(TRACE_INTERVAL)

            pcs = [snapshot["pc"] for snapshot in snapshots]
            histogram = Counter(pcs)
            transitions = compact_transitions(pcs)
            stable = len(histogram) == 1
            final_registers = snapshots[-1]["registers"]

            context_parts: list[str] = []
            for pc in dict.fromkeys(pcs):
                if pc < 0:
                    continue
                context = state.flatten(
                    self.hmp(f"x /16bx 0x{pc:x}", cpu_index=vpu_index)
                )
                context_parts.append(f"0x{pc:08x}={context}")
                if len(context_parts) == 8:
                    break

            return_parts: list[str] = []
            for snapshot in snapshots:
                return_pc = snapshot["registers"].get(26)
                if return_pc is None or any(
                    part.startswith(f"0x{return_pc:08x}=")
                    for part in return_parts
                ):
                    continue
                context = state.flatten(
                    self.hmp(
                        f"x /16bx 0x{return_pc:x}",
                        cpu_index=vpu_index,
                    )
                )
                return_parts.append(f"0x{return_pc:08x}={context}")
                if len(return_parts) == 4:
                    break

            mmio_parts: list[str] = []
            for reg, value in sorted(final_registers.items()):
                if not 0x7E000000 <= value < 0x7F000000:
                    continue
                dump = state.flatten(
                    self.hmp(f"x /4wx 0x{value:x}", cpu_index=vpu_index)
                )
                mmio_parts.append(f"r{reg}=0x{value:08x}:{dump}")
                if len(mmio_parts) == 4:
                    break

            print(
                "STOCK_BOOTCODE_TRACE "
                f"samples={len(snapshots)} interval={TRACE_INTERVAL:.3f}s "
                f"stable={str(stable).lower()} "
                "histogram="
                + ",".join(
                    f"0x{pc:08x}:{count}"
                    for pc, count in sorted(histogram.items())
                )
                + " transitions="
                + "->".join(f"0x{pc:08x}" for pc in transitions)
                + " snapshots="
                + ";".join(
                    format_snapshot(index, snapshot)
                    for index, snapshot in enumerate(snapshots)
                )
                + " pc-contexts="
                + (";".join(context_parts) or "none")
                + " return-contexts="
                + (";".join(return_parts) or "none")
                + " mmio="
                + (";".join(mmio_parts) or "none")
            )

        def execute(self, command: str,
                    arguments: dict[str, Any] | None = None) -> Any:
            if command == "stop" and not self._stock_trace_complete:
                self._stock_trace_complete = True
                self._capture_stock_trace()
                return None
            return super().execute(command, arguments)

    state.QMP = TracedQMP


def main() -> int:
    state = load_state_probe()
    original_loader = state.load_probe_module

    def load_probe_module() -> ModuleType:
        probe = original_loader()
        probe.BOOT_ENTRY = BOOT_ENTRY
        return probe

    state.load_probe_module = load_probe_module
    install_trace_qmp(state)
    return int(state.main())


if __name__ == "__main__":
    raise SystemExit(main())
