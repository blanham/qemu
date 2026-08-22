#!/usr/bin/env python3
"""Exercise Raspberry Pi firmware legacy and modern power-state tags."""

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
MAILBOX_READ = RPI3_PERIPHERAL_BASE + 0x0000B880
MAILBOX_WRITE = RPI3_PERIPHERAL_BASE + 0x0000B8A0
MAILBOX_PROPERTY_CHANNEL = 8
PROPERTY_BUFFER = 0x00010000
PROPERTY_RESPONSE_SUCCESS = 1 << 31
PROPERTY_RESPONSE_LENGTH = (1 << 31) | 8

RPI_FWREQ_GET_POWER_STATE = 0x00020001
RPI_FWREQ_SET_POWER_STATE = 0x00028001
RPI_FWREQ_GET_DOMAIN_STATE = 0x00030030
RPI_FWREQ_SET_DOMAIN_STATE = 0x00038030
RPI_FWREQ_GET_GPIO_STATE = 0x00030041
RPI_FWREQ_GET_GPIO_CONFIG = 0x00030043
RPI_FWREQ_SET_GPIO_STATE = 0x00038041
RPI_FWREQ_SET_GPIO_CONFIG = 0x00038043

LEGACY_V3D_POWER_ID = 10
EXP_GPIO_BASE = 128
EXP_GPIO_COUNT = 8
EXP_GPIO_HDMI_HPD = 4
MODERN_V3D_DOMAIN_ID = 11


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
        request = {
            "execute": command,
            "id": command,
        }
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
        except FileNotFoundError as error:
            last_error = error
        except ConnectionRefusedError as error:
            last_error = error
        time.sleep(0.02)
    raise RuntimeError(f"timed out connecting to {path}: {last_error}")


