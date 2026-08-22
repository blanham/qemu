#!/usr/bin/env python3
"""Read a monitor EDID through the Raspberry Pi BSC2 HDMI DDC bus."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from typing import Any


RPI3_PERIPHERAL_BASE = 0x3F000000
BSC2_BASE = RPI3_PERIPHERAL_BASE + 0x00805000

BCM2835_I2C_C = BSC2_BASE + 0x00
BCM2835_I2C_S = BSC2_BASE + 0x04
BCM2835_I2C_DLEN = BSC2_BASE + 0x08
BCM2835_I2C_A = BSC2_BASE + 0x0C
BCM2835_I2C_FIFO = BSC2_BASE + 0x10

BCM2835_I2C_C_READ = 1 << 0
BCM2835_I2C_C_ST = 1 << 7
BCM2835_I2C_C_INTD = 1 << 8
BCM2835_I2C_C_INTT = 1 << 9
BCM2835_I2C_C_INTR = 1 << 10
BCM2835_I2C_C_I2CEN = 1 << 15

BCM2835_I2C_S_DONE = 1 << 1
BCM2835_I2C_S_TA = 1 << 0
BCM2835_I2C_S_ERR = 1 << 8
BCM2835_I2C_S_CLKT = 1 << 9

HDMI_DDC_ADDRESS = 0x50
EDID_BLOCK_SIZE = 128
EDID_HEADER = bytes((0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00))


def load_qtest_support() -> Any:
    support_path = Path(__file__).with_name("property-power-domain-smoke.py")
    spec = importlib.util.spec_from_file_location(
        "vc4_property_smoke_support", support_path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load qtest support: {support_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clear_status(qtest: Any) -> None:
    qtest.writel(
        BCM2835_I2C_S,
        BCM2835_I2C_S_DONE | BCM2835_I2C_S_ERR | BCM2835_I2C_S_CLKT,
    )


def require_transfer_success(qtest: Any, operation: str) -> None:
    status = qtest.readl(BCM2835_I2C_S)
    if status & (BCM2835_I2C_S_ERR | BCM2835_I2C_S_CLKT):
        raise RuntimeError(
            f"BSC2 {operation} failed with status 0x{status:08x}"
        )
    if not status & BCM2835_I2C_S_DONE:
        raise RuntimeError(
            f"BSC2 {operation} did not complete: status=0x{status:08x}"
        )
    if status & BCM2835_I2C_S_TA:
        raise RuntimeError(
            f"BSC2 {operation} remained active: status=0x{status:08x}"
        )


def set_ddc_pointer(qtest: Any, offset: int) -> None:
    clear_status(qtest)
    qtest.writel(BCM2835_I2C_A, HDMI_DDC_ADDRESS)
    qtest.writel(BCM2835_I2C_DLEN, 1)
    qtest.writel(
        BCM2835_I2C_C,
        BCM2835_I2C_C_I2CEN | BCM2835_I2C_C_ST,
    )
    qtest.writel(BCM2835_I2C_FIFO, offset & 0xFF)
    require_transfer_success(qtest, "DDC pointer write")


def read_ddc(qtest: Any, length: int) -> bytes:
    clear_status(qtest)
    qtest.writel(BCM2835_I2C_A, HDMI_DDC_ADDRESS)
    qtest.writel(BCM2835_I2C_DLEN, length)
    qtest.writel(
        BCM2835_I2C_C,
        BCM2835_I2C_C_I2CEN | BCM2835_I2C_C_ST | BCM2835_I2C_C_READ,
    )
    data = bytes(
        qtest.readl(BCM2835_I2C_FIFO) & 0xFF for _ in range(length)
    )
    require_transfer_success(qtest, "DDC read")
    return data


def read_ddc_linux_sequence(
    qtest: Any, offset: int, length: int
) -> bytes:
    """Issue the two-message write/read sequence used by i2c-bcm2835.

    The Linux driver starts the read from its TXW interrupt without clearing
    DONE from the pointer write. This exercises QEMU's transfer handoff rather
    than relying only on two fully separated controller transactions.
    """
    clear_status(qtest)
    qtest.writel(BCM2835_I2C_A, HDMI_DDC_ADDRESS)
    qtest.writel(BCM2835_I2C_DLEN, 1)
    qtest.writel(
        BCM2835_I2C_C,
        BCM2835_I2C_C_I2CEN | BCM2835_I2C_C_ST | BCM2835_I2C_C_INTT,
    )
    qtest.writel(BCM2835_I2C_FIFO, offset & 0xFF)

    # Match bcm2835_i2c_start_transfer() for the final read message. Do not
    # clear the first transfer's status before issuing the second ST request.
    qtest.writel(BCM2835_I2C_A, HDMI_DDC_ADDRESS)
    qtest.writel(BCM2835_I2C_DLEN, length)
    qtest.writel(
        BCM2835_I2C_C,
        BCM2835_I2C_C_I2CEN
        | BCM2835_I2C_C_ST
        | BCM2835_I2C_C_READ
        | BCM2835_I2C_C_INTR
        | BCM2835_I2C_C_INTD,
    )
    data = bytes(
        qtest.readl(BCM2835_I2C_FIFO) & 0xFF for _ in range(length)
    )
    require_transfer_success(qtest, "Linux-style DDC write/read")
    return data


def validate_edid(edid: bytes) -> None:
    if len(edid) != EDID_BLOCK_SIZE:
        raise RuntimeError(f"unexpected EDID length: {len(edid)}")
    if not edid.startswith(EDID_HEADER):
        raise RuntimeError(f"invalid EDID header: {edid[:8].hex()}")
    if sum(edid) & 0xFF:
        raise RuntimeError("EDID checksum is invalid")
    if edid[18] != 1 or edid[19] < 3:
        raise RuntimeError(
            f"unexpected EDID version {edid[18]}.{edid[19]}"
        )
    if edid[20] == 0:
        raise RuntimeError("EDID reports an invalid video input definition")


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

    support = load_qtest_support()

    with tempfile.TemporaryDirectory(prefix="vc4-hdmi-ddc-") as temp_dir:
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

            set_ddc_pointer(qtest, 0)
            edid = read_ddc(qtest, EDID_BLOCK_SIZE)
            validate_edid(edid)

            combined_edid = read_ddc_linux_sequence(
                qtest, 0, EDID_BLOCK_SIZE
            )
            if combined_edid != edid:
                raise RuntimeError(
                    "Linux-style DDC sequence returned different EDID data"
                )

            set_ddc_pointer(qtest, 8)
            identity = read_ddc(qtest, 16)
            if identity != edid[8:24]:
                raise RuntimeError(
                    "DDC pointer write did not select the requested EDID range"
                )

            combined_identity = read_ddc_linux_sequence(qtest, 8, 16)
            if combined_identity != edid[8:24]:
                raise RuntimeError(
                    "Linux-style DDC pointer/read selected the wrong range"
                )

            qmp.execute("system_reset")
            reset_header = read_ddc(qtest, len(EDID_HEADER))
            if reset_header != EDID_HEADER:
                raise RuntimeError(
                    f"DDC pointer did not reset: {reset_header.hex()}"
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

    print(
        "Raspberry Pi HDMI DDC smoke test passed: "
        f"EDID {edid[18]}.{edid[19]}, checksum=0x{sum(edid) & 0xff:02x}, "
        "Linux-style write/read verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
