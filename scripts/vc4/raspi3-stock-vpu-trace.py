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

LOW_PC_LOOP = {
    "0x00000540",
    "0x00000542",
    "0x00000544",
    "0x00000546",
    "0x0000054a",
}
CORE_DEBUG_PROPERTIES = (
    "debug-halted",
    "debug-stop",
    "debug-stopped",
    "debug-exit-request",
    "debug-thread-kicked",
    "debug-hard-interrupt",
    "debug-has-work",
)
IRQ_DEBUG_PROPERTIES = (
    "debug-vc4-pending",
    "debug-basic-pending",
    "debug-basic-enable",
    "debug-gpu-pending",
    "debug-gpu-enable",
    "debug-direct-enable",
    "debug-direct-pending",
    "debug-cpu-irq",
    "debug-fiq-asserted",
    "debug-fiq-control",
    "debug-fiq-gpu",
    "debug-fiq-direct",
    "debug-irq-enabled-pending",
    "debug-irq-any-pending",
)

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


def read_qom_debug(
    qmp: Any,
    qom_path: str | None,
    *,
    include_irq: bool,
) -> dict[str, Any]:
    if not qom_path:
        return {"_error": "query-cpus-fast did not expose the VPU qom-path"}

    properties = CORE_DEBUG_PROPERTIES + (IRQ_DEBUG_PROPERTIES if include_irq else ())
    values: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for name in properties:
        try:
            values[name] = qmp.execute(
                "qom-get",
                {"path": qom_path, "property": name},
            )
        except Exception as error:
            errors[name] = f"{type(error).__name__}: {error}"
    if errors:
        values["_errors"] = errors
    return values


def register_value(sample: dict[str, Any], name: str) -> str | None:
    registers = sample.get("registers") or {}
    value = registers.get(name)
    if value is None and len(name) == 2 and name.startswith("r"):
        value = registers.get(f"r0{name[1]}")
    return value


