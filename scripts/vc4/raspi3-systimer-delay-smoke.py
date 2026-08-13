#!/usr/bin/env python3
"""Verify that a VC4 delay loop survives the 100 ms RR-TCG kick."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import time
from types import ModuleType

DELAY_US = 100_000
SYSTIMER_GPU_BASE = 0x7E003000
SYSTIMER_ARM_LOW = 0x3F003004
MARKER_ADDR = 0x00040000
ELAPSED_ADDR = MARKER_ADDR + 4
START_ADDR = MARKER_ADDR + 8
MARKER_VALUE = 0x51A7DE1A


def load_handoff_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-0200-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_bootrom_0200", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load 0x200 handoff module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_timer_bootcode(smoke: ModuleType) -> bytes:
    program = bytearray()

    # Match the production delay routine's register allocation and loop body.
    program += smoke.vc4_mov32(1, SYSTIMER_GPU_BASE)
    program += smoke.vc4_memory_offset(False, 3, 1, 4)
    program += smoke.vc4_mov32(0, DELAY_US)

    loop_offset = len(program)
    program += smoke.half(0x0001)  # nop
    program += smoke.vc4_memory_offset(False, 2, 1, 4)
    program += smoke.half(0x4632)  # sub r2, r3
    program += smoke.half(0x8300)  # addcmpb r0, 0, r2, hs, loop
    program += smoke.half(0x4BFD)
    if len(program) - loop_offset != 10:
        raise AssertionError("unexpected VC4 delay-loop size")

    # Publish the final elapsed value before the completion marker.
    program += smoke.vc4_mov32(4, ELAPSED_ADDR)
    program += smoke.vc4_memory_offset(True, 2, 4, 0)
    program += smoke.vc4_mov32(4, START_ADDR)
    program += smoke.vc4_memory_offset(True, 3, 4, 0)
    program += smoke.vc4_mov32(4, MARKER_ADDR)
    program += smoke.vc4_mov32(5, MARKER_VALUE)
    program += smoke.vc4_memory_offset(True, 5, 4, 0)
    program += smoke.half(0x0000)  # architectural halt

    if len(program) > smoke.BOOT_PAYLOAD_SIZE:
        raise AssertionError("timer regression exceeds synthetic payload")
    program += bytes(smoke.BOOT_PAYLOAD_SIZE - len(program))

    bootcode = bytes(smoke.BOOT_ENTRY) + bytes(program)
    if len(bootcode) != smoke.BOOT_FILE_SIZE:
        raise AssertionError("unexpected synthetic bootcode size")
    return bootcode


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def connect_qtest(smoke: ModuleType, path: Path,
                  proc: subprocess.Popen[bytes], timeout: float):
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        try:
            return smoke.LineSocket(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.01)
    raise TimeoutError(f"qtest did not accept connections: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    parser.add_argument(
        "--one-insn-per-tb",
        action="store_true",
        help="also force the exact one-instruction debugging mode",
    )
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    handoff = load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    handoff.install_real_handoff(smoke)

    with tempfile.TemporaryDirectory(prefix="vc4-systimer-delay-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "timer-delay.img"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        smoke.build_sd_image(image_path, build_timer_bootcode(smoke))

        accelerator = "tcg,thread=single"
        if args.one_insn_per_tb:
            accelerator += ",one-insn-per-tb=on"
        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", accelerator,
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
            qtest = connect_qtest(smoke, qtest_path, proc, 10.0)

            def readl(address: int) -> int:
                return smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{address:x}")
                )

            deadline = time.monotonic() + args.timeout
            marker = 0
            while time.monotonic() < deadline:
                marker = readl(MARKER_ADDR)
                if marker == MARKER_VALUE:
                    break
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"QEMU exited with status {proc.returncode}"
                    )
                time.sleep(0.01)

            elapsed = readl(ELAPSED_ADDR)
            start = readl(START_ADDR)
            timer = readl(SYSTIMER_ARM_LOW)
            if marker != MARKER_VALUE:
                raise RuntimeError(
                    "VC4 did not resume after the 100 ms RR-TCG kick: "
                    f"marker=0x{marker:08x} elapsed=0x{elapsed:08x} "
                    f"start=0x{start:08x} timer=0x{timer:08x}"
                )
            if elapsed < DELAY_US:
                raise RuntimeError(
                    f"VC4 delay ended early: elapsed={elapsed} us"
                )
            if elapsed > DELAY_US + 500_000:
                raise RuntimeError(
                    f"VC4 delay overshot implausibly: elapsed={elapsed} us"
                )

            mode = "one-insn-per-tb" if args.one_insn_per_tb else "normal-tb"
            print(
                "BCM2835 VC4 system-timer delay passed: "
                f"mode={mode} delay={DELAY_US} elapsed={elapsed} "
                f"start=0x{start:08x} timer=0x{timer:08x} "
                f"marker=0x{marker:08x}"
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
