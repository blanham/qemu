#!/usr/bin/env python3
"""Verify a bare ARM payload clears a live Pi framebuffer through VC4 V3D."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import time
from typing import Any


STATUS_ADDRESS = 0x00001000
STATUS_SIGNATURE = 0x5643345F5633444F  # "VC4_V3DO"
FAILURE_SIGNATURE = 0x5643345F56334446  # "VC4_V3DF"
EXPECTED_IDENT0 = 0x02443356
EXPECTED_WIDTH = 512
EXPECTED_HEIGHT = 512
EXPECTED_BORDER_WORD = 0x00FF0000
EXPECTED_CENTER_WORD = 0x0000FFFF
SERIAL_SUCCESS = "VC4_BARE_V3D_OK"
SERIAL_FAILURE = "VC4_BARE_V3D_FAILED"


class QMP:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)
        greeting = self._read_message()
        if "QMP" not in greeting:
            raise RuntimeError(f"invalid QMP greeting: {greeting!r}")
        self.execute("qmp_capabilities")

    def _read_message(self) -> dict[str, Any]:
        while True:
            line = self.file.readline()
            if not line:
                raise RuntimeError("QMP socket closed")
            message = json.loads(line)
            if "event" not in message:
                return message

    def execute(self, command: str,
                arguments: dict[str, Any] | None = None) -> Any:
        request: dict[str, Any] = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self.file.write(json.dumps(request).encode("utf-8") + b"\n")
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, command: str) -> list[str]:
        self.file.write(command.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest socket closed during {command!r}")
        fields = reply.decode("ascii", errors="replace").strip().split()
        if not fields or fields[0] != "OK":
            raise RuntimeError(f"qtest rejected {command!r}: {fields!r}")
        return fields

    def readl(self, address: int) -> int:
        fields = self.command(f"readl 0x{address:x}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)

    def readq(self, address: int) -> int:
        fields = self.command(f"readq 0x{address:x}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_socket(path: Path, proc: subprocess.Popen[bytes], kind: str,
                    timeout: float = 20.0) -> QMP | QTest:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"QEMU exited before {kind} connected "
                f"(status {proc.returncode})"
            )
        try:
            return QMP(path) if kind == "QMP" else QTest(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.02)
    raise TimeoutError(f"{kind} socket was not ready: {path}") from last_error


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    position = 0
    tokens: list[bytes] = []

    while len(tokens) < 4:
        while position < len(data) and data[position] in b" \t\r\n":
            position += 1
        if position >= len(data):
            raise ValueError("truncated PPM header")
        if data[position] == ord("#"):
            newline = data.find(b"\n", position)
            if newline < 0:
                raise ValueError("unterminated PPM comment")
            position = newline + 1
            continue
        end = position
        while end < len(data) and data[end] not in b" \t\r\n":
            end += 1
        tokens.append(data[position:end])
        position = end

    if tokens[0] != b"P6":
        raise ValueError(f"unsupported PPM magic: {tokens[0]!r}")
    width = int(tokens[1])
    height = int(tokens[2])
    maximum = int(tokens[3])
    if width <= 0 or height <= 0 or maximum != 255:
        raise ValueError(
            f"unsupported PPM geometry {width}x{height}, max={maximum}"
        )
    while position < len(data) and data[position] in b" \t\r\n":
        position += 1
    pixels = data[position:position + width * height * 3]
    if len(pixels) != width * height * 3:
        raise ValueError("truncated PPM pixel data")
    return width, height, pixels


def average_rgb(width: int, height: int, pixels: bytes,
                x: int, y: int, radius: int = 4) -> tuple[int, int, int]:
    totals = [0, 0, 0]
    count = 0
    for sample_y in range(max(0, y - radius), min(height, y + radius + 1)):
        for sample_x in range(max(0, x - radius), min(width, x + radius + 1)):
            offset = (sample_y * width + sample_x) * 3
            totals[0] += pixels[offset]
            totals[1] += pixels[offset + 1]
            totals[2] += pixels[offset + 2]
            count += 1
    return tuple(value // count for value in totals)


def blue_matches(color: tuple[int, int, int]) -> bool:
    red, green, blue = color
    return blue >= 150 and blue >= red + 70 and blue >= green + 70


def yellow_matches(color: tuple[int, int, int]) -> bool:
    red, green, blue = color
    return red >= 150 and green >= 150 and blue <= 100


def build_command(qemu: Path, kernel: Path, temp: Path,
                  serial_path: Path, stderr_path: Path) -> list[str]:
    return [
        str(qemu.resolve()),
        "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",
        "-m", "1G",
        "-smp", "5",
        "-kernel", str(kernel.resolve()),
        "-accel", "tcg,thread=single",
        "-display", "none",
        "-monitor", "none",
        "-serial", f"file:{serial_path}",
        "-serial", "none",
        "-no-reboot",
        "-d", "guest_errors,unimp",
        "-D", str(stderr_path),
        "-qmp", f"unix:{temp / 'qmp.sock'},server=on,wait=off",
        "-qtest", f"unix:{temp / 'qtest.sock'},server=on,wait=off",
    ]


def run_probe(args: argparse.Namespace) -> dict[str, Any]:
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    serial_path = out_dir / "serial.log"
    qemu_log_path = out_dir / "qemu.log"
    host_stderr_path = out_dir / "qemu-host.stderr"
    screenshot_path = out_dir / "v3d-clear.ppm"

    for path in (serial_path, qemu_log_path, host_stderr_path,
                 screenshot_path):
        path.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory(prefix="vc4-v3d-scanout-") as temp_s:
        temp = Path(temp_s)
        command = build_command(
            args.qemu, args.kernel, temp, serial_path, qemu_log_path
        )
        start = time.monotonic()
        with host_stderr_path.open("wb") as host_stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=host_stderr,
            )

        qmp: QMP | None = None
        qtest: QTest | None = None
        failure: str | None = None
        status_words: list[int] = []
        serial = ""
        try:
            qmp = wait_for_socket(temp / "qmp.sock", proc, "QMP")
            qtest = wait_for_socket(temp / "qtest.sock", proc, "qtest")
            deadline = time.monotonic() + args.seconds
            next_sample = 0.0
            while time.monotonic() < deadline:
                now = time.monotonic()
                if proc.poll() is not None:
                    failure = (
                        f"QEMU exited during probe with status "
                        f"{proc.returncode}"
                    )
                    break
                if now >= next_sample:
                    signature = qtest.readq(STATUS_ADDRESS)
                    serial = read_text(serial_path)
                    if signature in (STATUS_SIGNATURE, FAILURE_SIGNATURE):
                        status_words = [
                            qtest.readq(STATUS_ADDRESS + index * 8)
                            for index in range(8)
                        ]
                        break
                    if SERIAL_FAILURE in serial:
                        failure = "guest reported VC4_BARE_V3D_FAILED"
                        break
                    next_sample = now + args.sample_interval
                time.sleep(0.02)
            else:
                failure = "timed out before the V3D status signature"

            serial = read_text(serial_path)
            if status_words and status_words[0] == STATUS_SIGNATURE:
                qmp.execute("screendump", {"filename": str(screenshot_path)})
                # The command is synchronous, but tolerate slow file flushes.
                for _ in range(100):
                    if screenshot_path.is_file() and screenshot_path.stat().st_size:
                        break
                    time.sleep(0.02)
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            if qtest is not None:
                try:
                    qtest.close()
                except OSError:
                    pass
            if qmp is not None:
                try:
                    qmp.close()
                except OSError:
                    pass
            stop_process(proc)

    elapsed = time.monotonic() - start
    result: dict[str, Any] = {
        "schema_version": 1,
        "command": command,
        "elapsed_seconds": elapsed,
        "qemu_returncode": proc.returncode,
        "failure": failure,
        "serial_success_seen": SERIAL_SUCCESS in serial,
        "serial_failure_seen": SERIAL_FAILURE in serial,
        "status_words": [f"0x{word:016x}" for word in status_words],
        "qemu_log_tail": read_text(qemu_log_path).splitlines()[-200:],
        "host_stderr_tail": read_text(host_stderr_path).splitlines()[-100:],
        "serial_tail": serial.splitlines()[-100:],
    }

    signature_ok = len(status_words) == 8 and status_words[0] == STATUS_SIGNATURE
    if len(status_words) == 8:
        framebuffer_bus = status_words[1] & 0xFFFFFFFF
        width = status_words[2] >> 32
        height = status_words[2] & 0xFFFFFFFF
        pitch = status_words[3] >> 32
        property_response = status_words[3] & 0xFFFFFFFF
        ident0 = status_words[4] >> 32
        rfc = status_words[4] & 0xFFFFFFFF
        intctl = status_words[5] >> 32
        ct1cs = status_words[5] & 0xFFFFFFFF
        guest_border = status_words[6] & 0xFFFFFFFF
        guest_center = status_words[7] & 0xFFFFFFFF
        framebuffer_physical = framebuffer_bus & 0x3FFFFFFF

        qtest_border = None
        qtest_center = None
        # Re-open a stopped-TCG process is unnecessary: values were mirrored
        # into the guest status block specifically so they survive teardown.
        result["framebuffer"] = {
            "bus_address": f"0x{framebuffer_bus:08x}",
            "physical_address": f"0x{framebuffer_physical:08x}",
            "width": width,
            "height": height,
            "pitch": pitch,
            "property_response": f"0x{property_response:08x}",
            "guest_border_word": f"0x{guest_border:08x}",
            "guest_center_word": f"0x{guest_center:08x}",
            "qtest_border_word": qtest_border,
            "qtest_center_word": qtest_center,
        }
        result["v3d"] = {
            "ident0": f"0x{ident0:08x}",
            "rfc": rfc,
            "intctl_at_completion": f"0x{intctl:08x}",
            "ct1cs_at_completion": f"0x{ct1cs:08x}",
        }
        result["guest_memory_passed"] = (
            width == EXPECTED_WIDTH and
            height == EXPECTED_HEIGHT and
            pitch >= EXPECTED_WIDTH * 4 and
            property_response == 0x80000000 and
            ident0 == EXPECTED_IDENT0 and
            rfc >= 1 and
            (intctl & 1) != 0 and
            (ct1cs & ((1 << 5) | (1 << 3))) == 0 and
            guest_border == EXPECTED_BORDER_WORD and
            guest_center == EXPECTED_CENTER_WORD
        )
    else:
        result["guest_memory_passed"] = False

    screenshot_passed = False
    if screenshot_path.is_file() and screenshot_path.stat().st_size:
        try:
            width, height, pixels = parse_ppm(screenshot_path)
            border_samples = {
                "top_left": average_rgb(width, height, pixels,
                                          width // 8, height // 8),
                "bottom_right": average_rgb(width, height, pixels,
                                              width * 7 // 8,
                                              height * 7 // 8),
            }
            center = average_rgb(width, height, pixels,
                                 width // 2, height // 2)
            screenshot_passed = (
                width == EXPECTED_WIDTH and
                height == EXPECTED_HEIGHT and
                all(blue_matches(color)
                    for color in border_samples.values()) and
                yellow_matches(center)
            )
            result["screenshot"] = {
                "path": str(screenshot_path),
                "width": width,
                "height": height,
                "border_samples": border_samples,
                "center_sample": center,
                "passed": screenshot_passed,
            }
        except Exception as exc:
            result["screenshot"] = {
                "path": str(screenshot_path),
                "error": f"{type(exc).__name__}: {exc}",
                "passed": False,
            }
    else:
        result["screenshot"] = {
            "path": str(screenshot_path),
            "error": "screendump was not produced",
            "passed": False,
        }

    result["passed"] = (
        failure is None and
        signature_ok and
        result["serial_success_seen"] and
        result["guest_memory_passed"] and
        screenshot_passed
    )
    (out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path,
                        default=Path("build/qemu-system-aarch64"))
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path,
                        default=Path("build/vc4-v3d-clear-proof"))
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--sample-interval", type=float, default=1.0)
    args = parser.parse_args()

    for label, path in (("QEMU", args.qemu), ("kernel", args.kernel)):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")

    result = run_probe(args)
    print(json.dumps(result, indent=2, sort_keys=True))
    if not result["passed"]:
        print(
            "VC4 V3D clear scanout probe failed\n"
            f"command: {shlex.join(result['command'])}"
        )
        return 1
    print("VC4 V3D clear scanout probe passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
