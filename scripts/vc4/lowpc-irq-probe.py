#!/usr/bin/env python3
"""Capture the VC4 low-PC polling frontier and its interrupt fabric state.

The probe runs the real stock-firmware SD image, samples the VPU PC through
QMP, freezes the machine after it settles below 64 KiB, and snapshots the
VPU interrupt controllers, ARM interrupt controller, system timer, multicore
sync block, and ARM power-control registers through qtest.  It also extracts
bootcode.bin from the image so every sampled low PC is tied to exact bytes.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import hashlib
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any


SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21  # "VC4_ARM!"
KERNEL_LOAD_ADDR = 0x00080000
PC_RE = re.compile(r"(?:^|\s)pc\s*=\s*([0-9a-f]+)", re.IGNORECASE)


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


def wait_for(path: Path, proc: subprocess.Popen[bytes], kind: str,
             timeout: float = 20.0) -> QTest | QMP:
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


def parse_pc(registers: str) -> int | None:
    match = PC_RE.search(registers)
    return int(match.group(1), 16) if match else None


def find_vpu(qmp: QMP) -> tuple[int, str]:
    cpus = qmp.execute("query-cpus-fast")
    if not isinstance(cpus, list):
        raise RuntimeError(f"unexpected query-cpus-fast result: {cpus!r}")
    for item in cpus:
        if not isinstance(item, dict):
            continue
        qom_type = str(item.get("qom-type", ""))
        index = item.get("cpu-index")
        if isinstance(index, int) and "vc4" in qom_type.lower():
            return index, qom_type
    raise RuntimeError(f"no VC4 CPU in query-cpus-fast: {cpus!r}")


def cpu_snapshot(qmp: QMP) -> dict[str, Any]:
    cpus = qmp.execute("query-cpus-fast")
    records: list[dict[str, Any]] = []
    if isinstance(cpus, list):
        for item in cpus:
            if not isinstance(item, dict):
                continue
            index = item.get("cpu-index")
            registers = ""
            if isinstance(index, int):
                try:
                    registers = qmp.hmp("info registers", cpu_index=index)
                except Exception as exc:
                    registers = f"register query failed: {exc}"
            records.append({
                "cpu_index": index,
                "qom_type": item.get("qom-type"),
                "thread_id": item.get("thread-id"),
                "halted": item.get("halted"),
                "pc": None if parse_pc(registers) is None else
                      f"0x{parse_pc(registers):08x}",
                "registers": registers,
            })
    return {
        "query_cpus_fast": cpus,
        "cpus": records,
        "info_cpus": qmp.hmp("info cpus"),
    }


def read_mmio_window(qtest: QTest, base: int, size: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for offset in range(0, size, 4):
        try:
            value = qtest.readl(base + offset)
        except Exception as exc:
            values[f"0x{offset:03x}"] = f"error: {exc}"
        else:
            values[f"0x{offset:03x}"] = f"0x{value:08x}"
    return values


def snapshot_mmio(qtest: QTest) -> dict[str, Any]:
    ranges = {
        "multicore_sync": (0x3F000000, 0x100),
        "vpu_intc0": (0x3F002000, 0x100),
        "vpu_intc1": (0x3F002800, 0x100),
        "system_timer": (0x3F003000, 0x20),
        "arm_control": (0x3F00B000, 0x80),
        "arm_interrupt_controller": (0x3F00B200, 0x40),
        "arm_local_control": (0x3F00B400, 0x60),
        "power_management_proc": (0x3F100100, 0x40),
    }
    return {
        name: {
            "base": f"0x{base:08x}",
            "values": read_mmio_window(qtest, base, size),
        }
        for name, (base, size) in ranges.items()
    }


def find_fat_volume(image: Path) -> tuple[int, bytes]:
    with image.open("rb") as stream:
        sector = stream.read(512)
    if len(sector) != 512:
        raise RuntimeError("SD image is smaller than one sector")

    def plausible_bpb(data: bytes) -> bool:
        if len(data) != 512 or data[510:512] != b"\x55\xaa":
            return False
        bps = struct.unpack_from("<H", data, 11)[0]
        spc = data[13]
        return bps in (512, 1024, 2048, 4096) and spc != 0

    if plausible_bpb(sector):
        return 0, sector
    for index in range(4):
        entry = sector[446 + index * 16:462 + index * 16]
        part_type = entry[4]
        lba = struct.unpack_from("<I", entry, 8)[0]
        sectors = struct.unpack_from("<I", entry, 12)[0]
        if part_type not in (0x04, 0x06, 0x0B, 0x0C, 0x0E, 0xEF):
            continue
        if lba == 0 or sectors == 0:
            continue
        with image.open("rb") as stream:
            stream.seek(lba * 512)
            bpb = stream.read(512)
        if plausible_bpb(bpb):
            return lba * 512, bpb
    raise RuntimeError("no FAT boot volume in SD image")


def extract_bootcode(image: Path) -> bytes:
    volume_offset, bpb = find_fat_volume(image)
    bps = struct.unpack_from("<H", bpb, 11)[0]
    spc = bpb[13]
    reserved = struct.unpack_from("<H", bpb, 14)[0]
    fats = bpb[16]
    root_entries = struct.unpack_from("<H", bpb, 17)[0]
    fat16_sectors = struct.unpack_from("<H", bpb, 22)[0]
    fat32_sectors = struct.unpack_from("<I", bpb, 36)[0]
    fat_sectors = fat16_sectors or fat32_sectors
    fat32 = root_entries == 0
    root_cluster = struct.unpack_from("<I", bpb, 44)[0] & 0x0FFFFFFF
    cluster_bytes = bps * spc
    root_dir_sectors = (root_entries * 32 + bps - 1) // bps
    fat_offset = volume_offset + reserved * bps
    root_offset = fat_offset + fats * fat_sectors * bps
    data_offset = root_offset + root_dir_sectors * bps

    def cluster_offset(cluster: int) -> int:
        if cluster < 2:
            raise RuntimeError(f"invalid FAT cluster {cluster}")
        return data_offset + (cluster - 2) * cluster_bytes

    def next_cluster(cluster: int) -> int | None:
        size = 4 if fat32 else 2
        with image.open("rb") as stream:
            stream.seek(fat_offset + cluster * size)
            raw = stream.read(size)
        if len(raw) != size:
            raise RuntimeError("short FAT read")
        value = struct.unpack("<I" if fat32 else "<H", raw)[0]
        if fat32:
            value &= 0x0FFFFFFF
            return None if value >= 0x0FFFFFF8 else value
        return None if value >= 0xFFF8 else value

    def read_chain(first: int, limit: int | None = None) -> bytes:
        output = bytearray()
        seen: set[int] = set()
        cluster: int | None = first
        while cluster is not None:
            if cluster in seen:
                raise RuntimeError("loop in FAT chain")
            seen.add(cluster)
            with image.open("rb") as stream:
                stream.seek(cluster_offset(cluster))
                output.extend(stream.read(cluster_bytes))
            if limit is not None and len(output) >= limit:
                break
            cluster = next_cluster(cluster)
        return bytes(output if limit is None else output[:limit])

    if fat32:
        directory = read_chain(root_cluster)
    else:
        with image.open("rb") as stream:
            stream.seek(root_offset)
            directory = stream.read(root_entries * 32)

    for offset in range(0, len(directory), 32):
        entry = directory[offset:offset + 32]
        if len(entry) < 32 or entry[0] == 0:
            break
        if entry[0] == 0xE5 or entry[11] == 0x0F:
            continue
        if entry[:11] != b"BOOTCODEBIN":
            continue
        cluster = struct.unpack_from("<H", entry, 26)[0]
        if fat32:
            cluster |= struct.unpack_from("<H", entry, 20)[0] << 16
            cluster &= 0x0FFFFFFF
        size = struct.unpack_from("<I", entry, 28)[0]
        return read_chain(cluster, size)
    raise RuntimeError("bootcode.bin is absent from FAT root directory")


def byte_windows(bootcode: bytes, pcs: Counter[int]) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for pc, count in pcs.most_common(40):
        if pc < 0 or pc >= len(bootcode):
            continue
        start = max(0, pc - 32)
        end = min(len(bootcode), pc + 96)
        windows.append({
            "pc": f"0x{pc:08x}",
            "sample_count": count,
            "start": f"0x{start:08x}",
            "end": f"0x{end:08x}",
            "bytes_hex": bootcode[start:end].hex(),
        })
    return windows


def nonzero_mmio(mmio: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for name, region in mmio.items():
        base = int(region["base"], 0)
        for offset_s, value_s in region["values"].items():
            if value_s.startswith("error") or int(value_s, 0) != 0:
                address = base + int(offset_s, 0)
                lines.append(f"{name} 0x{address:08x} = {value_s}")
    return lines


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# VC4 low-PC interrupt frontier",
        "",
        f"Stop reason: **`{result.get('stop_reason')}`**",
        f"ARM payload signature: **{result.get('signature_seen')}**",
        f"VPU CPU: `{result.get('vpu_qom_type')}` index "
        f"`{result.get('vpu_cpu_index')}`",
        f"Samples: **{result.get('sample_count')}** over "
        f"**{result.get('elapsed_seconds', 0):.3f} s**",
        "",
        "## VPU PC histogram",
        "",
    ]
    for item in result.get("pc_histogram", [])[:50]:
        lines.append(f"- `{item['pc']}`: {item['count']} sample(s)")
    if not result.get("pc_histogram"):
        lines.append("- No PC samples were captured.")
    lines.extend(["", "## Non-zero MMIO snapshot", ""])
    mmio_lines = nonzero_mmio(result.get("mmio", {}))
    lines.extend(f"- `{line}`" for line in mmio_lines)
    if not mmio_lines:
        lines.append("- All sampled registers were zero.")
    lines.extend(["", "## Final CPU state", ""])
    for cpu in result.get("cpu_snapshot", {}).get("cpus", []):
        lines.append(
            f"- CPU `{cpu.get('cpu_index')}` `{cpu.get('qom_type')}`: "
            f"halted=`{cpu.get('halted')}`, pc=`{cpu.get('pc')}`"
        )
    lines.extend([
        "",
        "## Interpretation rule",
        "",
        "The next implementation change must follow the captured path: "
        "peripheral source → raw GPU line → VPU interrupt-controller raw/enable "
        "and status state → VC4 CPU external-interrupt condition.  A zero at an "
        "earlier stage rules out speculative fixes at later stages.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def probe(args: argparse.Namespace) -> int:
    qemu = args.qemu.resolve()
    image = args.image.resolve()
    if not qemu.is_file():
        raise SystemExit(f"not a QEMU executable: {qemu}")
    if not image.is_file():
        raise SystemExit(f"not an SD image: {image}")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    bootcode = extract_bootcode(image)
    bootcode_sha256 = hashlib.sha256(bootcode).hexdigest()
    (args.out_dir / "bootcode.bin").write_bytes(bootcode)

    with tempfile.TemporaryDirectory(prefix="vc4-lowpc-irq-") as tmp_s:
        tmp = Path(tmp_s)
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = args.out_dir / "qemu.stderr"
        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-no-reboot",
            "-d", "guest_errors",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]
        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=stderr
            )

        qmp: QMP | None = None
        qtest: QTest | None = None
        started = time.monotonic()
        pcs: Counter[int] = Counter()
        transitions: list[dict[str, Any]] = []
        recent_low: deque[bool] = deque(maxlen=args.stable_samples)
        previous_pc: int | None = None
        signature_seen = False
        stop_reason = "timeout"
        result: dict[str, Any] = {
            "schema_version": 1,
            "qemu": str(qemu),
            "image": str(image),
            "qemu_command": command,
            "bootcode_size": len(bootcode),
            "bootcode_sha256": bootcode_sha256,
            "signature_address": f"0x{SIGNATURE_ADDR:08x}",
            "expected_signature": f"0x{SIGNATURE:016x}",
        }
        try:
            qmp_obj = wait_for(qmp_path, proc, "qmp")
            qtest_obj = wait_for(qtest_path, proc, "qtest")
            assert isinstance(qmp_obj, QMP)
            assert isinstance(qtest_obj, QTest)
            qmp = qmp_obj
            qtest = qtest_obj
            vpu_index, vpu_type = find_vpu(qmp)
            result["vpu_cpu_index"] = vpu_index
            result["vpu_qom_type"] = vpu_type

            deadline = time.monotonic() + args.seconds
            while time.monotonic() < deadline:
                observed = qtest.readq(SIGNATURE_ADDR)
                if observed == SIGNATURE:
                    signature_seen = True
                    stop_reason = "arm-payload-signature"
                    break
                if proc.poll() is not None:
                    stop_reason = "qemu-exited"
                    break
                registers = qmp.hmp("info registers", cpu_index=vpu_index)
                pc = parse_pc(registers)
                if pc is not None:
                    pcs[pc] += 1
                    if pc != previous_pc:
                        transitions.append({
                            "elapsed_seconds": time.monotonic() - started,
                            "pc": f"0x{pc:08x}",
                        })
                        previous_pc = pc
                    recent_low.append(pc < 0x10000)
                    if (len(recent_low) == recent_low.maxlen and
                            all(recent_low)):
                        stop_reason = "stable-low-pc"
                        break
                time.sleep(args.interval)

            try:
                qmp.execute("stop")
            except Exception:
                pass
            result.update({
                "stop_reason": stop_reason,
                "signature_seen": signature_seen,
                "observed_signature":
                    f"0x{qtest.readq(SIGNATURE_ADDR):016x}",
                "kernel_word":
                    f"0x{qtest.readq(KERNEL_LOAD_ADDR):016x}",
                "elapsed_seconds": time.monotonic() - started,
                "sample_count": sum(pcs.values()),
                "pc_histogram": [
                    {"pc": f"0x{pc:08x}", "count": count}
                    for pc, count in pcs.most_common()
                ],
                "pc_transitions": transitions[-1000:],
                "cpu_snapshot": cpu_snapshot(qmp),
                "mmio": snapshot_mmio(qtest),
                "bootcode_windows": byte_windows(bootcode, pcs),
                "qemu_returncode_before_shutdown": proc.poll(),
            })
            for command_name in ("info irq", "info pic", "info qtree"):
                key = command_name.replace(" ", "_")
                try:
                    result[key] = qmp.hmp(command_name)
                except Exception as exc:
                    result[key] = f"error: {exc}"
        except Exception as exc:
            result["probe_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()
            stop_process(proc)

        stderr_text = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
        result["qemu_stderr_tail"] = stderr_text.splitlines()[-1000:]
        result_path = args.out_dir / "result.json"
        result_path.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_markdown(args.out_dir / "report.md", result)
        print(json.dumps({
            "stop_reason": result.get("stop_reason"),
            "signature_seen": result.get("signature_seen"),
            "sample_count": result.get("sample_count"),
            "top_pcs": result.get("pc_histogram", [])[:12],
            "probe_error": result.get("probe_error"),
        }, indent=2))
        return 1 if result.get("probe_error") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--interval", type=float, default=0.01)
    parser.add_argument("--stable-samples", type=int, default=128)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.seconds <= 0 or args.interval <= 0 or args.stable_samples <= 0:
        parser.error("timing arguments must be positive")
    return probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
