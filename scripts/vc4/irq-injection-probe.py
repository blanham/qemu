#!/usr/bin/env python3
"""Inject VC4 interrupt lines by layer at the reproducible low-PC frontier.

The probe boots the exact stock-firmware SD image, waits until the VideoCore
CPU settles into its sub-64-KiB interrupt wait loop, then uses qtest's GPIO
injection command to test the VPU interrupt-controller input and the VC4 CPU
input independently.  No production device semantics are changed.
"""

from __future__ import annotations

import argparse
from collections import Counter, deque
import json
from pathlib import Path
import re
import socket
import subprocess
import tempfile
import time
from typing import Any


SIGNATURE_ADDR = 0x00001000
SIGNATURE = 0x5643345F41524D21
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

    def set_irq(self, path: str, name: str, number: int, level: int) -> None:
        self.command(
            f"set_irq_in {path} {name} {number:d} {1 if level else 0}"
        )

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
        value = self.execute("human-monitor-command", arguments)
        return "" if value is None else str(value)

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


def find_vpu_cpu(qmp: QMP) -> tuple[int, str]:
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
    raise RuntimeError("query-cpus-fast did not expose a VC4 CPU")


def qom_children(qmp: QMP, path: str) -> list[tuple[str, str]]:
    try:
        entries = qmp.execute("qom-list", {"path": path})
    except Exception:
        return []
    children: list[tuple[str, str]] = []
    if not isinstance(entries, list):
        return children
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        kind = str(entry.get("type", ""))
        if not isinstance(name, str) or not kind.startswith("child<"):
            continue
        child = (path.rstrip("/") + "/" + name) if path != "/" else "/" + name
        children.append((child, kind[6:-1]))
    return children


def walk_qom(qmp: QMP) -> list[tuple[str, str]]:
    queue = deque(["/"])
    seen = {"/"}
    found: list[tuple[str, str]] = []
    while queue and len(seen) < 5000:
        path = queue.popleft()
        for child, kind in qom_children(qmp, path):
            found.append((child, kind))
            if child not in seen:
                seen.add(child)
                queue.append(child)
    return found


def find_instance(instances: list[tuple[str, str]], *terms: str,
                  prefer: str | None = None) -> tuple[str, str] | None:
    matches = [
        item for item in instances
        if all(term.lower() in item[1].lower() for term in terms)
    ]
    if prefer:
        preferred = [item for item in matches if prefer in item[0]]
        if preferred:
            matches = preferred
    return matches[0] if matches else None


def vpu_pc(qmp: QMP, index: int) -> int | None:
    return parse_pc(qmp.hmp("info registers", cpu_index=index))


