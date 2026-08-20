#!/usr/bin/env python3
"""Boot a pinned Raspberry Pi AArch64 Linux image on the heterogeneous VC4 machine.

This is an independent control for the ARM/Linux/framebuffer side of the
machine.  It bypasses only bootcode.bin/start.elf and therefore must not be
used as evidence that the stock firmware handoff works.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from typing import Any


DEFAULT_APPEND = " ".join(
    (
        "console=ttyAMA0,115200",
        "earlycon=pl011,mmio32,0x3f201000",
        "rdinit=/init",
        "root=/dev/ram0",
        "rw",
        "loglevel=8",
        "ignore_loglevel",
        "panic=-1",
        "printk.time=1",
    )
)


class QMPClient:
    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.reader = None
        self.next_id = 1

    def connect(self) -> None:
        deadline = time.monotonic() + self.timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(1.0)
                sock.connect(str(self.path))
                self.sock = sock
                self.reader = sock.makefile("r", encoding="utf-8")
                greeting = self._read_message(deadline)
                if "QMP" not in greeting:
                    raise RuntimeError(f"invalid QMP greeting: {greeting!r}")
                self.command("qmp_capabilities")
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.05)
        raise TimeoutError(f"could not connect to QMP socket: {last_error}")

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
            self.reader = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _read_message(self, deadline: float) -> dict[str, Any]:
        if self.reader is None:
            raise RuntimeError("QMP is not connected")
        while time.monotonic() < deadline:
            line = self.reader.readline()
            if not line:
                raise EOFError("QMP socket closed")
            message = json.loads(line)
            if isinstance(message, dict):
                return message
        raise TimeoutError("timed out waiting for QMP response")

    def command(self, execute: str, arguments: dict[str, Any] | None = None,
                timeout: float = 10.0) -> Any:
        if self.sock is None:
            raise RuntimeError("QMP is not connected")
        command_id = self.next_id
        self.next_id += 1
        request: dict[str, Any] = {"execute": execute, "id": command_id}
        if arguments:
            request["arguments"] = arguments
        self.sock.sendall((json.dumps(request) + "\r\n").encode("utf-8"))
        deadline = time.monotonic() + timeout
        while True:
            message = self._read_message(deadline)
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise RuntimeError(f"QMP {execute} failed: {message['error']}")
            return message.get("return")


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    index = 0

    def token() -> bytes:
        nonlocal index
        while index < len(data):
            if data[index:index + 1] == b"#":
                newline = data.find(b"\n", index)
                if newline < 0:
                    raise ValueError("unterminated PPM comment")
                index = newline + 1
            elif data[index:index + 1].isspace():
                index += 1
            else:
                break
        start = index
        while index < len(data) and not data[index:index + 1].isspace():
            index += 1
        if start == index:
            raise ValueError("truncated PPM header")
        return data[start:index]

    if token() != b"P6":
        raise ValueError("screendump is not a binary PPM")
    width = int(token())
    height = int(token())
    maximum = int(token())
    if maximum != 255:
        raise ValueError(f"unsupported PPM maximum {maximum}")
    while index < len(data) and data[index:index + 1].isspace():
        index += 1
    pixels = data[index:]
    expected = width * height * 3
    if len(pixels) != expected:
        raise ValueError(
            f"PPM pixel payload is {len(pixels)} bytes, expected {expected}"
        )
    return width, height, pixels


def sample_rgb(width: int, height: int, pixels: bytes,
               x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(pixels[offset:offset + 3])  # type: ignore[return-value]


def classify_colour(rgb: tuple[int, int, int]) -> str:
    red, green, blue = rgb
    if red > 160 and green < 110 and blue < 110:
        return "red"
    if green > 160 and red < 110 and blue < 110:
        return "green"
    if blue > 160 and red < 110 and green < 110:
        return "blue"
    if red > 170 and green > 170 and blue > 170:
        return "white"
    return "other"


def collect_cpu_state(qmp: QMPClient) -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        result["query_status"] = qmp.command("query-status")
    except Exception as exc:  # diagnostics must not hide the main result
        result["query_status_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["query_cpus_fast"] = qmp.command("query-cpus-fast")
    except Exception as exc:
        result["query_cpus_fast_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["info_registers"] = qmp.command(
            "human-monitor-command",
            {"command-line": "info registers"},
        )
    except Exception as exc:
        result["info_registers_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("kernel", type=Path)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("initramfs", type=Path)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--append", default=DEFAULT_APPEND)
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    kernel = args.kernel.resolve()
    dtb = args.dtb.resolve()
    initramfs = args.initramfs.resolve()
    out_dir = args.out_dir.resolve()
    for label, path in (
        ("QEMU", qemu),
        ("kernel", kernel),
        ("DTB", dtb),
        ("initramfs", initramfs),
    ):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vc4-direct-linux-") as temp_name:
        temp = Path(temp_name)
        qmp_path = temp / "qmp.sock"
        serial_path = out_dir / "serial.log"
        stderr_path = out_dir / "qemu.stderr"
        screen_path = out_dir / "screen.ppm"

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",
            "-m", "1G",
            "-kernel", str(kernel),
            "-dtb", str(dtb),
            "-initrd", str(initramfs),
            "-append", args.append,
            "-display", "none",
            "-monitor", "none",
            "-serial", f"file:{serial_path}",
            "-no-reboot",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
        ]
        (out_dir / "command.json").write_text(
            json.dumps(command, indent=2) + "\n",
            encoding="utf-8",
        )

        with stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
            )
            qmp = QMPClient(qmp_path)
            start = time.monotonic()
            qmp_error = None
            screen_error = None
            try:
                qmp.connect()
                deadline = start + args.seconds
                while time.monotonic() < deadline and process.poll() is None:
                    serial = (
                        serial_path.read_text(
                            encoding="utf-8", errors="replace"
                        ) if serial_path.is_file() else ""
                    )
                    if "VC4_LINUX_FB_OK" in serial:
                        break
                    time.sleep(0.25)
                try:
                    qmp.command(
                        "screendump",
                        {"filename": str(screen_path)},
                        timeout=15.0,
                    )
                except Exception as exc:
                    screen_error = f"{type(exc).__name__}: {exc}"
                cpu_state = collect_cpu_state(qmp)
            except Exception as exc:
                qmp_error = f"{type(exc).__name__}: {exc}"
                cpu_state = {}
            finally:
                try:
                    if qmp.sock is not None:
                        qmp.command("quit", timeout=2.0)
                except Exception:
                    pass
                qmp.close()
                try:
                    process.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=3.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=3.0)

        elapsed = time.monotonic() - start
        serial = (
            serial_path.read_text(encoding="utf-8", errors="replace")
            if serial_path.is_file() else ""
        )
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        linux_version_seen = bool(re.search(r"Linux version\s+\S+", serial))
        init_seen = "VC4_LINUX_INIT_START" in serial
        framebuffer_marker_seen = "VC4_LINUX_FB_OK" in serial

        screen: dict[str, Any] = {
            "present": screen_path.is_file(),
            "error": screen_error,
        }
        scanout_passed = False
        if screen_path.is_file():
            try:
                width, height, pixels = read_ppm(screen_path)
                coordinates = {
                    "top_left": (width // 4, height // 4),
                    "top_right": (3 * width // 4, height // 4),
                    "bottom_left": (width // 4, 3 * height // 4),
                    "bottom_right": (3 * width // 4, 3 * height // 4),
                }
                samples = {
                    name: sample_rgb(width, height, pixels, *coordinate)
                    for name, coordinate in coordinates.items()
                }
                classes = {
                    name: classify_colour(rgb)
                    for name, rgb in samples.items()
                }
                scanout_passed = list(classes.values()) == [
                    "red", "green", "blue", "white"
                ]
                screen.update({
                    "width": width,
                    "height": height,
                    "samples": samples,
                    "classes": classes,
                    "scanout_passed": scanout_passed,
                })
            except Exception as exc:
                screen["parse_error"] = f"{type(exc).__name__}: {exc}"

        passed = init_seen and framebuffer_marker_seen and scanout_passed
        result = {
            "schema_version": 1,
            "passed": passed,
            "elapsed_seconds": elapsed,
            "qemu_returncode": process.returncode,
            "qmp_error": qmp_error,
            "linux_version_seen": linux_version_seen,
            "init_seen": init_seen,
            "framebuffer_marker_seen": framebuffer_marker_seen,
            "scanout_passed": scanout_passed,
            "append": args.append,
            "screen": screen,
            "cpu_state": cpu_state,
            "serial_tail": serial.splitlines()[-400:],
            "stderr_tail": stderr.splitlines()[-240:],
        }
        (out_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(json.dumps({
            key: result[key]
            for key in (
                "passed",
                "linux_version_seen",
                "init_seen",
                "framebuffer_marker_seen",
                "scanout_passed",
                "qemu_returncode",
            )
        }, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
