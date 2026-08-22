#!/usr/bin/env python3
"""Exercise Raspberry Pi firmware clock enumeration and pixel-clock access."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import tempfile
import time


RPI3_PERIPHERAL_BASE = 0x3F000000
MAILBOX_READ = RPI3_PERIPHERAL_BASE + 0x0000B880
MAILBOX_WRITE = RPI3_PERIPHERAL_BASE + 0x0000B8A0
MAILBOX_PROPERTY_CHANNEL = 8
PROPERTY_BUFFER = 0x00010000
PROPERTY_RESPONSE_SUCCESS = 1 << 31
PROPERTY_RESPONSE_BIT = 1 << 31

RPI_FWREQ_GET_CLOCKS = 0x00010007
RPI_FWREQ_GET_CLOCK_STATE = 0x00030001
RPI_FWREQ_GET_CLOCK_RATE = 0x00030002
RPI_FWREQ_GET_MAX_CLOCK_RATE = 0x00030004
RPI_FWREQ_GET_MIN_CLOCK_RATE = 0x00030007

RPI_FIRMWARE_EMMC_CLK_ID = 1
RPI_FIRMWARE_PIXEL_CLK_ID = 9
RPI_FIRMWARE_DISP_CLK_ID = 16
EXPECTED_CLOCK_IDS = tuple(
    range(RPI_FIRMWARE_EMMC_CLK_ID, RPI_FIRMWARE_DISP_CLK_ID + 1)
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


def connect_when_ready(
    path: Path,
    process: subprocess.Popen[str],
    timeout: float = 10.0,
) -> QTestClient:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(
                f"QEMU exited with status {process.returncode}:\n{stderr}"
            )
        try:
            return QTestClient(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
        time.sleep(0.02)
    raise RuntimeError(f"timed out connecting to {path}: {last_error}")


def property_request(
    qtest: QTestClient,
    tag: int,
    payload_words: list[int],
    response_words: int,
) -> list[int]:
    payload_size = len(payload_words) * 4
    total_size = 24 + payload_size
    words = [
        total_size,
        0,
        tag,
        payload_size,
        0,
        *payload_words,
        0,
    ]
    for index, word in enumerate(words):
        qtest.writel(PROPERTY_BUFFER + index * 4, word)

    qtest.writel(MAILBOX_WRITE, PROPERTY_BUFFER | MAILBOX_PROPERTY_CHANNEL)
    reply = qtest.readl(MAILBOX_READ)
    expected_reply = PROPERTY_BUFFER | MAILBOX_PROPERTY_CHANNEL
    if reply != expected_reply:
        raise RuntimeError(
            f"mailbox returned 0x{reply:08x}, expected 0x{expected_reply:08x}"
        )

    status = qtest.readl(PROPERTY_BUFFER + 4)
    if status != PROPERTY_RESPONSE_SUCCESS:
        raise RuntimeError(f"property list failed with status 0x{status:08x}")

    response_length = qtest.readl(PROPERTY_BUFFER + 16)
    expected_length = PROPERTY_RESPONSE_BIT | response_words * 4
    if response_length != expected_length:
        raise RuntimeError(
            "property tag returned the wrong response length: "
            f"0x{response_length:08x} != 0x{expected_length:08x}"
        )

    return [
        qtest.readl(PROPERTY_BUFFER + 20 + index * 4)
        for index in range(response_words)
    ]


def get_clock_pairs(qtest: QTestClient, capacity: int) -> list[tuple[int, int]]:
    response = property_request(
        qtest,
        RPI_FWREQ_GET_CLOCKS,
        [0] * (capacity * 2),
        min(capacity, len(EXPECTED_CLOCK_IDS)) * 2,
    )
    return list(zip(response[0::2], response[1::2], strict=True))


def get_clock_value(qtest: QTestClient, tag: int, clock_id: int) -> int:
    response = property_request(qtest, tag, [clock_id, 0], 2)
    returned_id, value = response
    if returned_id != clock_id:
        raise RuntimeError(
            f"clock property changed id {clock_id} to {returned_id}"
        )
    return value


def validate_clocks(qtest: QTestClient) -> None:
    pairs = get_clock_pairs(qtest, len(EXPECTED_CLOCK_IDS) + 1)
    parents = tuple(parent for parent, _clock_id in pairs)
    clock_ids = tuple(clock_id for _parent, clock_id in pairs)
    if parents != (0,) * len(EXPECTED_CLOCK_IDS):
        raise RuntimeError(f"unexpected firmware clock parents: {parents!r}")
    if clock_ids != EXPECTED_CLOCK_IDS:
        raise RuntimeError(
            f"firmware clock IDs are {clock_ids!r}, expected {EXPECTED_CLOCK_IDS!r}"
        )

    truncated = get_clock_pairs(qtest, RPI_FIRMWARE_PIXEL_CLK_ID)
    truncated_ids = tuple(clock_id for _parent, clock_id in truncated)
    if truncated_ids != EXPECTED_CLOCK_IDS[:RPI_FIRMWARE_PIXEL_CLK_ID]:
        raise RuntimeError(
            f"truncated clock response is not a prefix: {truncated_ids!r}"
        )

    state = get_clock_value(
        qtest, RPI_FWREQ_GET_CLOCK_STATE, RPI_FIRMWARE_PIXEL_CLK_ID
    )
    if not state & 1:
        raise RuntimeError(f"pixel clock is not enabled: 0x{state:08x}")

    rate = get_clock_value(
        qtest, RPI_FWREQ_GET_CLOCK_RATE, RPI_FIRMWARE_PIXEL_CLK_ID
    )
    min_rate = get_clock_value(
        qtest, RPI_FWREQ_GET_MIN_CLOCK_RATE, RPI_FIRMWARE_PIXEL_CLK_ID
    )
    max_rate = get_clock_value(
        qtest, RPI_FWREQ_GET_MAX_CLOCK_RATE, RPI_FIRMWARE_PIXEL_CLK_ID
    )
    if not rate or not min_rate or not max_rate:
        raise RuntimeError(
            "pixel clock returned a zero rate: "
            f"rate={rate} min={min_rate} max={max_rate}"
        )
    if min_rate > max_rate:
        raise RuntimeError(
            f"pixel clock range is inverted: min={min_rate} max={max_rate}"
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

    with tempfile.TemporaryDirectory(prefix="vc4-clock-smoke-") as temp_dir:
        qtest_path = Path(temp_dir) / "qtest.sock"
        command = (
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
        )
        process = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        qtest: QTestClient | None = None
        try:
            qtest = connect_when_ready(qtest_path, process)
            validate_clocks(qtest)
        finally:
            if qtest is not None:
                qtest.close()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

        if process.returncode not in (0, -15):
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(
                f"QEMU exited with status {process.returncode}:\n{stderr}"
            )

    print("Raspberry Pi firmware clock enumeration smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
