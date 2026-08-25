#!/usr/bin/env python3
"""Exercise BCM2835 HVS channel state and display-list latching."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from typing import Any


HVS_BASE = 0x3F400000
SCALER_DISPCTRL = HVS_BASE + 0x0000
SCALER_DISPSTAT = HVS_BASE + 0x0004
SCALER_DISPLIST = tuple(HVS_BASE + 0x0020 + index * 4 for index in range(3))
SCALER_DISPLACT = tuple(HVS_BASE + 0x0030 + index * 4 for index in range(3))
SCALER_DISPCTRLX = tuple(HVS_BASE + 0x0040 + index * 0x10 for index in range(3))
SCALER_DISPSTATX = tuple(HVS_BASE + 0x0048 + index * 0x10 for index in range(3))
SCALER_DLIST_START = HVS_BASE + 0x2000

SCALER_DISPCTRLX_ENABLE = 1 << 31
SCALER_DISPCTRLX_RESET = 1 << 30
SCALER_DISPSTATX_MODE_MASK = 3 << 30
SCALER_DISPSTATX_MODE_DISABLED = 0 << 30
SCALER_DISPSTATX_MODE_RUN = 2 << 30
SCALER_DISPSTATX_FULL = 1 << 29
SCALER_DISPSTATX_EMPTY = 1 << 28


def load_property_support() -> Any:
    support_path = Path(__file__).with_name("property-power-domain-smoke.py")
    spec = importlib.util.spec_from_file_location(
        "vc4_property_smoke_support", support_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load property smoke support: {support_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect_disabled_empty(qtest: Any, channel: int) -> None:
    control = qtest.readl(SCALER_DISPCTRLX[channel])
    status = qtest.readl(SCALER_DISPSTATX[channel])
    if control != 0:
        raise RuntimeError(
            f"HVS channel {channel} control is not disabled: 0x{control:08x}"
        )
    if status & SCALER_DISPSTATX_MODE_MASK != SCALER_DISPSTATX_MODE_DISABLED:
        raise RuntimeError(
            f"HVS channel {channel} mode is not disabled: 0x{status:08x}"
        )
    if not status & SCALER_DISPSTATX_EMPTY:
        raise RuntimeError(
            f"HVS channel {channel} is not empty: 0x{status:08x}"
        )
    if status & SCALER_DISPSTATX_FULL:
        raise RuntimeError(
            f"HVS channel {channel} is both full and empty: 0x{status:08x}"
        )


def exercise_channel(qtest: Any, channel: int) -> None:
    first_list = 0x40 + channel * 0x20
    second_list = first_list + 0x10
    control = SCALER_DISPCTRLX_ENABLE | (640 << 12) | 480

    qtest.writel(SCALER_DISPLIST[channel], first_list)
    if qtest.readl(SCALER_DISPLIST[channel]) != first_list:
        raise RuntimeError(f"HVS channel {channel} did not retain DISPLIST")
    if qtest.readl(SCALER_DISPLACT[channel]) != first_list:
        raise RuntimeError(f"HVS channel {channel} did not latch DISPLACT")

    qtest.writel(SCALER_DISPCTRLX[channel], SCALER_DISPCTRLX_RESET)
    expect_disabled_empty(qtest, channel)

    qtest.writel(SCALER_DISPCTRLX[channel], control)
    read_control = qtest.readl(SCALER_DISPCTRLX[channel])
    if not read_control & SCALER_DISPCTRLX_ENABLE:
        raise RuntimeError(f"HVS channel {channel} did not enable")
    if read_control & SCALER_DISPCTRLX_RESET:
        raise RuntimeError(f"HVS channel {channel} RESET did not self-clear")
    status = qtest.readl(SCALER_DISPSTATX[channel])
    if status & SCALER_DISPSTATX_MODE_MASK != SCALER_DISPSTATX_MODE_RUN:
        raise RuntimeError(
            f"HVS channel {channel} did not enter RUN mode: 0x{status:08x}"
        )
    if not status & SCALER_DISPSTATX_FULL or status & SCALER_DISPSTATX_EMPTY:
        raise RuntimeError(
            f"HVS channel {channel} FIFO state is invalid: 0x{status:08x}"
        )

    qtest.writel(SCALER_DISPLIST[channel], second_list)
    if qtest.readl(SCALER_DISPLACT[channel]) != second_list:
        raise RuntimeError(
            f"HVS channel {channel} did not latch replacement display list"
        )

    qtest.writel(SCALER_DISPCTRLX[channel], 0)
    expect_disabled_empty(qtest, channel)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-aarch64"),
        help="path to qemu-system-aarch64",
    )
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    support = load_property_support()

    with tempfile.TemporaryDirectory(prefix="vc4-hvs-dlist-") as temp_dir:
        temp = Path(temp_dir)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
        process = subprocess.Popen(
            (
                str(qemu),
                "-M",
                "raspi3b",
                "-accel",
                "qtest",
                "-S",
                "-display",
                "none",
                "-serial",
                "none",
                "-monitor",
                "none",
                "-qtest",
                f"unix:{qtest_path},server=on,wait=off",
                "-qmp",
                f"unix:{qmp_path},server=on,wait=off",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        qtest = None
        qmp = None
        try:
            qtest = support.connect_when_ready(
                qtest_path, process, support.QTestClient
            )
            qmp = support.connect_when_ready(qmp_path, process, support.QMPClient)

            if qtest.readl(SCALER_DISPCTRL) != 0:
                raise RuntimeError("HVS global control did not reset to zero")
            if qtest.readl(SCALER_DISPSTAT) != 0:
                raise RuntimeError("HVS global status did not reset to zero")
            for channel in range(3):
                expect_disabled_empty(qtest, channel)
                if qtest.readl(SCALER_DISPLIST[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} DISPLIST was not reset"
                    )
                if qtest.readl(SCALER_DISPLACT[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} DISPLACT was not reset"
                    )

            for index, value in enumerate((0x11223344, 0x55667788, 0xA5A55A5A)):
                address = SCALER_DLIST_START + index * 4
                qtest.writel(address, value)
                if qtest.readl(address) != value:
                    raise RuntimeError(
                        f"HVS DLIST RAM did not retain word {index}"
                    )

            for channel in range(3):
                exercise_channel(qtest, channel)

            qmp.execute("system_reset")
            if qtest.readl(SCALER_DISPCTRL) != 0:
                raise RuntimeError("HVS global control survived system reset")
            for channel in range(3):
                expect_disabled_empty(qtest, channel)
                if qtest.readl(SCALER_DISPLIST[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} list survived system reset"
                    )
                if qtest.readl(SCALER_DISPLACT[channel]) != 0:
                    raise RuntimeError(
                        f"HVS channel {channel} active list survived system reset"
                    )
            if qtest.readl(SCALER_DLIST_START) != 0:
                raise RuntimeError("HVS DLIST RAM survived system reset")
        finally:
            if qmp is not None:
                try:
                    qmp.execute("quit")
                except (OSError, RuntimeError):
                    pass
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

        if process.returncode not in (0, None):
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(
                f"QEMU exited with status {process.returncode}:\n{stderr}"
            )

    print("BCM2835 HVS display-list smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
