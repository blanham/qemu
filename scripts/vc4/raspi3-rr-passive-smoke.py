#!/usr/bin/env python3
"""Verify VC4 progress after ARM release without qtest polling traffic."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import ModuleType


def load_module(filename: str, name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument(
        "--quiescent-seconds",
        type=float,
        default=5.0,
        help="host interval with no qtest requests after connection",
    )
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")
    if args.quiescent_seconds <= 0:
        parser.error("--quiescent-seconds must be positive")

    timer = load_module(
        "raspi3-systimer-delay-smoke.py", "vc4_systimer_delay"
    )
    handoff = timer.load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    handoff.install_real_handoff(smoke)

    with tempfile.TemporaryDirectory(prefix="vc4-rr-passive-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "rr-passive.img"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        smoke.build_sd_image(image_path, timer.build_timer_bootcode(smoke))

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-d", "guest_errors",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qtest = None
        try:
            qtest = timer.connect_qtest(smoke, qtest_path, proc, 10.0)

            # This is the essential regression condition.  Keep the socket
            # open, but send no qtest request capable of externally kicking
            # ARM0's branch-to-self TB while the VPU completes its delay.
            time.sleep(args.quiescent_seconds)

            def readl(address: int) -> int:
                return smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{address:x}")
                )

            marker = readl(timer.MARKER_ADDR)
            elapsed = readl(timer.ELAPSED_ADDR)
            start = readl(timer.START_ADDR)
            now = readl(timer.SYSTIMER_ARM_LOW)
            arm_marker = readl(timer.ARM_MARKER_ADDR)
            proc_state = readl(timer.PM_PROC_ARM)

            if marker != timer.MARKER_VALUE:
                raise RuntimeError(
                    "VC4 was starved without qtest polling: "
                    f"marker=0x{marker:08x} elapsed={elapsed} "
                    f"start=0x{start:08x} timer=0x{now:08x} "
                    f"arm-marker=0x{arm_marker:08x} "
                    f"pm-proc=0x{proc_state:08x}"
                )
            if arm_marker != timer.ARM_MARKER_VALUE:
                raise RuntimeError(
                    f"ARM0 marker mismatch: 0x{arm_marker:08x}"
                )
            if proc_state != timer.PM_PROC_READY:
                raise RuntimeError(
                    f"PM_PROC state mismatch: 0x{proc_state:08x}"
                )
            if elapsed < timer.DELAY_US:
                raise RuntimeError(
                    f"VC4 delay ended early: elapsed={elapsed} us"
                )

            print(
                "VC4 passive RR fairness passed: "
                f"quiescent={args.quiescent_seconds:.3f}s "
                f"delay={timer.DELAY_US} elapsed={elapsed} "
                f"start=0x{start:08x} timer=0x{now:08x} "
                f"arm-marker=0x{arm_marker:08x}"
            )
            return 0
        except Exception:
            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if diagnostics:
                print("--- qemu stderr ---", file=os.sys.stderr)
                print(diagnostics, file=os.sys.stderr)
            raise
        finally:
            if qtest is not None:
                qtest.close()
            stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