def property_words_request(
    qtest: QTestClient,
    tag: int,
    payload: tuple[int, ...],
    expected_response_size: int,
) -> list[int]:
    if expected_response_size % 4:
        raise ValueError("property responses must contain whole words")

    buffer_size = len(payload) * 4
    words = (
        24 + buffer_size,
        0,
        tag,
        buffer_size,
        0,
        *payload,
        0,
    )
    for index, word in enumerate(words):
        qtest.writel(PROPERTY_BUFFER + index * 4, word)

    qtest.writel(
        MAILBOX_WRITE,
        PROPERTY_BUFFER | MAILBOX_PROPERTY_CHANNEL,
    )
    response_address = qtest.readl(MAILBOX_READ)
    expected_address = PROPERTY_BUFFER | MAILBOX_PROPERTY_CHANNEL
    if response_address != expected_address:
        raise RuntimeError(
            "mailbox returned the wrong property buffer: "
            f"0x{response_address:08x} != 0x{expected_address:08x}"
        )

    response_code = qtest.readl(PROPERTY_BUFFER + 4)
    if response_code != PROPERTY_RESPONSE_SUCCESS:
        raise RuntimeError(
            f"property request failed: response=0x{response_code:08x}"
        )
    response_length = qtest.readl(PROPERTY_BUFFER + 16)
    expected_length = (1 << 31) | expected_response_size
    if response_length != expected_length:
        raise RuntimeError(
            "property tag returned the wrong response length: "
            f"0x{response_length:08x} != 0x{expected_length:08x}"
        )

    return [
        qtest.readl(PROPERTY_BUFFER + 20 + index * 4)
        for index in range(expected_response_size // 4)
    ]


def property_request(
    qtest: QTestClient,
    tag: int,
    resource_id: int,
    state: int,
) -> int:
    response = property_words_request(
        qtest, tag, (resource_id, state), 8
    )
    returned_id, returned_state = response
    if returned_id != resource_id:
        raise RuntimeError(
            f"property tag changed resource id {resource_id} to {returned_id}"
        )
    return returned_state


def exp_gpio_get_config(
    qtest: QTestClient, offset: int
) -> tuple[int, int, int, int]:
    response = property_words_request(
        qtest,
        RPI_FWREQ_GET_GPIO_CONFIG,
        (EXP_GPIO_BASE + offset, 0, 0, 0, 0),
        20,
    )
    if response[0] != 0:
        raise RuntimeError(
            f"failed to read expander GPIO {offset} config: {response!r}"
        )
    return tuple(response[1:])


def exp_gpio_set_config(
    qtest: QTestClient,
    offset: int,
    direction: int,
    polarity: int,
    term_en: int,
    term_pull_up: int,
    state: int,
) -> None:
    response = property_words_request(
        qtest,
        RPI_FWREQ_SET_GPIO_CONFIG,
        (
            EXP_GPIO_BASE + offset,
            direction,
            polarity,
            term_en,
            term_pull_up,
            state,
        ),
        24,
    )
    if response[0] != 0:
        raise RuntimeError(
            f"failed to write expander GPIO {offset} config: {response!r}"
        )


def exp_gpio_get_state(qtest: QTestClient, offset: int) -> int:
    response = property_words_request(
        qtest,
        RPI_FWREQ_GET_GPIO_STATE,
        (EXP_GPIO_BASE + offset, 0),
        8,
    )
    if response[0] != 0:
        raise RuntimeError(
            f"failed to read expander GPIO {offset} state: {response!r}"
        )
    return response[1]


def exp_gpio_set_state(
    qtest: QTestClient, offset: int, state: int
) -> None:
    response = property_words_request(
        qtest,
        RPI_FWREQ_SET_GPIO_STATE,
        (EXP_GPIO_BASE + offset, state),
        8,
    )
    if response[0] != 0 or response[1] != state:
        raise RuntimeError(
            f"failed to write expander GPIO {offset} state: {response!r}"
        )
def expect_state(
    qtest: QTestClient,
    tag: int,
    resource_id: int,
    requested: int,
    expected: int,
) -> None:
    actual = property_request(qtest, tag, resource_id, requested)
    if actual != expected:
        raise RuntimeError(
            f"tag 0x{tag:08x} resource {resource_id} returned {actual}, "
            f"expected {expected}"
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

    with tempfile.TemporaryDirectory(prefix="vc4-power-domain-") as temp_dir:
        temp = Path(temp_dir)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
        command = (
            str(qemu),
            "-M", "raspi3b",
            "-accel", "qtest",
            "-S",
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

            expect_state(
                qtest,
                RPI_FWREQ_GET_DOMAIN_STATE,
                MODERN_V3D_DOMAIN_ID,
                0xFFFFFFFF,
                0,
            )
            expect_state(
                qtest,
                RPI_FWREQ_SET_DOMAIN_STATE,
                MODERN_V3D_DOMAIN_ID,
                1,
                1,
            )
            expect_state(
                qtest,
                RPI_FWREQ_GET_DOMAIN_STATE,
                MODERN_V3D_DOMAIN_ID,
                0xFFFFFFFF,
                1,
            )

            expect_state(
                qtest,
                RPI_FWREQ_SET_POWER_STATE,
                LEGACY_V3D_POWER_ID,
                1,
                1,
            )
            expect_state(
                qtest,
                RPI_FWREQ_GET_POWER_STATE,
                LEGACY_V3D_POWER_ID,
                0xFFFFFFFF,
                1,
            )

            qmp.execute("system_reset")
            expect_state(
                qtest,
                RPI_FWREQ_GET_DOMAIN_STATE,
                MODERN_V3D_DOMAIN_ID,
                0xFFFFFFFF,
                0,
            )
            expect_state(
                qtest,
                RPI_FWREQ_GET_POWER_STATE,
                LEGACY_V3D_POWER_ID,
                0xFFFFFFFF,
                0,
            )

            expect_state(
                qtest,
                RPI_FWREQ_SET_DOMAIN_STATE,
                32,
                1,
                0,
            )


            for offset in range(EXP_GPIO_COUNT):
                config = exp_gpio_get_config(qtest, offset)
                if config != (0, 0, 0, 0):
                    raise RuntimeError(
                        f"expander GPIO {offset} reset config is {config!r}"
                    )
                if exp_gpio_get_state(qtest, offset) != 0:
                    raise RuntimeError(
                        f"expander GPIO {offset} reset state is not low"
                    )

            exp_gpio_set_config(
                qtest,
                EXP_GPIO_HDMI_HPD,
                direction=1,
                polarity=1,
                term_en=1,
                term_pull_up=0,
                state=1,
            )
            if exp_gpio_get_config(qtest, EXP_GPIO_HDMI_HPD) != (1, 1, 1, 0):
                raise RuntimeError("expander GPIO config did not persist")
            if exp_gpio_get_state(qtest, EXP_GPIO_HDMI_HPD) != 1:
                raise RuntimeError("expander GPIO configured state did not persist")

            exp_gpio_set_state(qtest, EXP_GPIO_HDMI_HPD, 0)
            if exp_gpio_get_state(qtest, EXP_GPIO_HDMI_HPD) != 0:
                raise RuntimeError("expander GPIO state update did not persist")

            invalid = property_words_request(
                qtest,
                RPI_FWREQ_GET_GPIO_CONFIG,
                (EXP_GPIO_BASE - 1, 0, 0, 0, 0),
                20,
            )
            if invalid[0] == 0:
                raise RuntimeError("invalid expander GPIO was accepted")

            qmp.execute("system_reset")
            if exp_gpio_get_config(qtest, EXP_GPIO_HDMI_HPD) != (0, 0, 0, 0):
                raise RuntimeError("expander GPIO config survived reset")
            if exp_gpio_get_state(qtest, EXP_GPIO_HDMI_HPD) != 0:
                raise RuntimeError("expander GPIO state survived reset")
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

    print("Raspberry Pi firmware power-domain and expander GPIO smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
