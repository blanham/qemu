#!/usr/bin/env python3
"""Exercise BCM2835 HDMI FIFO and pixel-valve vblank/IRQ timing."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from typing import Any


RPI3_PERIPHERAL_BASE = 0x3F000000
HDMI_CORE_BASE = RPI3_PERIPHERAL_BASE + 0x00902000
PIXELVALVES = (
    (RPI3_PERIPHERAL_BASE + 0x00206000, 45),
    (RPI3_PERIPHERAL_BASE + 0x00207000, 46),
    (RPI3_PERIPHERAL_BASE + 0x00807000, 42),
)
INTERRUPT_CONTROLLER_BASE = RPI3_PERIPHERAL_BASE + 0x0000B200

HDMI_FIFO_CTL = HDMI_CORE_BASE + 0x05C
HDMI_FIFO_CTL_RECENTER = 1 << 6
HDMI_FIFO_CTL_RECENTER_DONE = 1 << 14

PV_CONTROL = 0x00
PV_CONTROL_FIFO_CLR = 1 << 1
PV_CONTROL_EN = 1 << 0
PV_V_CONTROL = 0x04
PV_VCONTROL_CONTINUOUS = 1 << 1
PV_VCONTROL_VIDEN = 1 << 0
PV_INTEN = 0x24
PV_INTSTAT = 0x28
PV_INT_VFP_START = 1 << 7

IRQ_PENDING_2 = INTERRUPT_CONTROLLER_BASE + 0x08
IRQ_ENABLE_2 = INTERRUPT_CONTROLLER_BASE + 0x14
IRQ_DISABLE_2 = INTERRUPT_CONTROLLER_BASE + 0x20

FRAME_STEP_NS = 20_000_000
PIXELVALVE_IRQ_MASK = sum(1 << (irq - 32) for _base, irq in PIXELVALVES)


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


def gpu_irq_bit(irq: int) -> int:
    if not 32 <= irq < 64:
        raise ValueError(f"IRQ {irq} is not in GPU pending register 2")
    return 1 << (irq - 32)


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


def exercise_pixelvalve(qtest: Any, base: int, irq: int, index: int) -> None:
    irq_mask = gpu_irq_bit(irq)

    for offset in (PV_CONTROL, PV_V_CONTROL, PV_INTEN, PV_INTSTAT):
        if qtest.readl(base + offset) != 0:
            raise RuntimeError(
                f"pixel valve {index} register 0x{offset:x} was not reset"
            )

    qtest.writel(base + PV_INTSTAT, 0xFFFFFFFF)
    qtest.writel(IRQ_ENABLE_2, irq_mask)
    qtest.writel(base + PV_INTEN, PV_INT_VFP_START)

    qtest.writel(base + PV_CONTROL, PV_CONTROL_FIFO_CLR | PV_CONTROL_EN)
    control = qtest.readl(base + PV_CONTROL)
    expect_bits(control, PV_CONTROL_EN, f"pixel valve {index} did not enable")
    if control & PV_CONTROL_FIFO_CLR:
        raise RuntimeError(
            f"pixel valve {index} FIFO clear pulse did not self-clear"
        )

    qtest.writel(
        base + PV_V_CONTROL,
        PV_VCONTROL_CONTINUOUS | PV_VCONTROL_VIDEN,
    )
    qtest.command(f"clock_step {FRAME_STEP_NS}")

    expect_bits(
        qtest.readl(base + PV_INTSTAT),
        PV_INT_VFP_START,
        f"pixel valve {index} did not raise VFP-start",
    )
    expect_bits(
        qtest.readl(IRQ_PENDING_2),
        irq_mask,
        f"pixel valve {index} did not reach BCM2835 interrupt controller",
    )

    qtest.writel(base + PV_INTSTAT, PV_INT_VFP_START)
    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:
        raise RuntimeError(
            f"pixel valve {index} VFP-start was not cleared by W1C"
        )
    if qtest.readl(IRQ_PENDING_2) & irq_mask:
        raise RuntimeError(
            f"pixel valve {index} IRQ remained asserted after W1C"
        )

    qtest.writel(base + PV_V_CONTROL, 0)
    qtest.writel(base + PV_CONTROL, 0)
    qtest.command(f"clock_step {FRAME_STEP_NS}")
    if qtest.readl(base + PV_INTSTAT) & PV_INT_VFP_START:
        raise RuntimeError(
            f"pixel valve {index} generated VFP-start while disabled"
        )

    qtest.writel(base + PV_INTEN, 0)
    qtest.writel(IRQ_DISABLE_2, irq_mask)


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

            # -S leaves the virtual clock disabled.  The qtest accelerator
            # does not execute guest CPUs, so continuing is safe and permits
            # clock_step to dispatch virtual frame timers deterministically.
            qmp.execute("cont")

            exercise_hdmi(qtest)
            for index, (base, irq) in enumerate(PIXELVALVES):
                exercise_pixelvalve(qtest, base, irq, index)

            qmp.execute("system_reset")
            if qtest.readl(HDMI_FIFO_CTL) != 0:
                raise RuntimeError("HDMI FIFO state survived system reset")
            if qtest.readl(IRQ_PENDING_2) & PIXELVALVE_IRQ_MASK:
                raise RuntimeError("pixel-valve interrupt survived system reset")
            for index, (base, _irq) in enumerate(PIXELVALVES):
                for offset in (PV_CONTROL, PV_V_CONTROL, PV_INTEN, PV_INTSTAT):
                    if qtest.readl(base + offset) != 0:
                        raise RuntimeError(
                            f"pixel valve {index} state survived system reset"
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

    print("BCM2835 HDMI and pixel-valve timing/IRQ smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
