#!/usr/bin/env python3
"""Boot the framebuffer witness through stock Pi firmware and verify scanout."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from types import ModuleType
from typing import Any

RESULT_ADDRESS = 0x00001000
RESULT_MAGIC = 0x5643345F46422121

OFF_STATUS = 0x08
OFF_STAGE = 0x0C
OFF_DTB = 0x10
OFF_MPIDR = 0x18
OFF_PROPERTY_RESPONSE = 0x20
OFF_FRAMEBUFFER_BUS = 0x24
OFF_FRAMEBUFFER_PHYS = 0x28
OFF_FRAMEBUFFER_SIZE = 0x2C
OFF_PITCH = 0x30
OFF_WIDTH = 0x34
OFF_HEIGHT = 0x38
OFF_DEPTH = 0x3C
OFF_PIXEL_ORDER = 0x40
OFF_SAMPLES = 0x44

EXPECTED_WIDTH = 640
EXPECTED_HEIGHT = 480
EXPECTED_DEPTH = 32
EXPECTED_PIXEL_ORDER = 1
EXPECTED_GUEST_PIXELS = (
    0x000000FF,
    0x0000FF00,
    0x00FF0000,
    0x00FFFFFF,
)
EXPECTED_SCREEN_PIXELS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
)
DEFAULT_CLOCK_STEP_NS = 10_000_000


def load_stock_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load stock probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, command: str) -> list[str]:
        self.file.write(command.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest closed during {command!r}")
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

    def advance(self, nanoseconds: int) -> int:
        fields = self.command(f"clock_step {nanoseconds}")
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest clock reply: {fields!r}")
        return int(fields[1], 0)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


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
        if arguments is not None:
            request["arguments"] = arguments
        self.file.write(json.dumps(request).encode("utf-8") + b"\n")
        message = self._read_message()
        if "error" in message:
            raise RuntimeError(f"QMP {command} failed: {message['error']}")
        return message.get("return")

    def hmp(self, command: str, *, cpu_index: int | None = None) -> str:
        arguments: dict[str, Any] = {"command-line": command}
        if cpu_index is not None:
            arguments["cpu-index"] = cpu_index
        value = self.execute("human-monitor-command", arguments)
        return "" if value is None else str(value)

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_connection(path: Path, proc: subprocess.Popen[bytes],
                        kind: str, timeout: float = 15.0) -> QTest | QMP:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with {proc.returncode}")
        try:
            return QMP(path) if kind == "qmp" else QTest(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.02)
    raise TimeoutError(f"{kind} socket unavailable: {path}") from last_error


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def cpu_snapshot(qmp: QMP) -> dict[str, Any]:
    cpus = qmp.execute("query-cpus-fast")
    records: list[dict[str, Any]] = []
    if isinstance(cpus, list):
        for cpu in cpus:
            if not isinstance(cpu, dict):
                continue
            index = cpu.get("cpu-index")
            registers = ""
            if isinstance(index, int):
                try:
                    registers = qmp.hmp("info registers", cpu_index=index)
                except Exception as exc:
                    registers = f"register query failed: {exc}"
            records.append({
                "cpu_index": index,
                "qom_type": cpu.get("qom-type"),
                "halted": cpu.get("halted"),
                "thread_id": cpu.get("thread-id"),
                "registers": registers,
            })
    return {
        "query_cpus_fast": cpus,
        "cpus": records,
        "info_cpus": qmp.hmp("info cpus"),
    }


def read_ppm(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    position = 0

    def token() -> bytes:
        nonlocal position
        while True:
            while position < len(data) and data[position] in b" \t\r\n":
                position += 1
            if position < len(data) and data[position] == ord("#"):
                while position < len(data) and data[position] != ord("\n"):
                    position += 1
                continue
            break
        start = position
        while position < len(data) and data[position] not in b" \t\r\n#":
            position += 1
        if start == position:
            raise RuntimeError(f"truncated PPM header in {path}")
        return data[start:position]

    if token() != b"P6":
        raise RuntimeError(f"unsupported screendump format in {path}")
    width = int(token())
    height = int(token())
    maximum = int(token())
    if maximum != 255:
        raise RuntimeError(f"unsupported PPM maximum {maximum}")
    while position < len(data) and data[position] in b" \t\r\n":
        position += 1
    pixels = data[position:]
    expected = width * height * 3
    if len(pixels) != expected:
        raise RuntimeError(
            f"PPM pixel payload is {len(pixels)} bytes, expected {expected}"
        )
    return width, height, pixels


def ppm_pixel(width: int, pixels: bytes, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(pixels[offset:offset + 3])  # type: ignore[return-value]


def framebuffer_samples(qtest: QTest, base: int, pitch: int,
                        width: int, height: int) -> tuple[int, ...]:
    coordinates = (
        (width // 4, height // 4),
        (width * 3 // 4, height // 4),
        (width // 4, height * 3 // 4),
        (width * 3 // 4, height * 3 // 4),
    )
    return tuple(
        qtest.readl(base + y * pitch + x * 4)
        for x, y in coordinates
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("kernel8", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=240.0)
    parser.add_argument("--clock-step-ns", type=int,
                        default=DEFAULT_CLOCK_STEP_NS)
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.clock_step_ns <= 0:
        parser.error("--clock-step-ns must be positive")
    for path in (args.qemu, args.bootcode, args.start_elf,
                 args.fixup_dat, args.kernel8):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    image = out / "stock-framebuffer.img"
    stderr_path = out / "qemu.stderr"
    screenshot = out / "framebuffer.ppm"
    result_path = out / "result.json"

    config = (
        b"arm_64bit=1\n"
        b"kernel=kernel8.img\n"
        b"enable_gic=1\n"
        b"disable_splash=1\n"
        b"boot_delay=0\n"
    )
    stock = load_stock_probe()
    layouts = stock.build_fat32_image(image, [
        ("BOOTCODE.BIN", args.bootcode.read_bytes()),
        ("START.ELF", args.start_elf.read_bytes()),
        ("FIXUP.DAT", args.fixup_dat.read_bytes()),
        ("CONFIG.TXT", config),
        ("KERNEL8.IMG", args.kernel8.read_bytes()),
    ])

    with tempfile.TemporaryDirectory(prefix="vc4-stock-fb-") as temp_s:
        temp = Path(temp_s)
        qtest_path = temp / "qtest.sock"
        qmp_path = temp / "qmp.sock"
        command = [
            str(args.qemu.resolve()),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-no-reboot",
            "-d", "guest_errors,unimp",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]
        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=stderr
            )

        qtest: QTest | None = None
        qmp: QMP | None = None
        result: dict[str, Any] = {
            "schema_version": 1,
            "qemu_command": command,
            "image": str(image),
            "fat_layout": {name: list(chain) for name, chain in layouts.items()},
            "expected_magic": f"0x{RESULT_MAGIC:016x}",
            "payload_completed": False,
            "passed": False,
            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
        }
        try:
            qmp_obj = wait_for_connection(qmp_path, proc, "qmp")
            qtest_obj = wait_for_connection(qtest_path, proc, "qtest")
            assert isinstance(qmp_obj, QMP)
            assert isinstance(qtest_obj, QTest)
            qmp = qmp_obj
            qtest = qtest_obj

            deadline = time.monotonic() + args.seconds
            magic = 0
            qtest_clock_ns = 0
            while time.monotonic() < deadline:
                qtest_clock_ns = qtest.advance(args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                magic = qtest.readq(RESULT_ADDRESS)
                if magic == RESULT_MAGIC:
                    result["payload_completed"] = True
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.05)

            status = qtest.readl(RESULT_ADDRESS + OFF_STATUS)
            stage = qtest.readl(RESULT_ADDRESS + OFF_STAGE)
            fb_phys = qtest.readl(RESULT_ADDRESS + OFF_FRAMEBUFFER_PHYS)
            pitch = qtest.readl(RESULT_ADDRESS + OFF_PITCH)
            width = qtest.readl(RESULT_ADDRESS + OFF_WIDTH)
            height = qtest.readl(RESULT_ADDRESS + OFF_HEIGHT)
            depth = qtest.readl(RESULT_ADDRESS + OFF_DEPTH)
            pixel_order = qtest.readl(RESULT_ADDRESS + OFF_PIXEL_ORDER)
            payload_samples = tuple(
                qtest.readl(RESULT_ADDRESS + OFF_SAMPLES + index * 4)
                for index in range(4)
            )

            result.update({
                "observed_magic": f"0x{magic:016x}",
                "status": status,
                "stage": stage,
                "dtb": f"0x{qtest.readq(RESULT_ADDRESS + OFF_DTB):016x}",
                "mpidr": f"0x{qtest.readq(RESULT_ADDRESS + OFF_MPIDR):016x}",
                "property_response":
                    f"0x{qtest.readl(RESULT_ADDRESS + OFF_PROPERTY_RESPONSE):08x}",
                "framebuffer_bus":
                    f"0x{qtest.readl(RESULT_ADDRESS + OFF_FRAMEBUFFER_BUS):08x}",
                "framebuffer_phys": f"0x{fb_phys:08x}",
                "framebuffer_size":
                    qtest.readl(RESULT_ADDRESS + OFF_FRAMEBUFFER_SIZE),
                "pitch": pitch,
                "width": width,
                "height": height,
                "depth": depth,
                "pixel_order": pixel_order,
                "payload_samples": [f"0x{value:08x}" for value in payload_samples],
                "qtest_clock_ns": qtest_clock_ns,
            })

            geometry_ok = (
                width == EXPECTED_WIDTH and
                height == EXPECTED_HEIGHT and
                depth == EXPECTED_DEPTH and
                pixel_order == EXPECTED_PIXEL_ORDER and
                pitch >= width * 4 and
                fb_phys != 0
            )
            qtest_samples: tuple[int, ...] = ()
            if geometry_ok:
                qtest_samples = framebuffer_samples(
                    qtest, fb_phys, pitch, width, height
                )
            result["geometry_ok"] = geometry_ok
            result["payload_samples_match"] = (
                payload_samples == EXPECTED_GUEST_PIXELS
            )
            result["qtest_samples"] = [
                f"0x{value:08x}" for value in qtest_samples
            ]
            result["qtest_samples_match"] = (
                qtest_samples == EXPECTED_GUEST_PIXELS
            )

            screen_samples: tuple[tuple[int, int, int], ...] = ()
            if result["payload_completed"] and status == 0:
                qmp.execute("screendump", {"filename": str(screenshot)})
                dump_deadline = time.monotonic() + 10
                while time.monotonic() < dump_deadline and not screenshot.is_file():
                    time.sleep(0.02)
                if not screenshot.is_file():
                    raise RuntimeError("QMP did not produce the framebuffer PPM")
                ppm_width, ppm_height, pixels = read_ppm(screenshot)
                result["screendump_width"] = ppm_width
                result["screendump_height"] = ppm_height
                if ppm_width == width and ppm_height == height:
                    screen_samples = tuple(
                        ppm_pixel(ppm_width, pixels, x, y)
                        for x, y in (
                            (width // 4, height // 4),
                            (width * 3 // 4, height // 4),
                            (width // 4, height * 3 // 4),
                            (width * 3 // 4, height * 3 // 4),
                        )
                    )
            result["screendump_samples"] = [list(value) for value in screen_samples]
            result["screendump_samples_match"] = (
                screen_samples == EXPECTED_SCREEN_PIXELS
            )
            result["passed"] = bool(
                result["payload_completed"] and
                status == 0 and
                geometry_ok and
                result["payload_samples_match"] and
                result["qtest_samples_match"] and
                result["screendump_samples_match"]
            )

            try:
                qmp.execute("stop")
            except Exception:
                pass
            result["cpu_snapshot"] = cpu_snapshot(qmp)
        except Exception as exc:
            result["probe_error"] = f"{type(exc).__name__}: {exc}"
            if qmp is not None:
                try:
                    result["cpu_snapshot"] = cpu_snapshot(qmp)
                except Exception as snapshot_exc:
                    result["snapshot_error"] = (
                        f"{type(snapshot_exc).__name__}: {snapshot_exc}"
                    )
        finally:
            if stderr_path.is_file():
                stderr_text = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                result["qemu_diagnostics_tail"] = stderr_text.splitlines()[-300:]
            result["qemu_returncode"] = proc.poll()
            result_path.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            if qmp is not None:
                try:
                    if proc.poll() is None:
                        qmp.execute("quit")
                except Exception:
                    pass
                qmp.close()
            if qtest is not None:
                qtest.close()
            stop_process(proc)

    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
