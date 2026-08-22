#!/usr/bin/env python3
"""Exercise the Raspberry Pi firmware GET_CLOCKS enumeration."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import subprocess
import tempfile
from typing import Any


RPI_FWREQ_GET_CLOCKS = 0x00010007
EXPECTED_CLOCK_IDS = (3, 4, 5, 7, 9, 11, 13, 14, 15)


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


def get_clock_ids(support: Any, qtest: Any, pair_count: int) -> tuple[int, ...]:
    response = support.property_words_request(
        qtest,
        RPI_FWREQ_GET_CLOCKS,
        tuple(0 for _ in range(pair_count * 2)),
        pair_count * 2 * 4,
    )
    parents = tuple(response[0::2])
    if any(parent != 0 for parent in parents):
        raise RuntimeError(f"GET_CLOCKS returned unsupported parents: {parents!r}")
    return tuple(response[1::2])


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

    with tempfile.TemporaryDirectory(prefix="vc4-property-clocks-") as temp_dir:
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

            clock_ids = get_clock_ids(support, qtest, len(EXPECTED_CLOCK_IDS))
            if clock_ids != EXPECTED_CLOCK_IDS:
                raise RuntimeError(
                    f"unexpected GET_CLOCKS response: {clock_ids!r}; "
                    f"expected {EXPECTED_CLOCK_IDS!r}"
                )

            truncated_ids = get_clock_ids(support, qtest, 4)
            if truncated_ids != EXPECTED_CLOCK_IDS[:4]:
                raise RuntimeError(
                    f"unexpected truncated GET_CLOCKS response: {truncated_ids!r}"
                )

            if 9 not in clock_ids or 13 not in clock_ids:
                raise RuntimeError(
                    "GET_CLOCKS omitted the HDMI pixel or state-machine clock"
                )

            qmp.execute("system_reset")
            reset_ids = get_clock_ids(support, qtest, len(EXPECTED_CLOCK_IDS))
            if reset_ids != EXPECTED_CLOCK_IDS:
                raise RuntimeError(
                    f"GET_CLOCKS changed across reset: {reset_ids!r}"
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
        "Raspberry Pi firmware clock enumeration smoke test passed: "
        + ",".join(str(clock_id) for clock_id in EXPECTED_CLOCK_IDS)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