def fingerprint(sample: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    registers = sample.get("registers") or {}
    return tuple(sorted((str(key), str(value)) for key, value in registers.items()))


def summarize_low_pc_phase(
    timeline: list[dict[str, Any]],
    first_index: int | None,
) -> dict[str, Any]:
    if first_index is None:
        return {
            "entered": False,
            "first_sample_index": None,
            "sample_count": 0,
            "unique_state_count": 0,
            "transition_count": 0,
            "unique_pcs": [],
            "pc_counts": {},
            "r2_values": [],
            "r3_values": [],
            "timer_register_changed": False,
            "system_timer_delta_us": 0,
            "stationary": False,
        }

    phase = timeline[first_index:]
    states = [fingerprint(sample) for sample in phase]
    transitions = sum(
        current != previous
        for previous, current in zip(states, states[1:])
    )
    counts = Counter(str(sample.get("pc", "unknown")) for sample in phase)
    r2_values = sorted({
        value for sample in phase
        if (value := register_value(sample, "r2")) is not None
    })
    r3_values = sorted({
        value for sample in phase
        if (value := register_value(sample, "r3")) is not None
    })
    first_timer = int(str(phase[0]["system_timer_clo"]), 16)
    last_timer = int(str(phase[-1]["system_timer_clo"]), 16)
    unique_state_count = len(set(states))
    return {
        "entered": True,
        "first_sample_index": first_index,
        "first_elapsed_seconds": phase[0]["elapsed_seconds"],
        "sample_count": len(phase),
        "unique_state_count": unique_state_count,
        "transition_count": transitions,
        "unique_pcs": sorted(counts),
        "pc_counts": dict(counts.most_common()),
        "r2_values": r2_values,
        "r3_values": r3_values,
        "timer_register_changed": len(r2_values) > 1,
        "system_timer_delta_us": (last_timer - first_timer) & 0xffffffff,
        "stationary": len(phase) >= 10 and unique_state_count == 1,
    }


def classify_hint(
    *,
    signature_seen: bool,
    low_pc_phase: dict[str, Any],
    pre_stop_debug: dict[str, Any],
) -> str:
    if signature_seen:
        return "stock-firmware-aarch64-handoff-clear"
    if not low_pc_phase.get("entered"):
        return "stock-vpu-did-not-enter-delay-loop"
    if not low_pc_phase.get("stationary"):
        return "stock-vpu-delay-loop-state-progressing"

    stopped = any(
        pre_stop_debug.get(name) is True
        for name in ("debug-halted", "debug-stop", "debug-stopped")
    )
    if stopped:
        return "stock-vpu-delay-loop-cpu-stopped"
    return "stock-vpu-delay-loop-runnable-state-stationary"


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
    parser.add_argument("--qom-interval", type=float, default=0.5)
    parser.add_argument(
        "--one-insn-per-tb",
        action="store_true",
        help="run TCG with one guest instruction in each translation block",
    )
    args = parser.parse_args()

    if args.seconds <= 0 or args.interval <= 0 or args.qom_interval <= 0:
        parser.error("--seconds, --interval, and --qom-interval must be positive")
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
        accel = "tcg,thread=single"
        if args.one_insn_per_tb:
            accel += ",one-insn-per-tb=on"
        command = [
            str(args.qemu.resolve()),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", accel,
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
            "schema_version": 2,
            "source_sha": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "seconds_requested": args.seconds,
            "sample_interval_requested": args.interval,
            "qom_interval_requested": args.qom_interval,
            "one_insn_per_tb": args.one_insn_per_tb,
            "qemu_command": command,
            "fat_layout": {name: list(chain) for name, chain in layout.items()},
        }
        timeline: list[dict[str, Any]] = []
        unique_states: dict[tuple[tuple[str, str], ...], dict[str, Any]] = {}
        transitions: list[dict[str, Any]] = []
        pc_counts: Counter[str] = Counter()
        qom_timeline: list[dict[str, Any]] = []
        last_fingerprint: tuple[tuple[str, str], ...] | None = None
        started = time.monotonic()
        initial_timer = 0
        first_low_pc_index: int | None = None
        next_qom_sample = started
        vpu_qom_path: str | None = None
        pre_stop_debug: dict[str, Any] = {}

        try:
            qmp = handoff.wait_for_qmp(qmp_path, process, 15.0)
            cpus = qmp.execute("query-cpus-fast")
            vpu = next(
                item
                for item in cpus
                if "vc4" in str(item.get("qom-type", "")).lower()
            )
            vpu_index = int(vpu["cpu-index"])
            qom_path_value = vpu.get("qom-path")
            if isinstance(qom_path_value, str):
                vpu_qom_path = qom_path_value
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
                current_fingerprint = tuple(sorted(selected.items()))
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

                entered_now = pc in LOW_PC_LOOP and first_low_pc_index is None
                if entered_now:
                    first_low_pc_index = len(timeline) - 1

                if now >= next_qom_sample or entered_now:
                    qom_timeline.append({
                        "sample_index": len(timeline) - 1,
                        "elapsed_seconds": now - started,
                        "pc": pc,
                        "include_irq": entered_now,
                        "properties": read_qom_debug(
                            qmp,
                            vpu_qom_path,
                            include_irq=entered_now,
                        ),
                    })
                    next_qom_sample = now + args.qom_interval

                state = unique_states.get(current_fingerprint)
                if state is None:
                    unique_states[current_fingerprint] = {
                        "first_sample": len(timeline) - 1,
                        "last_sample": len(timeline) - 1,
                        "count": 1,
                        "sample": sample,
                        "raw_registers": raw,
                    }
                else:
                    state["last_sample"] = len(timeline) - 1
                    state["count"] += 1

                if current_fingerprint != last_fingerprint:
                    transitions.append({
                        "sample_index": len(timeline) - 1,
                        "elapsed_seconds": now - started,
                        "pc": pc,
                        "registers": selected,
                    })
                    last_fingerprint = current_fingerprint

                if signature == SIGNATURE or process.poll() is not None:
                    break
                remaining = args.interval - (time.monotonic() - now)
                if remaining > 0:
                    time.sleep(remaining)

            pre_stop_debug = read_qom_debug(
                qmp,
                vpu_qom_path,
                include_irq=True,
            )
            try:
                qmp.execute("stop")
            except Exception:
                pass

            final_timer = qmp.readl(SYSTEM_TIMER_CLO)
            low_pc_phase = summarize_low_pc_phase(timeline, first_low_pc_index)
            signature_seen = any(
                sample["signature"] == f"0x{SIGNATURE:016x}"
                for sample in timeline
            )
            result.update({
                "vpu_cpu_index": vpu_index,
                "vpu_qom_path": vpu_qom_path,
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
                "signature_seen": signature_seen,
                "arm_control0": f"0x{qmp.readl(ARM_CONTROL0):08x}",
                "arm_control1": f"0x{qmp.readl(ARM_CONTROL1):08x}",
                "arm_status": f"0x{qmp.readl(ARM_STATUS):08x}",
                "arm_id": f"0x{qmp.readl(ARM_ID):08x}",
                "pm_proc": f"0x{qmp.readl(PM_PROC):08x}",
                "low_pc_phase": low_pc_phase,
                "pre_stop_debug": pre_stop_debug,
                "classification_hint": classify_hint(
                    signature_seen=signature_seen,
                    low_pc_phase=low_pc_phase,
                    pre_stop_debug=pre_stop_debug,
                ),
                "cpu_snapshot": handoff.cpu_snapshot(qmp),
                "timeline": timeline,
                "qom_timeline": qom_timeline,
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
                "one_insn_per_tb", "low_pc_phase", "pre_stop_debug",
                "classification_hint",
            )
        }, indent=2, sort_keys=True))

    return 0 if "probe_error" not in result else 2


if __name__ == "__main__":
    raise SystemExit(main())
