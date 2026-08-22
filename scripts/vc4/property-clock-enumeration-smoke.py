#!/usr/bin/env python3
"""Verify the Raspberry Pi firmware GET_CLOCKS discovery contract."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import tempfile
import time

PERIPHERAL_BASE = 0x3F000000
MAILBOX_READ = PERIPHERAL_BASE + 0x0000B880
MAILBOX_WRITE = PERIPHERAL_BASE + 0x0000B8A0
PROPERTY_CHANNEL = 8
BUFFER = 0x00010000
GET_CLOCKS = 0x00010007
SUCCESS = 1 << 31
EXPECTED = (3, 4, 5, 7, 9, 11, 13, 14, 15, 16)


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.reader = self.sock.makefile("r", encoding="utf-8")

    def close(self) -> None:
        self.reader.close()
        self.sock.close()

    def command(self, command: str) -> list[str]:
        self.sock.sendall((command + "\n").encode())
        while True:
            line = self.reader.readline()
            if not line:
                raise RuntimeError(f"qtest closed during {command!r}")
            words = line.strip().split()
            if not words or words[0] == "IRQ":
                continue
            if words[0] != "OK":
                raise RuntimeError(f"qtest error: {line.strip()}")
            return words[1:]

    def writel(self, address: int, value: int) -> None:
        self.command(f"writel 0x{address:x} 0x{value & 0xffffffff:x}")

    def readl(self, address: int) -> int:
        values = self.command(f"readl 0x{address:x}")
        if len(values) != 1:
            raise RuntimeError(f"unexpected read response: {values!r}")
        return int(values[0], 0)


def connect(path: Path, process: subprocess.Popen[str]) -> QTest:
    deadline = time.monotonic() + 10
    last: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(
                f"QEMU exited with {process.returncode}:\n{stderr}"
            )
        try:
            return QTest(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last = error
            time.sleep(0.02)
    raise RuntimeError(f"qtest connection timed out: {last}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    args = parser.parse_args()
    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"missing QEMU binary: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-clocks-") as temp:
        qtest_path = Path(temp) / "qtest.sock"
        process = subprocess.Popen(
            (
                str(qemu), "-M", "raspi3b", "-accel", "qtest", "-S",
                "-display", "none", "-serial", "none",
                "-monitor", "none",
                "-qtest", f"unix:{qtest_path},server=on,wait=off",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        client: QTest | None = None
        try:
            client = connect(qtest_path, process)
            pair_capacity = 17
            payload_words = pair_capacity * 2
            payload_bytes = payload_words * 4
            words = (
                24 + payload_bytes, 0, GET_CLOCKS,
                payload_bytes, 0,
                *(0 for _ in range(payload_words)), 0,
            )
            for index, word in enumerate(words):
                client.writel(BUFFER + index * 4, word)
            client.writel(MAILBOX_WRITE, BUFFER | PROPERTY_CHANNEL)
            returned = client.readl(MAILBOX_READ)
            if returned != BUFFER | PROPERTY_CHANNEL:
                raise RuntimeError(f"wrong mailbox response: {returned:#x}")
            if client.readl(BUFFER + 4) != SUCCESS:
                raise RuntimeError("property request was not successful")
            length = client.readl(BUFFER + 16)
            expected_length = SUCCESS | (len(EXPECTED) * 8)
            if length != expected_length:
                raise RuntimeError(
                    f"response length {length:#x} != {expected_length:#x}"
                )
            pairs = tuple(
                (
                    client.readl(BUFFER + 20 + index * 8),
                    client.readl(BUFFER + 24 + index * 8),
                )
                for index in range(len(EXPECTED))
            )
            expected_pairs = tuple((0, clock) for clock in EXPECTED)
            if pairs != expected_pairs:
                raise RuntimeError(
                    f"clock pairs {pairs!r} != {expected_pairs!r}"
                )
        finally:
            if client is not None:
                client.close()
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    print("Raspberry Pi firmware clock enumeration smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
