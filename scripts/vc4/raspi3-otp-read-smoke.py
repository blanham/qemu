#!/usr/bin/env python3
"""Exercise the BCM2837 VPU-facing OTP read command through qtest."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType

OTP_ARM_BASE = 0x3F20F000
OTP_CONFIG = 0x04
OTP_CTRL_LO = 0x08
OTP_CTRL_HI = 0x0C
OTP_STATUS = 0x10
OTP_BITSEL = 0x14
OTP_DATA = 0x18
OTP_ADDR = 0x1C

OTP_CTRL_LO_START = 1 << 0
OTP_STATUS_CMD_DONE = 1 << 0
OTP_READ_COMMAND = 0
OTP_PROGRAM_WORD_COMMAND = 0x14
TEST_ROW = 36


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

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-otp-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "otp-sd.img"
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

            def read_reg(offset: int) -> int:
                return smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{OTP_ARM_BASE + offset:x}")
                )

            def write_reg(offset: int, value: int) -> None:
                smoke.qtest_writel(qtest, OTP_ARM_BASE + offset, value)

            if read_reg(OTP_STATUS) != 0:
                raise RuntimeError("OTP status did not reset to idle")

            # Verify the documented register masks independently of commands.
            write_reg(OTP_CONFIG, 0xFFFFFFFF)
            write_reg(OTP_CTRL_HI, 0xFFFFFFFF)
            write_reg(OTP_BITSEL, 0xFFFFFFFF)
            if read_reg(OTP_CONFIG) != 0x7:
                raise RuntimeError("OTP CONFIG mask was not applied")
            if read_reg(OTP_CTRL_HI) != 0xFFFF:
                raise RuntimeError("OTP CTRL_HI mask was not applied")
            if read_reg(OTP_BITSEL) != 0x1F:
                raise RuntimeError("OTP BITSEL mask was not applied")

            # Issue the same read sequence used by production VideoCore code.
            write_reg(OTP_ADDR, TEST_ROW)
            write_reg(OTP_CTRL_HI, 0)
            write_reg(OTP_CTRL_LO, OTP_READ_COMMAND)
            if read_reg(OTP_STATUS) != 0:
                raise RuntimeError("OTP command-done did not clear before start")
            write_reg(OTP_CTRL_LO, OTP_READ_COMMAND | OTP_CTRL_LO_START)

            status = read_reg(OTP_STATUS)
            data = read_reg(OTP_DATA)
            if not status & OTP_STATUS_CMD_DONE:
                raise RuntimeError(
                    f"OTP read never completed: status=0x{status:08x}"
                )
            if data != 0:
                raise RuntimeError(
                    f"blank OTP row {TEST_ROW} returned 0x{data:08x}"
                )
            if read_reg(OTP_ADDR) != TEST_ROW:
                raise RuntimeError("OTP address latch did not retain the row")

            # An unsupported programming command must complete but never burn
            # a bit in the emulated OTP array.
            write_reg(OTP_CTRL_LO, 0)
            write_reg(OTP_DATA, 0xFFFFFFFF)
            write_reg(OTP_CTRL_LO, OTP_PROGRAM_WORD_COMMAND)
            write_reg(
                OTP_CTRL_LO,
                OTP_PROGRAM_WORD_COMMAND | OTP_CTRL_LO_START,
            )
            program_status = read_reg(OTP_STATUS)
            if not program_status & OTP_STATUS_CMD_DONE:
                raise RuntimeError("unsupported OTP program command hung")

            write_reg(OTP_CTRL_LO, OTP_READ_COMMAND)
            write_reg(OTP_CTRL_LO, OTP_READ_COMMAND | OTP_CTRL_LO_START)
            data_after_program = read_reg(OTP_DATA)
            if data_after_program != 0:
                raise RuntimeError(
                    "read-only OTP model mutated a row: "
                    f"0x{data_after_program:08x}"
                )

            print(
                "BCM2837 OTP read command passed: "
                f"row={TEST_ROW} status=0x{status:08x} "
                f"data=0x{data:08x} "
                f"program-status=0x{program_status:08x} "
                "programming=ignored"
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
