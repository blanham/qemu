#!/usr/bin/env python3
"""Measure temporal VC4 state at the stock-firmware ARM-handoff frontier."""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from types import ModuleType
from typing import Any

SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21
SYSTEM_TIMER_CLO = 0x3F003004
ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110

REG_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*([0-9A-Fa-f]+)")
SELECTED_REGS = {
    "pc", "sp", "lr", "sr", "r0", "r00", "r1", "r01", "r2", "r02",
    "r3", "r03", "r23", "r24", "r25", "r26", "r27", "r28", "r29",
    "r30", "r31",
}


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_registers(text: str) -> dict[str, str]:
    registers: dict[str, str] = {}
    for name, value in REG_RE.findall(text):
        registers[name.lower()] = f"0x{int(value, 16):08x}"
    return registers


def extract_asm_context(log_text: str, target: int = 0x544) -> list[str]:
    lines = log_text.splitlines()
    pattern = re.compile(rf"(?:^|\s)0x0*{target:x}(?::|\s)", re.IGNORECASE)
    selected: list[str] = []
    seen: set[tuple[int, int]] = set()
    for index, line in enumerate(lines):
        if not pattern.search(line):
            continue
        start = max(0, index - 12)
        end = min(len(lines), index + 28)
        key = (start, end)
        if key in seen:
            continue
        seen.add(key)
        selected.append(f"--- lines {start + 1}-{end} ---")
        selected.extend(lines[start:end])
        if len(selected) >= 240:
            break
    return selected[:240]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("kernel8", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--interval", type=float, default=0.05)
    args = parser.parse_args()

    if args.seconds <= 0 or args.interval <= 0:
        parser.error("--seconds and --interval must be positive")
    for path in (
        args.qemu, args.bootcode, args.start_elf,
        args.fixup_dat, args.dtb, args.kernel8,
    ):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / "stock-vpu-trace.img"
    qemu_log_path = out_dir / "qemu-in-asm.log"
    stderr_path = out_dir / "qemu.stderr"
    result_path = out_dir / "VC4_STOCK_VPU_TRACE.json"

    handoff = load_module(
        Path(__file__).with_name("raspi3-stock-arm-payload.py"),
        "vc4_stock_handoff_probe",
    )
    stock = handoff.load_stock_probe()
    files = [
        ("BOOTCODE.BIN", args.bootcode.read_bytes()),
        ("START.ELF", args.start_elf.read_bytes()),
        ("FIXUP.DAT", args.fixup_dat.read_bytes()),
        ("CONFIG.TXT", handoff.default_config(True)),
        ("CMDLINE.TXT", b"console=serial0,115200 earlycon=pl011,0x3f201000\n"),
        ("RPI3.DTB", args.dtb.read_bytes()),
        ("KERNEL8.IMG", args.kernel8.read_bytes()),
    ]
    layout = stock.build_fat32_image(image_path, files)

    with tempfile.TemporaryDirectory(prefix="vc4-stock-vpu-trace-") as temp_s:
        temp = Path(temp_s)
        qmp_path = temp / "qmp.sock"
        command = [
            str(args.qemu.resolve()),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-no-reboot",
            "-d", "in_asm,guest_errors,unimp",
            "-D", str(qemu_log_path),
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
        ]
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp = None
        result: dict[str, Any] = {
            "schema_version": 1,
            "source_sha": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "seconds_requested": args.seconds,
            "sample_interval_requested": args.interval,
            "qemu_command": command,
            "fat_layout": {name: list(chain) for name, chain in layout.items()},
        }
        timeline: list[dict[str, Any]] = []
        unique_states: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        transitions: list[dict[str, Any]] = []
        pc_counts: Counter[str] = Counter()
        last_fingerprint: tuple[tuple[str, str], ...] | None = None
        started = time.monotonic()
        initial_timer = 0

        try:
            qmp = handoff.wait_for_qmp(qmp_path, process, 15.0)
            cpus = qmp.execute("query-cpus-fast")
            vpu_index = next(
                item["cpu-index"]
                for item in cpus
                if "vc4" in str(item.get("qom-type", "")).lower()
            )
            initial_timer = qmp.readl(SYSTEM_TIMER_CLO)
            deadline = started + args.seconds

            while time.monotonic() < deadline:
                now = time.monotonic()
                raw = qmp.hmp("info registers", cpu_index=vpu_index)
                registers = parse_registers(raw)
                pc = registers.get("pc", "unknown")
                timer = qmp.readl(SYSTEM_TIMER_CLO)
                signature = qmp.readq(SIGNATURE_ADDR)
                selected = {
                    name: value for name, value in registers.items()
                    if name in SELECTED_REGS
                }
                fingerprint = tuple(sorted(selected.items()))
                sample = {
                    "elapsed_seconds": now - started,
                    "system_timer_clo": f"0x{timer:08x}",
                    "system_timer_delta_us": (timer - initial_timer) & 0xffffffff,
                    "signature": f"0x{signature:016x}",
                    "pc": pc,
                    "registers": selected,
                }
                timeline.append(sample)
                pc_counts[pc] += 1

                state = unique_states.get(fingerprint)
                if state is None:
                    unique_states[fingerprint] = {
                        "first_sample": len(timeline) - 1,
                        "last_sample": len(timeline) - 1,
                        "count": 1,
                        "sample": sample,
                        "raw_registers": raw,
                    }
                else:
                    state["last_sample"] = len(timeline) - 1
                    state["count"] += 1

                if fingerprint != last_fingerprint:
                    transitions.append({
                        "sample_index": len(timeline) - 1,
                        "elapsed_seconds": now - started,
                        "pc": pc,
                        "registers": selected,
                    })
                    last_fingerprint = fingerprint

                if signature == SIGNATURE or process.poll() is not None:
                    break
                remaining = args.interval - (time.monotonic() - now)
                if remaining > 0:
                    time.sleep(remaining)

            try:
                qmp.execute("stop")
            except Exception:
                pass

            final_timer = qmp.readl(SYSTEM_TIMER_CLO)
            result.update({
                "vpu_cpu_index": vpu_index,
                "elapsed_seconds": time.monotonic() - started,
                "sample_count": len(timeline),
                "unique_state_count": len(unique_states),
                "transition_count": len(transitions),
                "unique_pcs": sorted(pc_counts),
                "pc_counts": dict(pc_counts.most_common()),
                "pc_0x00000544_fraction": (
                    pc_counts.get("0x00000544", 0) / len(timeline)
                    if timeline else 0.0
                ),
                "system_timer_clo_before": f"0x{initial_timer:08x}",
                "system_timer_clo_after": f"0x{final_timer:08x}",
                "system_timer_delta_us": (final_timer - initial_timer) & 0xffffffff,
                "signature_seen": any(
                    sample["signature"] == f"0x{SIGNATURE:016x}"
                    for sample in timeline
                ),
                "arm_control0": f"0x{qmp.readl(ARM_CONTROL0):08x}",
                "arm_control1": f"0x{qmp.readl(ARM_CONTROL1):08x}",
                "arm_status": f"0x{qmp.readl(ARM_STATUS):08x}",
                "arm_id": f"0x{qmp.readl(ARM_ID):08x}",
                "pm_proc": f"0x{qmp.readl(PM_PROC):08x}",
                "cpu_snapshot": handoff.cpu_snapshot(qmp),
                "timeline": timeline,
                "transitions": transitions,
                "unique_states": list(unique_states.values()),
            })
        except Exception as error:
            result["probe_error"] = f"{type(error).__name__}: {error}"
        finally:
            if qmp is not None:
                try:
                    if process.poll() is None:
                        qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            handoff.stop_process(process)

        qemu_log = (
            qemu_log_path.read_text(encoding="utf-8", errors="replace")
            if qemu_log_path.is_file() else ""
        )
        result["asm_context_0x544"] = extract_asm_context(qemu_log)
        result["qemu_log_size"] = len(qemu_log.encode("utf-8"))
        result["qemu_diagnostics_tail"] = handoff.diagnostics_tail(
            stderr_path.read_text(encoding="utf-8", errors="replace"), 128
        )
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            key: result.get(key)
            for key in (
                "probe_error", "elapsed_seconds", "sample_count",
                "unique_state_count", "transition_count", "unique_pcs",
                "pc_counts", "pc_0x00000544_fraction",
                "system_timer_delta_us", "signature_seen",
                "arm_control0", "arm_control1", "arm_status", "pm_proc",
            )
        }, indent=2, sort_keys=True))

    return 0 if "probe_error" not in result else 2


if __name__ == "__main__":
    raise SystemExit(main())
