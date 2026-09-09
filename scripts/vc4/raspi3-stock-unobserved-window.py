#!/usr/bin/env python3
"""Run stock Raspberry Pi firmware without observer interference, then inspect it.

Unlike the temporal sampler, this control deliberately does not connect to QMP
until the requested quiet window has elapsed.  It therefore answers one narrow
question: can unchanged stock firmware leave the measured low-PC delay phase
when ordinary single-thread TCG is allowed to run without repeated stop/query
cycles?
"""

from __future__ import annotations

import argparse
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
REG_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*=\s*([0-9A-Fa-f]+)")


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_registers(text: str) -> dict[str, str]:
    return {
        name.lower(): f"0x{int(value, 16):08x}"
        for name, value in REG_RE.findall(text)
    }


def classify(
    *,
    signature_seen: bool,
    vpu_pc: str | None,
    a53_pcs: list[str],
) -> str:
    if signature_seen:
        return "stock-firmware-aarch64-handoff-after-unobserved-window"
    if any(int(pc, 16) != 0 for pc in a53_pcs):
        return "stock-firmware-a53-active-without-signature"
    if vpu_pc in LOW_PC_LOOP:
        return "stock-vpu-low-pc-persists-without-observer"
    if vpu_pc is None:
        return "stock-vpu-post-window-registers-unavailable"
    return "stock-vpu-progressed-beyond-low-pc-without-arm-handoff"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("kernel8", type=Path)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--quiet-seconds", type=float, default=30.0)
    args = parser.parse_args()

    if args.quiet_seconds <= 0:
        parser.error("--quiet-seconds must be positive")
    for path in (
        args.qemu,
        args.bootcode,
        args.start_elf,
        args.fixup_dat,
        args.dtb,
        args.kernel8,
    ):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    image_path = out_dir / "stock-unobserved-window.img"
    stderr_path = out_dir / "qemu.stderr"
    result_path = out_dir / "VC4_STOCK_UNOBSERVED_WINDOW.json"

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

    with tempfile.TemporaryDirectory(prefix="vc4-stock-unobserved-") as temp_s:
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
            "-d", "guest_errors,unimp",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
        ]

        started = time.monotonic()
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        result: dict[str, Any] = {
            "schema_version": 1,
            "source_sha": os.environ.get("GITHUB_SHA"),
            "workflow_run_id": os.environ.get("GITHUB_RUN_ID"),
            "quiet_seconds_requested": args.quiet_seconds,
            "qemu_command": command,
            "fat_layout": {name: list(chain) for name, chain in layout.items()},
            "qmp_connected_before_quiet_window": False,
            "sample_count_during_quiet_window": 0,
        }
        qmp = None

        try:
            deadline = started + args.quiet_seconds
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(
                        "QEMU exited during the unobserved window with status "
                        f"{process.returncode}"
                    )
                time.sleep(min(0.1, deadline - time.monotonic()))

            result["quiet_seconds_elapsed"] = time.monotonic() - started
            result["qemu_alive_after_quiet_window"] = process.poll() is None

            # This is intentionally the first connection and first guest-state
            # observation since QEMU was launched.
            qmp = handoff.wait_for_qmp(qmp_path, process, 15.0)
            cpus = qmp.execute("query-cpus-fast")
            vpu = next(
                item
                for item in cpus
                if "vc4" in str(item.get("qom-type", "")).lower()
            )
            vpu_index = int(vpu["cpu-index"])
            vpu_raw = qmp.hmp("info registers", cpu_index=vpu_index)
            vpu_registers = parse_registers(vpu_raw)
            signature = qmp.readq(SIGNATURE_ADDR)
            timer = qmp.readl(SYSTEM_TIMER_CLO)
            cpu_snapshot = handoff.cpu_snapshot(qmp)

            a53_pcs: list[str] = []
            for record in cpu_snapshot.get("cpus", []):
                if "vc4" in str(record.get("qom_type", "")).lower():
                    continue
                registers = parse_registers(str(record.get("registers", "")))
                pc = registers.get("pc")
                if pc is not None:
                    a53_pcs.append(pc)

            signature_seen = signature == SIGNATURE
            vpu_pc = vpu_registers.get("pc")
            result.update({
                "observer_connection_elapsed_seconds": time.monotonic() - started,
                "vpu_cpu_index": vpu_index,
                "vpu_registers": vpu_registers,
                "vpu_registers_raw": vpu_raw,
                "vpu_pc": vpu_pc,
                "a53_pcs": a53_pcs,
                "signature": f"0x{signature:016x}",
                "signature_seen": signature_seen,
                "system_timer_clo": f"0x{timer:08x}",
                "system_timer_us": timer,
                "arm_control0": f"0x{qmp.readl(ARM_CONTROL0):08x}",
                "arm_control1": f"0x{qmp.readl(ARM_CONTROL1):08x}",
                "arm_status": f"0x{qmp.readl(ARM_STATUS):08x}",
                "arm_id": f"0x{qmp.readl(ARM_ID):08x}",
                "pm_proc": f"0x{qmp.readl(PM_PROC):08x}",
                "cpu_snapshot": cpu_snapshot,
                "classification": classify(
                    signature_seen=signature_seen,
                    vpu_pc=vpu_pc,
                    a53_pcs=a53_pcs,
                ),
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

        result["qemu_diagnostics_tail"] = handoff.diagnostics_tail(
            stderr_path.read_text(encoding="utf-8", errors="replace"), 256
        )
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            key: result.get(key)
            for key in (
                "probe_error",
                "classification",
                "quiet_seconds_elapsed",
                "qemu_alive_after_quiet_window",
                "qmp_connected_before_quiet_window",
                "sample_count_during_quiet_window",
                "vpu_pc",
                "a53_pcs",
                "signature_seen",
                "system_timer_us",
                "arm_control0",
                "arm_control1",
                "arm_status",
                "pm_proc",
            )
        }, indent=2, sort_keys=True))

    return 0 if "probe_error" not in result else 2


if __name__ == "__main__":
    raise SystemExit(main())
