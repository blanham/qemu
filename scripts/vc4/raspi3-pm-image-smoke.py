#!/usr/bin/env python3
"""Exercise the BCM2835 PM_IMAGE power-domain handshake through qtest."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType

PM_ARM_BASE = 0x3F100000
PM_IMAGE = 0x108
PM_PASSWORD = 0x5A000000

PM_CFG_BOOT = 1 << 16
PM_ENAB = 1 << 12
PM_ISPRSTN = 1 << 8
PM_H264RSTN = 1 << 7
PM_PERIRSTN = 1 << 6
PM_ISFUNC = 1 << 5
PM_MRDONE = 1 << 4
PM_MEMREP = 1 << 3
PM_ISPOW = 1 << 2
PM_POWOK = 1 << 1
PM_POWUP = 1 << 0

PM_IMAGE_MASK = 0x007F11FF
PM_IMAGE_WRITABLE = 0x007F11ED
PM_IMAGE_RESET = PM_ENAB


def load_handoff_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-0200-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_bootrom_0200", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load 0x200 handoff module from {path}")
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
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    handoff = load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    handoff.install_real_handoff(smoke)

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-pm-image-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "pm-image-sd.img"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        smoke.build_sd_image(image_path, smoke.build_bootcode())

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-S",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qtest = None
        try:
            smoke.wait_for_socket(qtest_path, proc, 10.0)
            qtest = smoke.LineSocket(qtest_path)

            def read_image() -> int:
                return smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{PM_ARM_BASE + PM_IMAGE:x}")
                )

            def write_image(value: int, *, password: bool = True) -> None:
                payload = value & 0x00FFFFFF
                if password:
                    payload |= PM_PASSWORD
                smoke.qtest_writel(
                    qtest,
                    PM_ARM_BASE + PM_IMAGE,
                    payload,
                )

            reset = read_image()
            if reset != PM_IMAGE_RESET:
                raise RuntimeError(
                    f"PM_IMAGE reset mismatch: 0x{reset:08x}"
                )

            # Password-less writes are ignored.
            write_image(0, password=False)
            if read_image() != PM_IMAGE_RESET:
                raise RuntimeError("PM_IMAGE accepted a write without password")

            # Mirror the open-firmware image-domain sequence.
            value = reset | PM_CFG_BOOT | PM_PERIRSTN
            write_image(value)
            configured = read_image()
            if configured != value:
                raise RuntimeError(
                    "PM_IMAGE configuration did not latch: "
                    f"0x{configured:08x}"
                )

            write_image(configured | PM_POWUP)
            powered = read_image()
            expected = configured | PM_POWUP | PM_POWOK
            if powered != expected:
                raise RuntimeError(
                    "PM_IMAGE POWUP did not produce POWOK: "
                    f"0x{powered:08x}"
                )

            write_image(powered | PM_ISPOW)
            isolated = read_image()
            expected |= PM_ISPOW
            if isolated != expected:
                raise RuntimeError(
                    "PM_IMAGE ISPOW did not latch: "
                    f"0x{isolated:08x}"
                )

            write_image(isolated | PM_MEMREP)
            repaired = read_image()
            expected |= PM_MEMREP | PM_MRDONE
            if repaired != expected:
                raise RuntimeError(
                    "PM_IMAGE MEMREP did not produce MRDONE: "
                    f"0x{repaired:08x}"
                )

            write_image(
                repaired | PM_ISFUNC | PM_H264RSTN | PM_ISPRSTN
            )
            ready = read_image()
            expected |= PM_ISFUNC | PM_H264RSTN | PM_ISPRSTN
            if ready != expected:
                raise RuntimeError(
                    "PM_IMAGE reset release did not latch: "
                    f"0x{ready:08x}"
                )

            # The complete register mask must preserve writable controls and
            # synthesize the two read-only handshake bits.
            write_image(0xFFFFFFFF)
            saturated = read_image()
            if saturated != PM_IMAGE_MASK:
                raise RuntimeError(
                    "PM_IMAGE register mask mismatch: "
                    f"0x{saturated:08x}"
                )

            # Dropping POWUP clears both derived status bits immediately.
            write_image(saturated & ~PM_POWUP)
            powered_down = read_image()
            expected_down = PM_IMAGE_WRITABLE & ~PM_POWUP
            if powered_down != expected_down:
                raise RuntimeError(
                    "PM_IMAGE power-down did not clear handshakes: "
                    f"0x{powered_down:08x}"
                )
            if powered_down & (PM_POWOK | PM_MRDONE):
                raise RuntimeError("PM_IMAGE retained derived ready bits")

            print(
                "BCM2835 PM_IMAGE power handshake passed: "
                f"reset=0x{reset:08x} configured=0x{configured:08x} "
                f"powok=0x{powered:08x} mrdone=0x{repaired:08x} "
                f"ready=0x{ready:08x} saturated=0x{saturated:08x} "
                f"down=0x{powered_down:08x}"
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
