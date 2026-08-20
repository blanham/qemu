#!/usr/bin/env python3
"""Capture time-series CPU and memory state for a silent AArch64 kernel."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import struct
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


class QTestClient:
    def __init__(self, path: Path, timeout: float = 3.0) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.socket.connect(str(path))
        self.buffer = b""

    def close(self) -> None:
        self.socket.close()

    def command(self, command: str) -> str:
        self.socket.sendall(command.encode("ascii") + b"\n")
        while b"\n" not in self.buffer:
            chunk = self.socket.recv(65536)
            if not chunk:
                raise ConnectionError("qtest socket closed")
            self.buffer += chunk
        line, self.buffer = self.buffer.split(b"\n", 1)
        response = line.decode("ascii", errors="replace").strip()
        if not response.startswith("OK"):
            raise RuntimeError(f"qtest {command!r}: {response}")
        return response[2:].strip()

    def read(self, address: int, size: int) -> bytes:
        value = self.command(f"read 0x{address:x} 0x{size:x}")
        if value.startswith("0x"):
            value = value[2:]
        return bytes.fromhex(value)


class QMPClient:
    def __init__(self, path: Path, timeout: float = 3.0) -> None:
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(timeout)
        self.socket.connect(str(path))
        self.file = self.socket.makefile("rwb", buffering=0)
        self._read()
        self.execute("qmp_capabilities")

    def close(self) -> None:
        self.file.close()
        self.socket.close()

    def _read(self) -> dict[str, Any]:
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
        self.file.write(json.dumps(command).encode() + b"\n")
        response = self._read()
        if "error" in response:
            raise RuntimeError(f"QMP {name}: {response['error']}")
        return response.get("return")

    def hmp(self, command: str) -> str:
        value = self.execute(
            "human-monitor-command", {"command-line": command}
        )
        return str(value or "")


def connect(kind, path: Path, process: subprocess.Popen[bytes], timeout: float):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"QEMU exited with status {process.returncode}")
        try:
            return kind(path)
        except (FileNotFoundError, ConnectionRefusedError, socket.timeout) as exc:
            last_error = exc
            time.sleep(0.025)
    raise TimeoutError(f"could not connect to {path}: {last_error}")


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def parse_image_header(data: bytes) -> dict[str, Any]:
    if len(data) < 64:
        return {"valid": False, "error": "truncated header"}
    code0, code1, text_offset, image_size, flags = struct.unpack_from(
        "<IIQQQ", data, 0
    )
    magic = data[56:60]
    return {
        "valid": magic == b"ARM\x64",
        "code0": f"0x{code0:08x}",
        "code1": f"0x{code1:08x}",
        "text_offset": f"0x{text_offset:016x}",
        "image_size": f"0x{image_size:016x}",
        "flags": f"0x{flags:016x}",
        "magic_hex": magic.hex(),
        "pe_offset": f"0x{struct.unpack_from('<I', data, 60)[0]:08x}",
    }


def cpu_register_snapshots(qmp: QMPClient) -> dict[str, Any]:
    result: dict[str, Any] = {
        "query_cpus_fast": qmp.execute("query-cpus-fast"),
        "info_cpus": qmp.hmp("info cpus"),
        "registers": [],
    }
    cpus = result["query_cpus_fast"] or []
    for index in range(len(cpus)):
        try:
            qmp.hmp(f"cpu {index}")
            registers = qmp.hmp("info registers")
        except Exception as exc:
            registers = f"snapshot failed: {type(exc).__name__}: {exc}"
        result["registers"].append(
            {"cpu_index": index, "registers": registers}
        )
    return result


def extract_pcs(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    pcs = []
    for item in snapshot.get("registers", []):
        text = str(item.get("registers", ""))
        matches = re.findall(
            r"(?:^|\s)(?:pc|PC)\s*[=:]\s*(?:0x)?([0-9a-fA-F]+)", text
        )
        pcs.append(
            {
                "cpu_index": item.get("cpu_index"),
                "pcs": [f"0x{int(value, 16):x}" for value in matches],
            }
        )
    return pcs


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=90.0)
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

    host_kernel = inputs["kernel"].read_bytes()
    host_prefix = host_kernel[:256]
    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    qtest_path = out_dir / "qtest.sock"
    qmp_path = out_dir / "qmp.sock"
    serial_path = out_dir / "serial.log"
    stderr_path = out_dir / "qemu.stderr"
    qemu_log_path = out_dir / "qemu.log"
    for path in (
        qtest_path, qmp_path, serial_path, stderr_path, qemu_log_path
    ):
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
        "-qtest", f"unix:{qtest_path},server=on,wait=off",
        "-qmp", f"unix:{qmp_path},server=on,wait=off",
        "-d", "guest_errors,int,cpu_reset",
        "-D", str(qemu_log_path),
        "-no-reboot",
    ]
    (out_dir / "command.json").write_text(
        json.dumps(command, indent=2) + "\n", encoding="utf-8"
    )

    process: subprocess.Popen[bytes] | None = None
    qtest: QTestClient | None = None
    qmp: QMPClient | None = None
    probe_error: str | None = None
    snapshots: list[dict[str, Any]] = []
    start = time.monotonic()
    schedule = [0.05, 0.25, 1.0, 3.0, 8.0, 15.0, 30.0, 60.0, args.seconds]
    schedule = sorted({value for value in schedule if value <= args.seconds})

    with stderr_path.open("wb") as stderr:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )
            qtest = connect(QTestClient, qtest_path, process, 8.0)
            qmp = connect(QMPClient, qmp_path, process, 8.0)
            for target in schedule:
                while time.monotonic() - start < target:
                    if process.poll() is not None:
                        break
                    time.sleep(0.01)
                guest_prefix = b""
                memory_error = None
                try:
                    guest_prefix = qtest.read(0x80000, len(host_prefix))
                except Exception as exc:
                    memory_error = f"{type(exc).__name__}: {exc}"
                try:
                    cpu_state = cpu_register_snapshots(qmp)
                except Exception as exc:
                    cpu_state = {
                        "error": f"{type(exc).__name__}: {exc}",
                        "registers": [],
                    }
                serial = read_text(serial_path)
                snapshots.append(
                    {
                        "elapsed_seconds": time.monotonic() - start,
                        "process_returncode": process.poll(),
                        "kernel_memory_matches": guest_prefix == host_prefix,
                        "guest_kernel_header": parse_image_header(guest_prefix),
                        "guest_prefix_hex": guest_prefix.hex(),
                        "memory_error": memory_error,
                        "cpu_state": cpu_state,
                        "pcs": extract_pcs(cpu_state),
                        "serial_tail": serial.splitlines()[-160:],
                    }
                )
                if process.poll() is not None or FB_MARKER in serial:
                    break
        except Exception as exc:
            probe_error = f"{type(exc).__name__}: {exc}"
        finally:
            if qmp is not None:
                try:
                    qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            if qtest is not None:
                qtest.close()
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
    if FB_MARKER in serial:
        classification = "linux-framebuffer-marker"
    elif INIT_MARKER in serial:
        classification = "linux-init"
    elif LINUX_BANNER in serial:
        classification = "linux-banner"
    elif any(
        any(
            pc not in ("0x0", "0x80000")
            for entry in snapshot.get("pcs", [])
            for pc in entry.get("pcs", [])
        )
        for snapshot in snapshots
    ):
        classification = "kernel-executed-silent"
    elif snapshots and all(
        snapshot.get("kernel_memory_matches") for snapshot in snapshots
    ):
        classification = "kernel-loaded-not-executing"
    else:
        classification = "kernel-load-or-control-failure"

    result = {
        "schema_version": 1,
        "classification": classification,
        "probe_error": probe_error,
        "elapsed_seconds": time.monotonic() - start,
        "qemu_returncode": process.returncode if process is not None else None,
        "host_kernel_header": parse_image_header(host_prefix),
        "host_kernel_size": len(host_kernel),
        "linux_version_seen": LINUX_BANNER in serial,
        "init_seen": INIT_MARKER in serial,
        "fb_marker_seen": FB_MARKER in serial,
        "snapshots": snapshots,
        "serial_tail": serial.splitlines()[-1000:],
        "qemu_stderr_tail": read_text(stderr_path).splitlines()[-500:],
        "qemu_log_tail": read_text(qemu_log_path).splitlines()[-2000:],
    }
    (out_dir / "result.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if classification in {
        "linux-framebuffer-marker", "linux-init", "linux-banner"
    } else 1


if __name__ == "__main__":
    raise SystemExit(main())
