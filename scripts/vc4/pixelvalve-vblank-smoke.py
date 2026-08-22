#!/usr/bin/env python3
"""Exercise BCM2835 pixel-valve timing and GPU interrupt routing."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from typing import Any


RPI3_PERIPHERAL_BASE = 0x3F000000
ARMCTRL_IC_BASE = RPI3_PERIPHERAL_BASE + 0x0000B200
IRQ_PENDING_2 = ARMCTRL_IC_BASE + 0x08
IRQ_ENABLE_2 = ARMCTRL_IC_BASE + 0x14
IRQ_DISABLE_2 = ARMCTRL_IC_BASE + 0x20

PV_CONTROL = 0x00
PV_CONTROL_FIFO_CLR = 1 << 1
PV_CONTROL_EN = 1 << 0
PV_V_CONTROL = 0x04
PV_VCONTROL_VIDEN = 1 << 0
PV_HORZA = 0x0C
PV_INTEN = 0x24
PV_INTSTAT = 0x28
PV_INT_VFP_START = 1 << 7

FRAME_STEP_NS = 20_000_000

PIXEL_VALVES = (
    ("pv0", RPI3_PERIPHERAL_BASE + 0x00206000, 45),
    ("pv1", RPI3_PERIPHERAL_BASE + 0x00207000, 46),
    ("pv2", RPI3_PERIPHERAL_BASE + 0x00807000, 42),
)


class QTestClient:
    def __init__(self, path: Path) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(str(path))
        self._reader = self._sock.makefile("r", encoding="utf-8")

    def close(self) -> None:
        self._reader.close()
        self._sock.close()

    def command(self, command: str) -> list[str]:
        self._sock.sendall((command + "\n").encode("utf-8"))
        while True:
            response = self._reader.readline()
            if not response:
                raise RuntimeError(f"qtest closed while handling: {command}")
            words = response.strip().split()
            if not words:
                continue
            if words[0] == "IRQ":
                continue
            if words[0] != "OK":
                raise RuntimeError(
                    f"qtest command failed: {command!r}: {response.strip()}"
                )
            return words[1:]

    def writel(self, address: int, value: int) -> None:
        self.command(f"writel 0x{address:x} 0x{value & 0xffffffff:x}")

    def readl(self, address: int) -> int:
        values = self.command(f"readl 0x{address:x}")
        if len(values) != 1:
            raise RuntimeError(f"unexpected qtest readl response: {values!r}")
        return int(values[0], 0)

    def clock_step(self, nanoseconds: int) -> int:
        values = self.command(f"clock_step {nanoseconds}")
        if len(values) != 1:
            raise RuntimeError(f"unexpected qtest clock_step response: {values!r}")
        return int(values[0], 0)


class QMPClient:
    def __init__(self, path: Path) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.connect(str(path))
        self._reader = self._sock.makefile("r", encoding="utf-8")
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"unexpected QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def close(self) -> None:
        self._reader.close()
        self._sock.close()

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self._reader.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            message = json.loads(line)
            if isinstance(message, dict):
                return message

    def execute(self, command: str) -> Any:
        request = {"execute": command, "id": command}
        self._sock.sendall((json.dumps(request) + "\n").encode("utf-8"))
        while True:
            response = self._read_message()
            if response.get("id") != command:
                continue
            if "error" in response:
                raise RuntimeError(f"QMP {command} failed: {response['error']!r}")
            return response.get("return")


def connect_when_ready(
    path: Path,
    process: subprocess.Popen[str],
    constructor: type[QTestClient] | type[QMPClient],
    timeout: float = 10.0,
) -> QTestClient | QMPClient:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(
                f"QEMU exited with status {process.returncode}:\n{stderr}"
            )
        try:
            return constructor(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
        time.sleep(0.02)
    raise RuntimeError(f"timed out connecting to {path}: {last_error}")


def expect_bit(value: int, bit: int, expected: bool, what: str) -> None:
    present = bool(value & bit)
    if present != expected:
        raise RuntimeError(
            f"{what}: value 0x{value:08x}, bit 0x{bit:08x}, "
            f"expected {'set' if expected else 'clear'}"
        )


def expect_register(
    qtest: QTestClient, address: int, expected: int, what: str
) -> None:
    actual = qtest.readl(address)
    if actual != expected:
        raise RuntimeError(
            f"{what}: value 0x{actual:08x}, expected 0x{expected:08x}"
        )


def exercise_pixel_valve(
    qtest: QTestClient, name: str, base: int, gpu_irq: int
) -> None:
    irq_bit = 1 << (gpu_irq - 32)

    qtest.writel(base + PV_V_CONTROL, 0)
    qtest.writel(base + PV_CONTROL, 0)
    qtest.writel(base + PV_INTEN, 0)
    qtest.writel(base + PV_INTSTAT, 0xFFFFFFFF)
    qtest.writel(IRQ_DISABLE_2, irq_bit)

    qtest.writel(base + PV_HORZA, 0x12345678)
    expect_register(
        qtest, base + PV_HORZA, 0x12345678,
        f"{name} timing register",
    )

    qtest.writel(IRQ_ENABLE_2, irq_bit)
    qtest.writel(base + PV_INTEN, PV_INT_VFP_START)
    qtest.writel(
        base + PV_CONTROL,
        PV_CONTROL_EN | PV_CONTROL_FIFO_CLR,
    )
    expect_register(
        qtest, base + PV_CONTROL, PV_CONTROL_EN,
        f"{name} FIFO clear pulse",
    )
    qtest.writel(base + PV_V_CONTROL, PV_VCONTROL_VIDEN)
    expect_register(
        qtest, base + PV_V_CONTROL, PV_VCONTROL_VIDEN,
        f"{name} video enable",
    )
    expect_register(
        qtest, base + PV_INTEN, PV_INT_VFP_START,
        f"{name} vblank interrupt enable",
    )

    advanced_to = qtest.clock_step(FRAME_STEP_NS)
    if advanced_to <= 0:
        raise RuntimeError(
            f"{name}: virtual clock did not advance: {advanced_to}"
        )

    expect_bit(
        qtest.readl(base + PV_INTSTAT),
        PV_INT_VFP_START,
        True,
        f"{name} local vblank",
    )
    expect_bit(
        qtest.readl(IRQ_PENDING_2),
        irq_bit,
        True,
        f"{name} GPU IRQ {gpu_irq}",
    )

    qtest.writel(base + PV_INTSTAT, PV_INT_VFP_START)
    expect_bit(
        qtest.readl(base + PV_INTSTAT),
        PV_INT_VFP_START,
        False,
        f"{name} W1C local vblank",
    )
    expect_bit(
        qtest.readl(IRQ_PENDING_2),
        irq_bit,
        False,
        f"{name} W1C routed IRQ",
    )

    qtest.writel(base + PV_V_CONTROL, 0)
    qtest.clock_step(FRAME_STEP_NS)
    expect_bit(
        qtest.readl(base + PV_INTSTAT),
        PV_INT_VFP_START,
        False,
        f"{name} disabled local vblank",
    )
    expect_bit(
        qtest.readl(IRQ_PENDING_2),
        irq_bit,
        False,
        f"{name} disabled routed IRQ",
    )

    qtest.writel(base + PV_INTEN, 0)
    qtest.writel(base + PV_CONTROL, 0)
    qtest.writel(IRQ_DISABLE_2, irq_bit)


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

    with tempfile.TemporaryDirectory(prefix="vc4-pixelvalve-") as temp_dir:
        temp = Path(temp_dir)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
        command = (
            str(qemu),
            "-M", "raspi3b",
            "-accel", "qtest",
            "-display", "none",
            "-serial", "none",
            "-monitor", "none",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        qtest: QTestClient | None = None
        qmp: QMPClient | None = None
        try:
            connected_qtest = connect_when_ready(
                qtest_path, process, QTestClient
            )
            connected_qmp = connect_when_ready(qmp_path, process, QMPClient)
            assert isinstance(connected_qtest, QTestClient)
            assert isinstance(connected_qmp, QMPClient)
            qtest = connected_qtest
            qmp = connected_qmp

            for name, base, gpu_irq in PIXEL_VALVES:
                exercise_pixel_valve(qtest, name, base, gpu_irq)

            qmp.execute("system_reset")
            for name, base, gpu_irq in PIXEL_VALVES:
                expect_register(
                    qtest, base + PV_CONTROL, 0,
                    f"{name} reset control",
                )
                expect_register(
                    qtest, base + PV_V_CONTROL, 0,
                    f"{name} reset video control",
                )
                expect_register(
                    qtest, base + PV_INTEN, 0,
                    f"{name} reset interrupt enable",
                )
                expect_register(
                    qtest, base + PV_INTSTAT, 0,
                    f"{name} reset interrupt status",
                )
                irq_bit = 1 << (gpu_irq - 32)
                expect_bit(
                    qtest.readl(IRQ_PENDING_2), irq_bit, False,
                    f"{name} reset routed IRQ",
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

    print("BCM2835 pixel-valve vblank and IRQ routing smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
