#!/usr/bin/env python3
"""Probe bare-metal and Linux framebuffer bring-up on Raspberry Pi 3 models.

The direct modes validate payload, kernel, initramfs, serial, property-mailbox,
and scanout behavior independently of the VideoCore boot path.  The stock
modes place the same artifacts on a normal FAT32 boot volume and require the
emulated VPU firmware to load and release the ARM side.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from types import ModuleType
from typing import Any

BARE_SIGNATURE_ADDR = 0x00001000
BARE_SIGNATURE = 0x5643345F46424F4B  # "VC4_FBOK"
KERNEL_LOAD_ADDR = 0x00080000
SYSTEM_TIMER_LOW = 0x3F003004
ARM_CONTROL0 = 0x3F00B000
ARM_CONTROL1 = 0x3F00B440
ARM_STATUS = 0x3F00B444
ARM_ID = 0x3F00B44C
PM_PROC = 0x3F100110

PC_RE = re.compile(r"(?:^|\s)pc=([0-9a-fA-F]+)")
SR_RE = re.compile(r"(?:^|\s)sr=([0-9a-fA-F]+)")
REG_RE = re.compile(r"\br([0-9]+)=([0-9a-fA-F]+)")

LINUX_CMDLINE = (
    "earlycon=pl011,0x3f201000 "
    "console=ttyAMA0,115200 "
    "rdinit=/init "
    "loglevel=8 ignore_loglevel printk.time=1 "
    "panic=-1 oops=panic "
    "random.trust_cpu=on"
)


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

    def hmp(self, command: str, *, cpu_index: int | None = None) -> str:
        arguments: dict[str, Any] = {"command-line": command}
        if cpu_index is not None:
            arguments["cpu-index"] = cpu_index
        result = self.execute("human-monitor-command", arguments)
        return "" if result is None else str(result)

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


def load_stock_builder() -> ModuleType:
    path = Path(__file__).with_name("raspi3-stock-bootcode-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_stock_builder", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import FAT32 builder from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def wait_for(path: Path, proc: subprocess.Popen[bytes], kind: str,
             timeout: float = 20.0) -> QMP | QTest:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
        try:
            return QMP(path) if kind == "qmp" else QTest(path)
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


def cpu_snapshot(qmp: QMP, *, full_registers: bool) -> dict[str, Any]:
    cpus = qmp.execute("query-cpus-fast")
    records: list[dict[str, Any]] = []
    if not isinstance(cpus, list):
        return {"query_cpus_fast": cpus, "cpus": records}

    for item in cpus:
        if not isinstance(item, dict):
            continue
        index = item.get("cpu-index")
        record = {
            "cpu_index": index,
            "qom_type": item.get("qom-type"),
            "thread_id": item.get("thread-id"),
            "halted": item.get("halted"),
        }
        if full_registers and isinstance(index, int):
            try:
                record["registers"] = qmp.hmp(
                    "info registers", cpu_index=index
                )
            except Exception as exc:
                record["registers_error"] = f"{type(exc).__name__}: {exc}"
        records.append(record)

    result = {"query_cpus_fast": cpus, "cpus": records}
    if full_registers:
        try:
            result["info_cpus"] = qmp.hmp("info cpus")
        except Exception as exc:
            result["info_cpus_error"] = f"{type(exc).__name__}: {exc}"
    return result


def vpu_state(qmp: QMP) -> dict[str, Any] | None:
    snapshot = cpu_snapshot(qmp, full_registers=False)
    for item in snapshot.get("cpus", []):
        qom_type = str(item.get("qom_type", ""))
        index = item.get("cpu_index")
        if "vc4" not in qom_type.lower() or not isinstance(index, int):
            continue
        registers = qmp.hmp("info registers", cpu_index=index)
        pc_match = PC_RE.search(registers)
        sr_match = SR_RE.search(registers)
        values = {
            int(number): int(value, 16)
            for number, value in REG_RE.findall(registers)
        }
        return {
            "cpu_index": index,
            "qom_type": qom_type,
            "halted": item.get("halted"),
            "pc": None if pc_match is None else f"0x{int(pc_match.group(1), 16):08x}",
            "sr": None if sr_match is None else f"0x{int(sr_match.group(1), 16):08x}",
            "r0": values.get(0),
            "r1": values.get(1),
            "r2": values.get(2),
            "r3": values.get(3),
            "sp": values.get(25),
            "lr": values.get(26),
        }
    return None


def build_stock_image(args: argparse.Namespace, image: Path) -> dict[str, Any]:
    required = [args.bootcode, args.start_elf, args.fixup_dat, args.kernel]
    if args.mode == "linux-stock":
        required.extend([args.dtb, args.initrd])
    for path in required:
        if path is None or not path.is_file():
            raise RuntimeError(f"stock mode input is missing: {path}")

    config_lines = [
        "arm_64bit=1",
        "kernel=KERNEL8.IMG",
        "enable_uart=1",
        "enable_gic=1",
        "disable_splash=1",
        "boot_delay=0",
        "hdmi_force_hotplug=1",
        "disable_overscan=1",
        "framebuffer_width=640",
        "framebuffer_height=480",
        "framebuffer_depth=32",
        "framebuffer_ignore_alpha=1",
    ]
    files: list[tuple[str, bytes]] = [
        ("BOOTCODE.BIN", args.bootcode.read_bytes()),
        ("START.ELF", args.start_elf.read_bytes()),
        ("FIXUP.DAT", args.fixup_dat.read_bytes()),
        ("KERNEL8.IMG", args.kernel.read_bytes()),
    ]
    if args.mode == "linux-stock":
        config_lines.extend([
            "device_tree=BCM2710.DTB",
            "initramfs INITRD.GZ followkernel",
        ])
        files.extend([
            ("BCM2710.DTB", args.dtb.read_bytes()),
            ("INITRD.GZ", args.initrd.read_bytes()),
            ("CMDLINE.TXT", (LINUX_CMDLINE + "\n").encode("ascii")),
        ])
    files.append(("CONFIG.TXT", ("\n".join(config_lines) + "\n").encode("ascii")))

    stock = load_stock_builder()
    layout = stock.build_fat32_image(image, files)
    return {name: list(chain) for name, chain in layout.items()}


def build_command(args: argparse.Namespace, temp: Path,
                  serial_path: Path, image_path: Path | None) -> list[str]:
    command = [str(args.qemu.resolve())]

    if args.mode in {"bare-direct", "linux-direct"}:
        command.extend([
            "-M", "raspi3b-vc4-hetero,direct-arm-kernel=on",
            "-m", "1G", "-smp", "5",
        ])
        command.extend(["-kernel", str(args.kernel.resolve())])
        if args.mode == "linux-direct":
            assert args.dtb is not None and args.initrd is not None
            command.extend([
                "-dtb", str(args.dtb.resolve()),
                "-initrd", str(args.initrd.resolve()),
                "-append", LINUX_CMDLINE,
            ])
    else:
        if image_path is None:
            raise AssertionError("stock mode has no SD image")
        command.extend([
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
        ])

    command.extend([
        "-accel", "tcg,thread=single",
        "-display", "none",
        "-monitor", "none",
        "-serial", f"file:{serial_path}",
        "-serial", "none",
        "-no-reboot",
        "-d", "guest_errors,unimp",
        "-qmp", f"unix:{temp / 'qmp.sock'},server=on,wait=off",
        "-qtest", f"unix:{temp / 'qtest.sock'},server=on,wait=off",
    ])
    if args.icount_shift is not None:
        command.extend([
            "-icount",
            f"shift={args.icount_shift},sleep=off",
        ])
    return command


def read_serial(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def ppm_image(path: Path) -> tuple[int, int, bytes]:
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
        raise ValueError(f"unsupported PPM magic {tokens[0]!r}")
    width = int(tokens[1])
    height = int(tokens[2])
    maximum = int(tokens[3])
    if width <= 0 or height <= 0 or maximum != 255:
        raise ValueError(
            f"unsupported PPM geometry {width}x{height} maximum={maximum}"
        )
    while position < len(data) and data[position] in b" \t\r\n":
        position += 1
    pixels = data[position:]
    expected = width * height * 3
    if len(pixels) < expected:
        raise ValueError(
            f"truncated PPM pixels: expected {expected}, got {len(pixels)}"
        )
    return width, height, pixels[:expected]


def sample_average(width: int, height: int, pixels: bytes,
                   x: int, y: int) -> tuple[int, int, int]:
    totals = [0, 0, 0]
    count = 0
    radius = 4
    for sample_y in range(max(0, y - radius), min(height, y + radius + 1)):
        for sample_x in range(max(0, x - radius), min(width, x + radius + 1)):
            offset = (sample_y * width + sample_x) * 3
            totals[0] += pixels[offset]
            totals[1] += pixels[offset + 1]
            totals[2] += pixels[offset + 2]
            count += 1
    return tuple(value // count for value in totals)


def color_matches(name: str, color: tuple[int, int, int]) -> bool:
    red, green, blue = color
    if name == "red":
        return red >= 150 and red >= green + 70 and red >= blue + 70
    if name == "green":
        return green >= 150 and green >= red + 70 and green >= blue + 70
    if name == "blue":
        return blue >= 150 and blue >= red + 70 and blue >= green + 70
    if name == "white":
        return min(color) >= 150 and max(color) - min(color) <= 70
    raise ValueError(name)


def evaluate_screenshot(path: Path) -> dict[str, Any]:
    try:
        width, height, pixels = ppm_image(path)
        points = {
            "red": (width // 4, height // 4),
            "green": (3 * width // 4, height // 4),
            "blue": (width // 4, 3 * height // 4),
            "white": (3 * width // 4, 3 * height // 4),
        }
        samples = {
            name: sample_average(width, height, pixels, x, y)
            for name, (x, y) in points.items()
        }
        matches = {
            name: color_matches(name, sample)
            for name, sample in samples.items()
        }
        return {
            "available": True,
            "width": width,
            "height": height,
            "samples": {name: list(value) for name, value in samples.items()},
            "matches": matches,
            "quadrants_match": all(matches.values()),
        }
    except Exception as exc:
        return {
            "available": path.is_file(),
            "quadrants_match": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def progress_sample(qtest: QTest, qmp: QMP, elapsed: float) -> dict[str, Any]:
    sample: dict[str, Any] = {"elapsed_seconds": elapsed}
    for name, address, width in (
        ("system_timer_low", SYSTEM_TIMER_LOW, 32),
        ("kernel_word", KERNEL_LOAD_ADDR, 64),
        ("bare_signature", BARE_SIGNATURE_ADDR, 64),
        ("arm_control0", ARM_CONTROL0, 32),
        ("arm_control1", ARM_CONTROL1, 32),
        ("arm_status", ARM_STATUS, 32),
        ("arm_id", ARM_ID, 32),
        ("pm_proc", PM_PROC, 32),
    ):
        try:
            value = qtest.readq(address) if width == 64 else qtest.readl(address)
            sample[name] = f"0x{value:0{width // 4}x}"
        except Exception as exc:
            sample[f"{name}_error"] = f"{type(exc).__name__}: {exc}"
    try:
        sample["vpu"] = vpu_state(qmp)
    except Exception as exc:
        sample["vpu_error"] = f"{type(exc).__name__}: {exc}"
    return sample


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=("bare-direct", "bare-stock", "linux-direct", "linux-stock"),
        required=True,
    )
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--dtb", type=Path)
    parser.add_argument("--initrd", type=Path)
    parser.add_argument("--bootcode", type=Path)
    parser.add_argument("--start-elf", type=Path)
    parser.add_argument("--fixup-dat", type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=240.0)
    parser.add_argument("--icount-shift", type=int)
    parser.add_argument("--sample-interval", type=float, default=5.0)
    args = parser.parse_args()

    if args.seconds <= 0 or args.sample_interval <= 0:
        parser.error("--seconds and --sample-interval must be positive")
    if args.icount_shift is not None and not 0 <= args.icount_shift <= 20:
        parser.error("--icount-shift must be between 0 and 20")
    if not args.qemu.is_file() or not args.kernel.is_file():
        parser.error("--qemu and --kernel must name files")
    if args.mode in {"linux-direct", "linux-stock"}:
        if args.dtb is None or not args.dtb.is_file():
            parser.error("Linux modes require --dtb")
        if args.initrd is None or not args.initrd.is_file():
            parser.error("Linux modes require --initrd")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    result_path = out_dir / "result.json"
    serial_path = out_dir / "serial.log"
    stderr_path = out_dir / "qemu.stderr"
    screenshot_path = out_dir / "framebuffer.ppm"
    image_path: Path | None = None
    fat_layout: dict[str, Any] | None = None

    if args.mode.endswith("stock"):
        image_path = out_dir / "stock-linux.img"
        fat_layout = build_stock_image(args, image_path)

    with tempfile.TemporaryDirectory(prefix="vc4-linux-probe-") as temp_s:
        temp = Path(temp_s)
        command = build_command(args, temp, serial_path, image_path)
        result: dict[str, Any] = {
            "schema_version": 1,
            "mode": args.mode,
            "qemu_command": command,
            "seconds_requested": args.seconds,
            "icount_shift": args.icount_shift,
            "fat_layout": fat_layout,
            "linux_version_seen": False,
            "earlycon_seen": False,
            "init_seen": False,
            "framebuffer_marker_seen": False,
            "framebuffer_missing_seen": False,
            "bare_marker_seen": False,
            "bare_signature_seen": False,
            "progress": [],
        }

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp: QMP | None = None
        qtest: QTest | None = None
        started = time.monotonic()
        next_sample = 0.0
        screenshot_requested = False
        try:
            qmp_obj = wait_for(temp / "qmp.sock", proc, "qmp")
            qtest_obj = wait_for(temp / "qtest.sock", proc, "qtest")
            assert isinstance(qmp_obj, QMP)
            assert isinstance(qtest_obj, QTest)
            qmp = qmp_obj
            qtest = qtest_obj

            deadline = started + args.seconds
            while time.monotonic() < deadline:
                elapsed = time.monotonic() - started
                serial = read_serial(serial_path)
                result["linux_version_seen"] = "Linux version" in serial
                result["earlycon_seen"] = (
                    "Booting Linux on physical CPU" in serial
                    or "Machine model:" in serial
                )
                result["init_seen"] = "VC4_LINUX_INIT_OK" in serial
                result["framebuffer_marker_seen"] = "VC4_LINUX_FB_OK" in serial
                result["framebuffer_missing_seen"] = any(
                    marker in serial
                    for marker in (
                        "VC4_LINUX_FB_MISSING",
                        "VC4_LINUX_FB_FAILED",
                    )
                )
                result["bare_marker_seen"] = "VC4_BARE_FB_OK" in serial
                try:
                    result["bare_signature_seen"] = (
                        qtest.readq(BARE_SIGNATURE_ADDR) == BARE_SIGNATURE
                    )
                except Exception:
                    pass

                if elapsed >= next_sample:
                    result["progress"].append(
                        progress_sample(qtest, qmp, elapsed)
                    )
                    next_sample += args.sample_interval

                marker_ready = (
                    result["bare_marker_seen"]
                    if args.mode.startswith("bare-")
                    else result["framebuffer_marker_seen"]
                )
                if marker_ready and not screenshot_requested:
                    time.sleep(1.0)
                    qmp.execute("screendump", {"filename": str(screenshot_path)})
                    screenshot_requested = True
                    time.sleep(0.5)
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.1)

            if not screenshot_requested:
                try:
                    qmp.execute("screendump", {"filename": str(screenshot_path)})
                    screenshot_requested = True
                except Exception as exc:
                    result["screendump_error"] = f"{type(exc).__name__}: {exc}"

            try:
                qmp.execute("stop")
            except Exception:
                pass
            try:
                result["final_cpu_snapshot"] = cpu_snapshot(
                    qmp, full_registers=True
                )
            except Exception as exc:
                result["final_cpu_snapshot_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
        except Exception as exc:
            result["probe_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            result["elapsed_seconds"] = time.monotonic() - started
            result["qemu_returncode_before_stop"] = proc.poll()
            serial = read_serial(serial_path)
            result["serial_tail"] = serial.splitlines()[-500:]
            if stderr_path.is_file():
                stderr_text = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                result["qemu_diagnostics_tail"] = stderr_text.splitlines()[-500:]
            result["screenshot"] = evaluate_screenshot(screenshot_path)

            if args.mode.startswith("bare-"):
                full_success = (
                    result.get("bare_marker_seen") is True
                    and result.get("bare_signature_seen") is True
                    and result["screenshot"].get("quadrants_match") is True
                )
            else:
                full_success = (
                    result.get("init_seen") is True
                    and result.get("framebuffer_marker_seen") is True
                    and result["screenshot"].get("quadrants_match") is True
                )
            result["passed"] = full_success
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
                try:
                    qmp.close()
                except OSError:
                    pass
            if qtest is not None:
                try:
                    qtest.close()
                except OSError:
                    pass
            stop_process(proc)

    if result.get("passed") is True:
        return 0
    if args.mode.startswith("linux-") and result.get("init_seen") is True:
        return 2
    if args.mode.startswith("linux-") and result.get("linux_version_seen") is True:
        return 3
    if args.mode.startswith("bare-") and result.get("bare_marker_seen") is True:
        return 4
    return 5


if __name__ == "__main__":
    sys.exit(main())
