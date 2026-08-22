#!/usr/bin/env python3
"""Exercise the BCM2835 HVS primary-plane to QEMU-console bridge."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from typing import Any, BinaryIO


PERIPHERAL_BASE = 0x3F000000
HVS_BASE = PERIPHERAL_BASE + 0x00400000
FRAMEBUFFER_BASE = 0x00100000
WIDTH = 64
HEIGHT = 48
PITCH = WIDTH * 4
LIST_WORD = 0x100


class QTestClient:
    def __init__(self, path: Path) -> None:
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(str(path))
        self._reader = self._socket.makefile("r", encoding="utf-8")

    def close(self) -> None:
        self._reader.close()
        self._socket.close()

    def command(self, command: str) -> list[str]:
        self._socket.sendall((command + "\n").encode("utf-8"))
        while True:
            line = self._reader.readline()
            if not line:
                raise RuntimeError("qtest connection closed")
            words = line.strip().split()
            if not words or words[0] == "IRQ":
                continue
            if words[0] != "OK":
                raise RuntimeError(line.strip())
            return words[1:]

    def writel(self, address: int, value: int) -> None:
        self.command(f"writel 0x{address:x} 0x{value & 0xffffffff:x}")


class QMPClient:
    def __init__(self, path: Path) -> None:
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.connect(str(path))
        self._reader = self._socket.makefile("r", encoding="utf-8")
        greeting = self._read()
        if "QMP" not in greeting:
            raise RuntimeError(f"unexpected QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def close(self) -> None:
        self._reader.close()
        self._socket.close()

    def _read(self) -> dict[str, Any]:
        while True:
            line = self._reader.readline()
            if not line:
                raise RuntimeError("QMP connection closed")
            value = json.loads(line)
            if isinstance(value, dict):
                return value

    def execute(self, command: str, arguments: dict[str, Any] | None = None) -> Any:
        request: dict[str, Any] = {"execute": command, "id": command}
        if arguments:
            request["arguments"] = arguments
        self._socket.sendall((json.dumps(request) + "\n").encode("utf-8"))
        while True:
            response = self._read()
            if response.get("id") != command:
                continue
            if "error" in response:
                raise RuntimeError(f"QMP {command} failed: {response['error']!r}")
            return response.get("return")


def connect(path: Path, process: subprocess.Popen[str], constructor: type[Any]) -> Any:
    deadline = time.monotonic() + 10.0
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr else ""
            raise RuntimeError(f"QEMU exited before connection: {stderr}")
        try:
            return constructor(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
        time.sleep(0.02)
    raise RuntimeError(f"connection timed out: {last_error}")


def parse_model_contract(source: str) -> tuple[int, int]:
    dlist_match = re.search(
        r"#define\s+SCALER_DLIST_START_OFFSET\s+0x([0-9a-fA-F]+)",
        source,
    )
    displist_match = re.search(
        r"hvs_displist_offsets\[3\]\s*=\s*\{\s*"
        r"0x([0-9a-fA-F]+)",
        source,
        re.DOTALL,
    )
    if not dlist_match or not displist_match:
        raise RuntimeError("unable to parse HVS model register contract")
    return int(dlist_match.group(1), 16), int(displist_match.group(1), 16)


def expected_pixel(x: int, y: int) -> tuple[int, int, int]:
    red = x * 255 // (WIDTH - 1)
    green = y * 255 // (HEIGHT - 1)
    blue = 0xFF if ((x // 8) ^ (y // 8)) & 1 else 0x20
    return red, green, blue


def framebuffer_word(x: int, y: int) -> int:
    red, green, blue = expected_pixel(x, y)
    return (red << 16) | (green << 8) | blue


def read_token(stream: BinaryIO) -> bytes:
    token = bytearray()
    while True:
        byte = stream.read(1)
        if not byte:
            raise RuntimeError("short PPM header")
        if byte == b"#" and not token:
            stream.readline()
            continue
        if byte.isspace():
            if token:
                return bytes(token)
            continue
        token.extend(byte)


def verify_ppm(path: Path) -> None:
    with path.open("rb") as stream:
        if read_token(stream) != b"P6":
            raise RuntimeError("screendump is not P6 PPM")
        width = int(read_token(stream))
        height = int(read_token(stream))
        maximum = int(read_token(stream))
        if (width, height, maximum) != (WIDTH, HEIGHT, 255):
            raise RuntimeError(
                f"unexpected screendump geometry {width}x{height}/{maximum}"
            )
        pixels = stream.read(WIDTH * HEIGHT * 3)
    if len(pixels) != WIDTH * HEIGHT * 3:
        raise RuntimeError("short screendump payload")

    for x, y in (
        (0, 0),
        (WIDTH - 1, 0),
        (0, HEIGHT - 1),
        (WIDTH - 1, HEIGHT - 1),
        (WIDTH // 3, HEIGHT // 3),
        (WIDTH * 2 // 3, HEIGHT * 2 // 3),
    ):
        offset = (y * WIDTH + x) * 3
        actual = tuple(pixels[offset:offset + 3])
        expected = expected_pixel(x, y)
        if any(abs(actual[index] - expected[index]) > 2 for index in range(3)):
            raise RuntimeError(
                f"pixel {x},{y} is {actual!r}, expected {expected!r}"
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-aarch64"),
    )
    parser.add_argument(
        "--model-source",
        type=Path,
        default=Path("hw/display/bcm2835_hvs.c"),
    )
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    model_source = args.model_source.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")
    if not model_source.is_file():
        parser.error(f"HVS model source does not exist: {model_source}")
    dlist_start, displist0 = parse_model_contract(model_source.read_text())

    with tempfile.TemporaryDirectory(prefix="vc4-hvs-scanout-") as temp_value:
        temp = Path(temp_value)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
        screenshot = temp / "scanout.ppm"
        process = subprocess.Popen(
            (
                str(qemu),
                "-M", "raspi3b",
                "-accel", "qtest",
                "-S",
                "-display", "none",
                "-serial", "none",
                "-monitor", "none",
                "-qtest", f"unix:{qtest_path},server=on,wait=off",
                "-qmp", f"unix:{qmp_path},server=on,wait=off",
            ),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        qtest: QTestClient | None = None
        qmp: QMPClient | None = None
        try:
            qtest = connect(qtest_path, process, QTestClient)
            qmp = connect(qmp_path, process, QMPClient)

            for y in range(HEIGHT):
                for x in range(WIDTH):
                    qtest.writel(
                        FRAMEBUFFER_BASE + y * PITCH + x * 4,
                        framebuffer_word(x, y),
                    )

            list_address = HVS_BASE + dlist_start + LIST_WORD * 4
            qtest.writel(list_address + 0, 1)
            qtest.writel(list_address + 4, (HEIGHT << 16) | WIDTH)
            qtest.writel(list_address + 8, FRAMEBUFFER_BASE)
            qtest.writel(list_address + 12, PITCH)
            qtest.writel(HVS_BASE + displist0, LIST_WORD)

            qmp.execute("screendump", {"filename": str(screenshot)})
            verify_ppm(screenshot)
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

    print("BCM2835 HVS primary scanout smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
