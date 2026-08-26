#!/usr/bin/env python3
"""Exercise Raspberry Pi firmware virtual-GPIO buffer registration."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType


RPI_FWREQ_FRAMEBUFFER_GET_GPIOVIRTBUF = 0x00040010
RPI_FWREQ_FRAMEBUFFER_SET_GPIOVIRTBUF = 0x00048020
FIRST_BUS_ADDRESS = 0xC0200000
SECOND_BUS_ADDRESS = 0x00204000


def load_support() -> ModuleType:
    path = Path(__file__).with_name("property-power-domain-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_property_support", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import property smoke support: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request(support: ModuleType, qtest: object, tag: int, value: int) -> int:
    response = support.property_words_request(qtest, tag, (value,), 4)
    if len(response) != 1:
        raise RuntimeError(f"tag 0x{tag:08x} returned {response!r}")
    return response[0]


def expect_get(support: ModuleType, qtest: object, expected: int) -> None:
    actual = request(
        support,
        qtest,
        RPI_FWREQ_FRAMEBUFFER_GET_GPIOVIRTBUF,
        0,
    )
    if actual != expected:
        raise RuntimeError(
            "virtual-GPIO GET returned "
            f"0x{actual:08x}, expected 0x{expected:08x}"
        )


def set_buffer(support: ModuleType, qtest: object, address: int) -> None:
    result = request(
        support,
        qtest,
        RPI_FWREQ_FRAMEBUFFER_SET_GPIOVIRTBUF,
        address,
    )
    if result != 0:
        raise RuntimeError(
            "virtual-GPIO SET did not acknowledge success: "
            f"0x{result:08x}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-aarch64"),
    )
    args = parser.parse_args()
    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    support = load_support()
    with tempfile.TemporaryDirectory(prefix="vc4-virtgpio-") as temp_dir:
        temp = Path(temp_dir)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
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
        qtest = None
        qmp = None
        try:
            qtest = support.connect_when_ready(
                qtest_path, process, support.QTestClient
            )
            qmp = support.connect_when_ready(
                qmp_path, process, support.QMPClient
            )

            expect_get(support, qtest, 0)
            set_buffer(support, qtest, FIRST_BUS_ADDRESS)
            expect_get(support, qtest, FIRST_BUS_ADDRESS)
            set_buffer(support, qtest, SECOND_BUS_ADDRESS)
            expect_get(support, qtest, SECOND_BUS_ADDRESS)

            qmp.execute("system_reset")
            expect_get(support, qtest, 0)
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

    print("BCM2835 firmware virtual-GPIO buffer smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
