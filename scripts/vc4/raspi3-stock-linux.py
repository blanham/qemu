#!/usr/bin/env python3
"""Boot a pinned Raspberry Pi Linux kernel through the emulated VC4 firmware."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time
from types import ModuleType
from typing import Any

LINUX_BANNER = "Linux version "
INIT_MARKER = "VC4_LINUX_INIT_START"
FB_OK_MARKER = "VC4_LINUX_FB_OK"
FB_FAILURE_MARKERS = (
    "VC4_LINUX_FB_OPEN_FAIL",
    "VC4_LINUX_FB_IOCTL_FAIL",
    "VC4_LINUX_FB_GEOMETRY_FAIL",
    "VC4_LINUX_FB_SIZE_FAIL",
    "VC4_LINUX_FB_MMAP_FAIL",
    "VC4_LINUX_FB_UNKNOWN_FAIL",
)
EXPECTED_SCREEN_PIXELS = (
    (255, 0, 0),
    (0, 255, 0),
    (0, 0, 255),
    (255, 255, 255),
)
DEFAULT_CLOCK_STEP_NS = 10_000_000

ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110


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
    if len(pixels) != width * height * 3:
        raise RuntimeError(
            f"PPM has {len(pixels)} pixel bytes, expected {width * height * 3}"
        )
    return width, height, pixels


def ppm_pixel(width: int, pixels: bytes, x: int, y: int) -> tuple[int, int, int]:
    offset = (y * width + x) * 3
    return tuple(pixels[offset:offset + 3])  # type: ignore[return-value]


def serial_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("qemu", type=Path)
    parser.add_argument("bootcode", type=Path)
    parser.add_argument("start_elf", type=Path)
    parser.add_argument("fixup_dat", type=Path)
    parser.add_argument("kernel8", type=Path)
    parser.add_argument("dtb", type=Path)
    parser.add_argument("initramfs", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=600.0)
    parser.add_argument("--clock-step-ns", type=int,
                        default=DEFAULT_CLOCK_STEP_NS)
    parser.add_argument("--poll-interval", type=float, default=0.005)
    args = parser.parse_args()

    if args.seconds <= 0:
        parser.error("--seconds must be positive")
    if args.clock_step_ns <= 0:
        parser.error("--clock-step-ns must be positive")
    if args.poll_interval < 0:
        parser.error("--poll-interval must not be negative")
    for path in (args.qemu, args.bootcode, args.start_elf, args.fixup_dat,
                 args.kernel8, args.dtb, args.initramfs):
        if not path.is_file():
            parser.error(f"not a file: {path}")

    out = args.out_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    image = out / "stock-linux.img"
    serial_path = out / "serial.log"
    stderr_path = out / "qemu.stderr"
    screenshot = out / "linux-framebuffer.ppm"
    result_path = out / "result.json"

    config = (
        b"arm_64bit=1\n"
        b"kernel=kernel8.img\n"
        b"device_tree=bcm2710.dtb\n"
        b"initramfs initrd.gz followkernel\n"
        b"enable_gic=1\n"
        b"enable_uart=1\n"
        b"core_freq=250\n"
        b"disable_splash=1\n"
        b"boot_delay=0\n"
        b"framebuffer_width=640\n"
        b"framebuffer_height=480\n"
        b"framebuffer_depth=32\n"
        b"framebuffer_ignore_alpha=1\n"
        b"hdmi_force_hotplug=1\n"
    )
    command_line = (
        b"console=tty1 console=ttyAMA0,115200 "
        b"earlycon=pl011,mmio32,0x3f201000 "
        b"rdinit=/init rw loglevel=8 ignore_loglevel nokaslr "
        b"fbcon=map:0 panic=-1\n"
    )
    stock = load_stock_probe()
    layouts = stock.build_fat32_image(image, [
        ("BOOTCODE.BIN", args.bootcode.read_bytes()),
        ("START.ELF", args.start_elf.read_bytes()),
        ("FIXUP.DAT", args.fixup_dat.read_bytes()),
        ("CONFIG.TXT", config),
        ("CMDLINE.TXT", command_line),
        ("KERNEL8.IMG", args.kernel8.read_bytes()),
        ("BCM2710.DTB", args.dtb.read_bytes()),
        ("INITRD.GZ", args.initramfs.read_bytes()),
    ])

    with tempfile.TemporaryDirectory(prefix="vc4-stock-linux-") as temp_s:
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
            "-serial", f"file:{serial_path}",
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
            "qtest_clock_step_ns": args.clock_step_ns,
            "qtest_clock_steps": 0,
            "linux_banner_seen": False,
            "init_marker_seen": False,
            "framebuffer_marker_seen": False,
            "framebuffer_failure_marker": None,
            "screendump_samples_match": False,
            "passed": False,
        }
        try:
            qmp_obj = wait_for_connection(qmp_path, proc, "qmp")
            qtest_obj = wait_for_connection(qtest_path, proc, "qtest")
            assert isinstance(qmp_obj, QMP)
            assert isinstance(qtest_obj, QTest)
            qmp = qmp_obj
            qtest = qtest_obj

            deadline = time.monotonic() + args.seconds
            qtest_clock_ns = 0
            text = ""
            while time.monotonic() < deadline:
                qtest_clock_ns = qtest.advance(args.clock_step_ns)
                result["qtest_clock_steps"] += 1
                text = serial_text(serial_path)
                result["linux_banner_seen"] = LINUX_BANNER in text
                result["init_marker_seen"] = INIT_MARKER in text
                result["framebuffer_marker_seen"] = FB_OK_MARKER in text
                failure = next(
                    (marker for marker in FB_FAILURE_MARKERS if marker in text),
                    None,
                )
                result["framebuffer_failure_marker"] = failure
                if result["framebuffer_marker_seen"] or failure is not None:
                    break
                if proc.poll() is not None:
                    break
                if args.poll_interval:
                    time.sleep(args.poll_interval)

            result["qtest_clock_ns"] = qtest_clock_ns
            text = serial_text(serial_path)
            result["serial_bytes"] = len(text.encode("utf-8", errors="replace"))
            result["serial_tail"] = text.splitlines()[-1200:]
            result["arm_control0"] = f"0x{qtest.readl(ARM_CONTROL0):08x}"
            result["arm_control1"] = f"0x{qtest.readl(ARM_CONTROL1):08x}"
            result["arm_status"] = f"0x{qtest.readl(ARM_STATUS):08x}"
            result["arm_id"] = f"0x{qtest.readl(ARM_ID):08x}"
            result["pm_proc"] = f"0x{qtest.readl(PM_PROC):08x}"

            screen_samples: tuple[tuple[int, int, int], ...] = ()
            if result["framebuffer_marker_seen"]:
                qmp.execute("screendump", {"filename": str(screenshot)})
                dump_deadline = time.monotonic() + 10
                while time.monotonic() < dump_deadline and not screenshot.is_file():
                    time.sleep(0.02)
                if not screenshot.is_file():
                    raise RuntimeError("QMP did not produce the Linux framebuffer PPM")
                width, height, pixels = read_ppm(screenshot)
                result["screendump_width"] = width
                result["screendump_height"] = height
                screen_samples = tuple(
                    ppm_pixel(width, pixels, x, y)
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
                result["linux_banner_seen"] and
                result["init_marker_seen"] and
                result["framebuffer_marker_seen"] and
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
            text = serial_text(serial_path)
            result["serial_tail"] = text.splitlines()[-1200:]
            if stderr_path.is_file():
                stderr_text = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                result["qemu_diagnostics_tail"] = stderr_text.splitlines()[-500:]
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
