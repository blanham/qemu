#!/usr/bin/env python3
"""Exercise BCM2835 HDMI FIFO and pixel-valve completion timing."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from typing import Any


RPI3_PERIPHERAL_BASE = 0x3F000000
HDMI_CORE_BASE = RPI3_PERIPHERAL_BASE + 0x00902000
PIXELVALVE_BASES = (
    RPI3_PERIPHERAL_BASE + 0x00206000,
    RPI3_PERIPHERAL_BASE + 0x00207000,
    RPI3_PERIPHERAL_BASE + 0x00807000,
)

HDMI_FIFO_CTL = HDMI_CORE_BASE + 0x05C
HDMI_FIFO_CTL_RECENTER = 1 << 6
HDMI_FIFO_CTL_RECENTER_DONE = 1 << 14

PV_V_CONTROL = 0x0C
PV_INTEN = 0x24
PV_INTSTAT = 0x28
PV_STAT = 0x2C
PV_INT_VFP_START = 1 << 1
PV_VCONTROL_VIDEN = 1 << 0
PV_VCONTROL_CONTINUOUS = 1 << 1
PV_STAT_VIDEN = 1 << 0

FRAME_STEP_NS = 20_000_000


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


def expect_bits(actual: int, required: int, description: str) -> None:
    if actual & required != required:
        raise RuntimeError(
            f"{description}: 0x{actual:08x} lacks 0x{required:08x}"
        )


def exercise_hdmi(qtest: Any) -> None:
    if qtest.readl(HDMI_FIFO_CTL) != 0:
        raise RuntimeError("HDMI FIFO control did not reset to zero")

    qtest.writel(HDMI_FIFO_CTL, HDMI_FIFO_CTL_RECENTER)
    fifo = qtest.readl(HDMI_FIFO_CTL)
    expect_bits(
        fifo,
        HDMI_FIFO_CTL_RECENTER | HDMI_FIFO_CTL_RECENTER_DONE,
        "HDMI FIFO recenter did not complete",
    )

    qtest.writel(HDMI_FIFO_CTL, 0)
    fifo = qtest.readl(HDMI_FIFO_CTL)
    if fifo & HDMI_FIFO_CTL_RECENTER_DONE:
        raise RuntimeError(
            f"HDMI FIFO recenter completion did not clear: 0x{fifo:08x}"
        )


def exercise_pixelvalve(qtest: Any, base: int, index: int) -> None:
    if qtest.readl(base + PV_STAT) != 0:
        raise RuntimeError(f"pixel valve {index} active after reset")
    if qtest.readl(base + PV_INTSTAT) != 0:
        raise RuntimeError(f"pixel valve {index} interrupt pending after reset")

    qtest.writel(base + PV_INTEN, PV_INT_VFP_START)
    qtest.writel(
        base + PV_V_CONTROL,
        PV_VCONTROL_VIDEN | PV_VCONTROL_CONTINUOUS,
    )
    expect_bits(
        qtest.readl(base + PV_STAT),
        PV_STAT_VIDEN,
        f"pixel valve {index} did not become active",
    )

    qtest.command(f"clock_step {FRAME_STEP_NS}")
    expect_bits(
        qtest.readl(base + PV_INTSTAT),
        PV_INT_VFP_START,
        f"pixel valve {index} did not raise VFP-start",
    )

    qtest.writel(base + PV_INTSTAT, PV_INT_VFP_START)
    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:
        raise RuntimeError(
            f"pixel valve {index} VFP-start was not cleared by W1C"
        )

    qtest.writel(base + PV_V_CONTROL, 0)
    if qtest.readl(base + PV_STAT) & PV_STAT_VIDEN:
        raise RuntimeError(f"pixel valve {index} did not stop immediately")

    qtest.command(f"clock_step {FRAME_STEP_NS}")
    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:
        raise RuntimeError(
            f"pixel valve {index} generated VFP-start while disabled"
        )


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

    with tempfile.TemporaryDirectory(prefix="vc4-display-timing-") as temp_dir:
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

            exercise_hdmi(qtest)
            for index, base in enumerate(PIXELVALVE_BASES):
                exercise_pixelvalve(qtest, base, index)

            qmp.execute("system_reset")
            if qtest.readl(HDMI_FIFO_CTL) != 0:
                raise RuntimeError("HDMI FIFO state survived system reset")
            for index, base in enumerate(PIXELVALVE_BASES):
                if qtest.readl(base + PV_STAT) != 0:
                    raise RuntimeError(
                        f"pixel valve {index} active state survived reset"
                    )
                if qtest.readl(base + PV_INTSTAT) != 0:
                    raise RuntimeError(
                        f"pixel valve {index} interrupt survived reset"
                    )
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

    print("BCM2835 HDMI and pixel-valve timing smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
