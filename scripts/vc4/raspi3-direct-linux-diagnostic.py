#!/usr/bin/env python3
"""Boot a real Raspberry Pi 3 Linux image on the heterogeneous VC4 machine.

This is deliberately independent of raspi3-linux-probe.py.  It treats the
serial transcript as the primary boot frontier, captures QMP CPU/register
state, and verifies the deterministic four-quadrant framebuffer through an
actual QEMU screendump when the initramfs reports success.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import time
from typing import Any


LINUX_BANNER = "Linux version "
INIT_MARKER = "VC4_LINUX_INIT_START"
FB_MARKER = "VC4_LINUX_FB_OK"
DEFAULT_APPEND = " ".join(
    (
        "console=ttyAMA0,115200",
        "earlycon=pl011,mmio32,0x3f201000",
        "keep_bootcon",
        "ignore_loglevel",
        "loglevel=8",
        "printk.time=1",
        "rdinit=/init",
        "root=/dev/ram0",
        "rw",
        "panic=-1",
    )
)


class QMPClient:
    def __init__(self, path: Path, timeout: float = 3.0) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.socket.connect(str(path))
        self.file = self.socket.makefile("rwb", buffering=0)
        self._read_response()
        self.execute("qmp_capabilities")

    def close(self) -> None:
        self.file.close()
        self.socket.close()

    def _read_response(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise ConnectionError("QMP socket closed")
            message = json.loads(line)
            if "event" in message:
                continue
            return message

    def execute(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        command: dict[str, Any] = {"execute": name}
        if arguments:
            command["arguments"] = arguments
        self.file.write(json.dumps(command).encode("utf-8") + b"\n")
        response = self._read_response()
        if "error" in response:
            raise RuntimeError(f"QMP {name}: {response['error']}")
        return response.get("return")


def connect_qmp(path: Path, process: subprocess.Popen[bytes], timeout: float) -> QMPClient:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited with status {process.returncode}")
        try:
            return QMPClient(path)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout) as exc:
            last_error = exc
            time.sleep(0.025)
    raise TimeoutError(f"could not connect to QMP socket: {last_error}")


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    offset = 0

    def token() -> bytes:
        nonlocal offset
        while offset < len(data):
            if data[offset:offset + 1] == b"#":
                end = data.find(b"\n", offset)
                offset = len(data) if end < 0 else end + 1
            elif data[offset:offset + 1].isspace():
                offset += 1
            else:
                break
        start = offset
        while offset < len(data) and not data[offset:offset + 1].isspace():
            offset += 1
        if start == offset:
            raise ValueError("truncated PPM header")
        return data[start:offset]

    magic = token()
    width = int(token())
    height = int(token())
    maximum = int(token())
    while offset < len(data) and data[offset:offset + 1].isspace():
        offset += 1
    if maximum != 255:
        raise ValueError(f"unsupported PPM maximum {maximum}")
    if magic == b"P6":
        pixels = data[offset:]
    elif magic == b"P3":
        values = [int(value) for value in data[offset:].split()]
        pixels = bytes(values)
    else:
        raise ValueError(f"unsupported PPM magic {magic!r}")
    expected = width * height * 3
    if len(pixels) < expected:
        raise ValueError(f"truncated PPM pixels: {len(pixels)} < {expected}")
    return width, height, pixels[:expected]


def pixel(pixels: bytes, width: int, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(pixels[offset:offset + 3])  # type: ignore[return-value]


def close_to(actual: tuple[int, int, int], expected: tuple[int, int, int]) -> bool:
    return all(abs(left - right) <= 8 for left, right in zip(actual, expected))


def classify(serial: str, screen_passed: bool) -> str:
    if FB_MARKER in serial and screen_passed:
        return "linux-framebuffer-scanout"
    if FB_MARKER in serial:
        return "linux-fb-painted-scanout-blocked"
    if INIT_MARKER in serial:
        return "linux-init-framebuffer-blocked"
    if LINUX_BANNER in serial:
        return "linux-early-boot"
    if serial.strip():
        return "arm-entered-no-linux-banner"
    return "silent-linux-entry"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=180.0)
    parser.add_argument("--append", default=DEFAULT_APPEND)
    args = parser.parse_args()

    inputs = {
        "qemu": args.qemu.resolve(),
        "kernel": args.kernel.resolve(),
        "dtb": args.dtb.resolve(),
        "initrd": args.initrd.resolve(),
    }
    for label, path in inputs.items():
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    qmp_path = out_dir / "qmp.sock"
    serial_path = out_dir / "serial.log"
    stderr_path = out_dir / "qemu.stderr"
    screen_path = out_dir / "screendump.ppm"
    for path in (qmp_path, serial_path, stderr_path, screen_path):
        path.unlink(missing_ok=True)

    command = [
        str(inputs["qemu"]),
        "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",
        "-m", "1G",
        "-accel", "tcg,thread=single",
        "-kernel", str(inputs["kernel"]),
        "-dtb", str(inputs["dtb"]),
        "-initrd", str(inputs["initrd"]),
        "-append", args.append,
        "-display", "none",
        "-monitor", "none",
        "-serial", f"file:{serial_path}",
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-no-reboot",
    ]
    (out_dir / "command.json").write_text(
        json.dumps(command, indent=2) + "\n", encoding="utf-8"
    )

    process: subprocess.Popen[bytes] | None = None
    qmp: QMPClient | None = None
    probe_error: str | None = None
    cpu_snapshot: Any = None
    registers: Any = None
    screen_error: str | None = None
    screen_samples: dict[str, tuple[int, int, int]] = {}
    screen_passed = False
    start = time.monotonic()

    with stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
            qmp = connect_qmp(qmp_path, process, 8.0)
            deadline = start + args.seconds
            last_snapshot = 0.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    break
                serial = read_text(serial_path)
                now = time.monotonic()
                if now - last_snapshot >= 10.0:
                    last_snapshot = now
                    try:
                        cpu_snapshot = qmp.execute("query-cpus-fast")
                        registers = qmp.execute(
                            "human-monitor-command",
                            {"command-line": "info registers"},
                        )
                    except Exception as exc:
                        registers = f"snapshot failed: {exc}"
                if FB_MARKER in serial:
                    time.sleep(0.25)
                    break
                time.sleep(0.05)

            serial = read_text(serial_path)
            try:
                qmp.execute("screendump", {"filename": str(screen_path)})
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline and not screen_path.is_file():
                    time.sleep(0.025)
                width, height, pixels = parse_ppm(screen_path)
                points = {
                    "red": (width // 4, height // 4, (255, 0, 0)),
                    "green": (3 * width // 4, height // 4, (0, 255, 0)),
                    "blue": (width // 4, 3 * height // 4, (0, 0, 255)),
                    "white": (3 * width // 4, 3 * height // 4, (255, 255, 255)),
                }
                checks = []
                for name, (x, y, expected) in points.items():
                    actual = pixel(pixels, width, x, y)
                    screen_samples[name] = actual
                    checks.append(close_to(actual, expected))
                screen_passed = all(checks)
            except Exception as exc:
                screen_error = f"{type(exc).__name__}: {exc}"
        except Exception as exc:
            probe_error = f"{type(exc).__name__}: {exc}"
        finally:
            if qmp is not None:
                try:
                    cpu_snapshot = qmp.execute("query-cpus-fast")
                    registers = qmp.execute(
                        "human-monitor-command",
                        {"command-line": "info registers"},
                    )
                except Exception:
                    pass
                try:
                    qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            if process is not None:
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    try:
                        process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2.0)

    serial = read_text(serial_path)
    classification = classify(serial, screen_passed)
    result = {
        "schema_version": 1,
        "passed": classification == "linux-framebuffer-scanout",
        "classification": classification,
        "elapsed_seconds": time.monotonic() - start,
        "probe_error": probe_error,
        "qemu_returncode": process.returncode if process is not None else None,
        "linux_version_seen": LINUX_BANNER in serial,
        "init_seen": INIT_MARKER in serial,
        "fb_marker_seen": FB_MARKER in serial,
        "screendump_pixels_match": screen_passed,
        "screendump_error": screen_error,
        "screendump_samples": {
            key: list(value) for key, value in screen_samples.items()
        },
        "cpu_snapshot": cpu_snapshot,
        "registers": registers,
        "serial_tail": serial.splitlines()[-1000:],
        "qemu_stderr_tail": read_text(stderr_path).splitlines()[-500:],
        "append": args.append,
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