def sample(qmp: QMP, qtest: QTest, index: int, seconds: float,
           interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    pcs: Counter[int] = Counter()
    transitions: list[str] = []
    previous: int | None = None
    signature_seen = False
    while time.monotonic() < deadline:
        if qtest.readq(SIGNATURE_ADDR) == SIGNATURE:
            signature_seen = True
            break
        pc = vpu_pc(qmp, index)
        if pc is not None:
            pcs[pc] += 1
            if pc != previous:
                transitions.append(f"0x{pc:08x}")
                previous = pc
        time.sleep(interval)
    return {
        "signature_seen": signature_seen,
        "pc_histogram": [
            {"pc": f"0x{pc:08x}", "count": count}
            for pc, count in pcs.most_common()
        ],
        "pc_transitions": transitions,
        "final_pc": None if previous is None else f"0x{previous:08x}",
    }


def wait_for_low_pc(qmp: QMP, qtest: QTest, index: int,
                    timeout: float, stable_samples: int,
                    interval: float) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    recent: deque[int] = deque(maxlen=stable_samples)
    pcs: Counter[int] = Counter()
    while time.monotonic() < deadline:
        if qtest.readq(SIGNATURE_ADDR) == SIGNATURE:
            return {
                "reached": False,
                "signature_seen": True,
                "pc_histogram": [],
            }
        pc = vpu_pc(qmp, index)
        if pc is not None:
            pcs[pc] += 1
            recent.append(pc)
            if (len(recent) == recent.maxlen and
                    all(value < 0x10000 for value in recent)):
                return {
                    "reached": True,
                    "signature_seen": False,
                    "stable_pc": f"0x{Counter(recent).most_common(1)[0][0]:08x}",
                    "pc_histogram": [
                        {"pc": f"0x{value:08x}", "count": count}
                        for value, count in pcs.most_common()
                    ],
                }
        time.sleep(interval)
    return {
        "reached": False,
        "signature_seen": False,
        "pc_histogram": [
            {"pc": f"0x{value:08x}", "count": count}
            for value, count in pcs.most_common()
        ],
    }


def mmio_window(qtest: QTest, base: int, size: int) -> dict[str, str]:
    values: dict[str, str] = {}
    for offset in range(0, size, 4):
        try:
            value = qtest.readl(base + offset)
        except Exception as exc:
            values[f"0x{offset:03x}"] = f"error: {exc}"
        else:
            values[f"0x{offset:03x}"] = f"0x{value:08x}"
    return values


def attempt_irq(qtest: QTest, qmp: QMP, vpu_index: int,
                path: str, names: tuple[str, ...], number: int,
                pulse_seconds: float, interval: float) -> dict[str, Any]:
    errors: list[str] = []
    before = vpu_pc(qmp, vpu_index)
    for name in names:
        try:
            qtest.set_irq(path, name, number, 1)
        except Exception as exc:
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        try:
            observed = sample(
                qmp, qtest, vpu_index, pulse_seconds, interval
            )
        finally:
            try:
                qtest.set_irq(path, name, number, 0)
            except Exception as exc:
                errors.append(
                    f"{name} deassert: {type(exc).__name__}: {exc}"
                )
        time.sleep(interval * 2)
        after = vpu_pc(qmp, vpu_index)
        transitions = observed.get("pc_transitions", [])
        changed = any(
            int(value, 0) != before for value in transitions
            if before is not None
        )
        return {
            "path": path,
            "gpio_name": name,
            "number": number,
            "before_pc": None if before is None else f"0x{before:08x}",
            "after_pc": None if after is None else f"0x{after:08x}",
            "changed_pc": changed,
            "errors": errors,
            **observed,
        }
    return {
        "path": path,
        "gpio_name": None,
        "number": number,
        "before_pc": None if before is None else f"0x{before:08x}",
        "after_pc": None,
        "changed_pc": False,
        "signature_seen": False,
        "errors": errors,
        "pc_histogram": [],
        "pc_transitions": [],
    }


def classify(result: dict[str, Any]) -> str:
    if result.get("baseline", {}).get("signature_seen"):
        return "baseline-reached-arm-payload"
    intc = result.get("intc_injections", [])
    if any(item.get("signature_seen") for item in intc):
        return "missing-or-misrouted-raw-gpu-source"
    if any(item.get("changed_pc") for item in intc):
        return "raw-source-or-edge-delivery"
    cpu = result.get("cpu_injection") or {}
    if cpu.get("signature_seen"):
        return "vpu-interrupt-controller-path"
    if cpu.get("changed_pc"):
        return "vpu-interrupt-controller-or-mask"
    if not result.get("vpu_intc_instance"):
        return "probe-could-not-resolve-vpu-intc"
    if not result.get("vpu_cpu_instance"):
        return "probe-could-not-resolve-vpu-cpu"
    return "vc4-cpu-interrupt-condition-or-injection-unavailable"


def write_markdown(path: Path, result: dict[str, Any]) -> None:
    lines = [
        "# VC4 interrupt delivery injection probe",
        "",
        f"Classification: **`{result.get('classification')}`**",
        f"Baseline low-PC reached: **{result.get('baseline', {}).get('reached')}**",
        f"Baseline stable PC: `{result.get('baseline', {}).get('stable_pc')}`",
        f"VPU interrupt controller: `{result.get('vpu_intc_instance')}`",
        f"VPU CPU object: `{result.get('vpu_cpu_instance')}`",
        "",
        "## VPU interrupt-controller input injections",
        "",
    ]
    for item in result.get("intc_injections", []):
        lines.append(
            f"- line `{item.get('number')}` via `{item.get('gpio_name')}`: "
            f"changed_pc=`{item.get('changed_pc')}`, "
            f"signature=`{item.get('signature_seen')}`, "
            f"before=`{item.get('before_pc')}`, after=`{item.get('after_pc')}`"
        )
        for error in item.get("errors", []):
            lines.append(f"  - error: `{error}`")
    lines.extend(["", "## Direct VC4 CPU input injection", ""])
    cpu = result.get("cpu_injection") or {}
    lines.append(
        f"- changed_pc=`{cpu.get('changed_pc')}`, "
        f"signature=`{cpu.get('signature_seen')}`, "
        f"GPIO=`{cpu.get('gpio_name')}`, "
        f"before=`{cpu.get('before_pc')}`, after=`{cpu.get('after_pc')}`"
    )
    for error in cpu.get("errors", []):
        lines.append(f"  - error: `{error}`")
    lines.extend([
        "",
        "## Decision rule",
        "",
        "A controller-input injection that moves the VPU proves the CPU and "
        "controller path can deliver an interrupt and points upstream at the "
        "raw peripheral source/mirror.  A direct CPU injection that moves the "
        "VPU while controller injection does not points at controller register "
        "or mask semantics.  Neither candidate is retained by this probe.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def probe(args: argparse.Namespace) -> int:
    qemu = args.qemu.resolve()
    image = args.image.resolve()
    if not qemu.is_file():
        raise SystemExit(f"not a QEMU executable: {qemu}")
    if not image.is_file():
        raise SystemExit(f"not an SD image: {image}")
    args.out.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vc4-irq-inject-") as tmp_s:
        tmp = Path(tmp_s)
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = args.out.with_suffix(".stderr")
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
        result: dict[str, Any] = {
            "schema_version": 1,
            "qemu": str(qemu),
            "image": str(image),
            "qemu_command": command,
        }
        try:
            qmp_obj = wait_for(qmp_path, proc, "qmp")
            qtest_obj = wait_for(qtest_path, proc, "qtest")
            assert isinstance(qmp_obj, QMP)
            assert isinstance(qtest_obj, QTest)
            qmp = qmp_obj
            qtest = qtest_obj
            vpu_index, vpu_type = find_vpu_cpu(qmp)
            result["vpu_cpu_index"] = vpu_index
            result["vpu_cpu_type"] = vpu_type
            instances = walk_qom(qmp)
            result["qom_instances"] = [
                {"path": path, "type": kind}
                for path, kind in instances
                if "vc4" in kind.lower() or "vc4" in path.lower()
            ]
            intc_instance = find_instance(
                instances, "vc4", "intc", prefer="vpu-intc0"
            )
            cpu_instance = find_instance(
                instances, "vc4", "cpu"
            )
            result["vpu_intc_instance"] = (
                None if intc_instance is None else
                {"path": intc_instance[0], "type": intc_instance[1]}
            )
            result["vpu_cpu_instance"] = (
                None if cpu_instance is None else
                {"path": cpu_instance[0], "type": cpu_instance[1]}
            )

            baseline = wait_for_low_pc(
                qmp, qtest, vpu_index, args.wait_seconds,
                args.stable_samples, args.interval
            )
            result["baseline"] = baseline
            result["intc_before"] = {
                "base": "0x3f002000",
                "values": mmio_window(qtest, 0x3F002000, 0x100),
            }
            result["arm_ic_before"] = {
                "base": "0x3f00b200",
                "values": mmio_window(qtest, 0x3F00B200, 0x40),
            }

            intc_attempts: list[dict[str, Any]] = []
            if baseline.get("reached") and intc_instance is not None:
                line_numbers = [
                    int(value, 0) for value in args.lines.split(",")
                    if value.strip()
                ]
                for line in line_numbers:
                    attempt = attempt_irq(
                        qtest, qmp, vpu_index, intc_instance[0],
                        ("gpu-irq", "gpu_irq", "irq"), line,
                        args.pulse_seconds, args.interval
                    )
                    intc_attempts.append(attempt)
                    if attempt.get("signature_seen") or attempt.get("changed_pc"):
                        break
            result["intc_injections"] = intc_attempts
            result["intc_after"] = {
                "base": "0x3f002000",
                "values": mmio_window(qtest, 0x3F002000, 0x100),
            }

            cpu_attempt: dict[str, Any] | None = None
            if (baseline.get("reached") and
                    not any(item.get("changed_pc") for item in intc_attempts) and
                    cpu_instance is not None):
                cpu_attempt = attempt_irq(
                    qtest, qmp, vpu_index, cpu_instance[0],
                    ("unnamed-gpio-in", "irq"), 0,
                    args.pulse_seconds, args.interval
                )
            result["cpu_injection"] = cpu_attempt
            result["observed_signature"] = (
                f"0x{qtest.readq(SIGNATURE_ADDR):016x}"
            )
            result["final_pc"] = (
                None if vpu_pc(qmp, vpu_index) is None else
                f"0x{vpu_pc(qmp, vpu_index):08x}"
            )
            result["cpu_snapshot"] = qmp.execute("query-cpus-fast")
            result["info_cpus"] = qmp.hmp("info cpus")
            result["classification"] = classify(result)
        except Exception as exc:
            result["probe_error"] = f"{type(exc).__name__}: {exc}"
            result["classification"] = "probe-error"
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()
            stop_process(proc)

        result["qemu_stderr_tail"] = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()[-1000:]
        args.out.write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_markdown(args.out.with_suffix(".md"), result)
        print(json.dumps({
            "classification": result.get("classification"),
            "baseline": result.get("baseline"),
            "intc_injections": result.get("intc_injections"),
            "cpu_injection": result.get("cpu_injection"),
            "probe_error": result.get("probe_error"),
        }, indent=2))
        return 1 if result.get("probe_error") else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--wait-seconds", type=float, default=20.0)
    parser.add_argument("--stable-samples", type=int, default=128)
    parser.add_argument("--interval", type=float, default=0.005)
    parser.add_argument("--pulse-seconds", type=float, default=0.08)
    parser.add_argument(
        "--lines",
        default=",".join(str(value) for value in range(64)),
        help="comma-separated VPU interrupt-controller input lines",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if any(value <= 0 for value in (
        args.wait_seconds, args.stable_samples,
        args.interval, args.pulse_seconds,
    )):
        parser.error("timing arguments must be positive")
    return probe(args)


if __name__ == "__main__":
    raise SystemExit(main())
