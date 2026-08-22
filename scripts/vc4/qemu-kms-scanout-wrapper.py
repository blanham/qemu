#!/usr/bin/env python3
"""Wrap QEMU and capture a second QMP screendump stream for KMS tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any


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
        request: dict[str, Any] = {
            "execute": command,
            "id": command,
        }
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


def connect_qmp(path: Path, process: subprocess.Popen[bytes], timeout: float) -> QMPClient:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited before scanout QMP connected: {process.returncode}")
        try:
            return QMPClient(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
        time.sleep(0.05)
    raise RuntimeError(f"timed out connecting to scanout QMP: {last_error}")


def capture_frames(
    qmp_path: Path,
    output_dir: Path,
    process: subprocess.Popen[bytes],
    stop: threading.Event,
    interval: float,
) -> None:
    record: dict[str, Any] = {
        "qmp_path": str(qmp_path),
        "interval_seconds": interval,
        "frames": [],
        "errors": [],
    }
    client: QMPClient | None = None
    try:
        client = connect_qmp(qmp_path, process, timeout=15.0)
        index = 0
        while not stop.is_set() and process.poll() is None:
            frame = output_dir / f"frame-{index:04d}.ppm"
            started = time.monotonic()
            try:
                client.execute("screendump", {"filename": str(frame)})
                record["frames"].append(
                    {
                        "index": index,
                        "filename": frame.name,
                        "elapsed_seconds": started,
                        "size": frame.stat().st_size if frame.is_file() else None,
                    }
                )
            except (OSError, RuntimeError) as error:
                record["errors"].append(
                    {
                        "index": index,
                        "error": str(error),
                    }
                )
                if process.poll() is not None:
                    break
            index += 1
            stop.wait(interval)
    except (OSError, RuntimeError) as error:
        record["errors"].append({"stage": "connect", "error": str(error)})
    finally:
        if client is not None:
            try:
                client.close()
            except OSError:
                pass
        (output_dir / "qmp-capture.json").write_text(
            json.dumps(record, indent=2, sort_keys=True) + "\n"
        )


def main() -> int:
    real_qemu = os.environ.get("VC4_REAL_QEMU")
    output_value = os.environ.get("VC4_SCANOUT_DIR")
    interval = float(os.environ.get("VC4_SCANOUT_INTERVAL", "2.0"))
    if not real_qemu:
        print("VC4_REAL_QEMU is required", file=sys.stderr)
        return 2
    if not output_value:
        print("VC4_SCANOUT_DIR is required", file=sys.stderr)
        return 2
    if interval <= 0:
        print("VC4_SCANOUT_INTERVAL must be positive", file=sys.stderr)
        return 2

    output_dir = Path(output_value).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    qmp_path = output_dir / "scanout-qmp.sock"
    qmp_path.unlink(missing_ok=True)

    command = [
        str(Path(real_qemu).resolve()),
        *sys.argv[1:],
        "-qmp",
        f"unix:{qmp_path},server=on,wait=off",
    ]
    (output_dir / "qemu-command.json").write_text(
        json.dumps(command, indent=2) + "\n"
    )
    process = subprocess.Popen(command)
    stop = threading.Event()
    capture = threading.Thread(
        target=capture_frames,
        args=(qmp_path, output_dir, process, stop, interval),
        name="vc4-kms-scanout-capture",
        daemon=True,
    )
    capture.start()

    def forward(signum: int, _frame: object) -> None:
        if process.poll() is None:
            try:
                process.send_signal(signum)
            except ProcessLookupError:
                pass

    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, forward)

    try:
        return_code = process.wait()
    finally:
        stop.set()
        capture.join(timeout=max(5.0, interval * 2))
        if capture.is_alive():
            (output_dir / "capture-thread-timeout").write_text("1\n")
        qmp_path.unlink(missing_ok=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
