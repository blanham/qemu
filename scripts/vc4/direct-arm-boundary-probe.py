#!/usr/bin/env python3
"""Verify direct ARM loading, release, serial output, and RAM witnesses."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from typing import Any


class LineSocket:
    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        self.path = path
        self.timeout = timeout
        self.sock: socket.socket | None = None
        self.file = None

    def connect(self) -> None:
        deadline = time.monotonic() + self.timeout
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(2.0)
                sock.connect(str(self.path))
                self.sock = sock
                self.file = sock.makefile("r", encoding="utf-8")
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.05)
        raise TimeoutError(f"could not connect to {self.path}: {last_error}")

    def close(self) -> None:
        if self.file is not None:
            self.file.close()
            self.file = None
        if self.sock is not None:
            self.sock.close()
            self.sock = None


class QMPClient(LineSocket):
    def __init__(self, path: Path, timeout: float = 10.0) -> None:
        super().__init__(path, timeout)
        self.next_id = 1

    def connect(self) -> None:
        super().connect()
        greeting = self._read(time.monotonic() + self.timeout)
        if "QMP" not in greeting:
            raise RuntimeError(f"invalid QMP greeting: {greeting!r}")
        self.command("qmp_capabilities")

    def _read(self, deadline: float) -> dict[str, Any]:
        if self.file is None:
            raise RuntimeError("QMP is not connected")
        while time.monotonic() < deadline:
            line = self.file.readline()
            if not line:
                raise EOFError("QMP socket closed")
            value = json.loads(line)
            if isinstance(value, dict):
                return value
        raise TimeoutError("QMP response timeout")

    def command(self, execute: str, arguments: dict[str, Any] | None = None,
                timeout: float = 10.0) -> Any:
        if self.sock is None:
            raise RuntimeError("QMP is not connected")
        command_id = self.next_id
        self.next_id += 1
        request: dict[str, Any] = {"execute": execute, "id": command_id}
        if arguments:
            request["arguments"] = arguments
        self.sock.sendall((json.dumps(request) + "\r\n").encode())
        deadline = time.monotonic() + timeout
        while True:
            message = self._read(deadline)
            if message.get("id") != command_id:
                continue
            if "error" in message:
                raise RuntimeError(f"QMP {execute}: {message['error']}")
            return message.get("return")


class QTestClient(LineSocket):
    def command(self, command: str) -> str:
        if self.sock is None or self.file is None:
            raise RuntimeError("qtest is not connected")
        self.sock.sendall((command + "\n").encode())
        line = self.file.readline()
        if not line:
            raise EOFError("qtest socket closed")
        line = line.strip()
        if not line.startswith("OK"):
            raise RuntimeError(f"qtest {command!r}: {line}")
        return line[2:].strip()

    def read(self, address: int, size: int) -> bytes:
        value = self.command(f"read 0x{address:x} {size}")
        if value.startswith("0x"):
            value = value[2:]
        return bytes.fromhex(value)

    def readq(self, address: int) -> int:
        value = self.command(f"readq 0x{address:x}")
        return int(value, 0)


def parse_load(value: str) -> tuple[Path, int]:
    try:
        filename, address = value.rsplit("@", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected FILE@ADDRESS") from exc
    return Path(filename), int(address, 0)


def parse_marker(value: str) -> tuple[int, int]:
    try:
        address, expected = value.split("=", 1)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected ADDRESS=VALUE") from exc
    return int(address, 0), int(expected, 0)


def cpu_state(qmp: QMPClient) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, command, arguments in (
        ("status", "query-status", None),
        ("cpus", "query-cpus-fast", None),
        (
            "registers",
            "human-monitor-command",
            {"command-line": "info registers"},
        ),
    ):
        try:
            result[name] = qmp.command(command, arguments)
        except Exception as exc:
            result[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("boot_kernel", type=Path)
    parser.add_argument("--boot-address", type=lambda value: int(value, 0),
                        default=0x80000)
    parser.add_argument("--load", action="append", type=parse_load,
                        default=[])
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--initrd", type=Path)
    parser.add_argument("--append", default="")
    parser.add_argument("--serial-marker")
    parser.add_argument("--memory-marker", action="append", type=parse_marker,
                        default=[])
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    boot_kernel = args.boot_kernel.resolve()
    loads = [(path.resolve(), address) for path, address in args.load]
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    for label, path in (("QEMU", qemu), ("boot kernel", boot_kernel), *[
        ("loaded image", path) for path, _ in loads
    ]):
        if not path.is_file():
            parser.error(f"{label} does not exist: {path}")

    with tempfile.TemporaryDirectory(prefix="vc4-arm-boundary-") as temp_name:
        temp = Path(temp_name)
        qmp_path = temp / "qmp.sock"
        qtest_path = temp / "qtest.sock"
        serial_path = out_dir / "serial.log"
        stderr_path = out_dir / "qemu.stderr"
        command = [
            str(qemu),
            "-accel", "tcg",
            "-S",
            "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",
            "-m", "1G",
            "-kernel", str(boot_kernel),
            "-display", "none",
            "-monitor", "none",
            "-serial", f"file:{serial_path}",
            "-no-reboot",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:path={qtest_path},server=on,wait=off",
        ]
        if args.dtb:
            command += ["-dtb", str(args.dtb.resolve())]
        if args.initrd:
            command += ["-initrd", str(args.initrd.resolve())]
        if args.append:
            command += ["-append", args.append]
        for path, address in loads:
            command += [
                "-device",
                f"loader,file={path},addr=0x{address:x},force-raw=on",
            ]
        (out_dir / "command.json").write_text(
            json.dumps(command, indent=2) + "\n"
        )

        with stderr_path.open("wb") as stderr_file:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr_file,
            )
            qmp = QMPClient(qmp_path)
            qtest = QTestClient(qtest_path)
            qmp_error = None
            qtest_error = None
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            load_checks: list[dict[str, Any]] = []
            marker_checks: list[dict[str, Any]] = []
            try:
                qmp.connect()
                qtest.connect()
                before = cpu_state(qmp)
                expected = boot_kernel.read_bytes()[:64]
                observed = qtest.read(args.boot_address, len(expected))
                load_checks.append({
                    "kind": "boot_kernel",
                    "address": f"0x{args.boot_address:x}",
                    "expected": expected.hex(),
                    "observed": observed.hex(),
                    "matched": observed == expected,
                })
                for path, address in loads:
                    expected = path.read_bytes()[:64]
                    observed = qtest.read(address, len(expected))
                    load_checks.append({
                        "kind": "secondary_load",
                        "path": str(path),
                        "address": f"0x{address:x}",
                        "expected": expected.hex(),
                        "observed": observed.hex(),
                        "matched": observed == expected,
                    })
                qmp.command("cont")
                deadline = time.monotonic() + args.seconds
                while time.monotonic() < deadline and process.poll() is None:
                    serial = (
                        serial_path.read_text(
                            encoding="utf-8", errors="replace"
                        ) if serial_path.is_file() else ""
                    )
                    if args.serial_marker and args.serial_marker in serial:
                        break
                    time.sleep(0.1)
                after = cpu_state(qmp)
                for address, expected in args.memory_marker:
                    observed = qtest.readq(address)
                    marker_checks.append({
                        "address": f"0x{address:x}",
                        "expected": f"0x{expected:016x}",
                        "observed": f"0x{observed:016x}",
                        "matched": observed == expected,
                    })
            except Exception as exc:
                if qmp.sock is None:
                    qmp_error = f"{type(exc).__name__}: {exc}"
                else:
                    qtest_error = f"{type(exc).__name__}: {exc}"
            finally:
                try:
                    if qmp.sock is not None:
                        qmp.command("quit", timeout=2.0)
                except Exception:
                    pass
                qtest.close()
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

        serial = (
            serial_path.read_text(encoding="utf-8", errors="replace")
            if serial_path.is_file() else ""
        )
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        serial_marker_seen = (
            args.serial_marker in serial if args.serial_marker else True
        )
        passed = (
            bool(load_checks)
            and all(check["matched"] for check in load_checks)
            and serial_marker_seen
            and all(check["matched"] for check in marker_checks)
            and qmp_error is None
            and qtest_error is None
        )
        result = {
            "schema_version": 1,
            "passed": passed,
            "qemu_returncode": process.returncode,
            "qmp_error": qmp_error,
            "qtest_error": qtest_error,
            "serial_marker": args.serial_marker,
            "serial_marker_seen": serial_marker_seen,
            "load_checks": load_checks,
            "memory_marker_checks": marker_checks,
            "cpu_state_before_cont": before,
            "cpu_state_after_cont": after,
            "serial_tail": serial.splitlines()[-240:],
            "stderr_tail": stderr.splitlines()[-240:],
        }
        (out_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n"
        )
        print(json.dumps({
            "passed": passed,
            "serial_marker_seen": serial_marker_seen,
            "loads_matched": all(
                check["matched"] for check in load_checks
            ) if load_checks else False,
            "memory_markers_matched": all(
                check["matched"] for check in marker_checks
            ),
            "qemu_returncode": process.returncode,
            "qmp_error": qmp_error,
            "qtest_error": qtest_error,
        }, indent=2))
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
