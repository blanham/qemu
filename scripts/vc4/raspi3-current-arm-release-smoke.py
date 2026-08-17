#!/usr/bin/env python3
"""Prove that the current VC4-first Pi 3 model can start Cortex-A53 CPU0.

The test keeps the VideoCore and ARM payloads separate so each generic loader
writes through the correct CPU address space.  The VPU performs the same ARM
control and PM_PROC sequence used by start.elf, publishes the register state
through the GPU RAM alias, and then waits in a stable loop.  CPU0 begins in the
architectural powered-off state and writes an AArch64 marker only after the VPU
has released it.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any

VC4_ENTRY = 0x00010000
ARM_MARKER_ADDR = 0x00002000
OBSERVATION_ADDR = 0x00003000
ARM_MARKER = 0x52504933          # "RPI3"
VC4_DONE = 0x56433444            # "VC4D"

ARM_VIEW_BASE = 0x3F000000
ARM_CONTROL0 = ARM_VIEW_BASE + 0x0000B000
ARM_CONTROL1 = ARM_VIEW_BASE + 0x0000B440
ARM_STATUS = ARM_VIEW_BASE + 0x0000B444
ARM_ID = ARM_VIEW_BASE + 0x0000B44C
PM_PROC = ARM_VIEW_BASE + 0x00100110

VC_VIEW_ARM_CONTROL = 0x7E00B000
VC_VIEW_PM = 0x7E100000
VC_VIEW_RAM = 0xC0000000

CONTROL0_VALUE = 0x0000A243
CONTROL1_VALUE = 0x00000100
PM_PROC_VALUE = 0x0000007F
ARM_ID_VALUE = 0x364D5241


class QMP:
    def __init__(self, path: Path, process: subprocess.Popen[bytes],
                 stderr_path: Path, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None

        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                raise RuntimeError(
                    f"QEMU exited with status {process.returncode}:\n{stderr}"
                )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(path))
            except OSError as error:
                last_error = error
                client.close()
                time.sleep(0.01)
                continue
            self.socket = client
            self.stream = client.makefile("rwb", buffering=0)
            greeting = self._read()
            if "QMP" not in greeting:
                raise RuntimeError(f"invalid QMP greeting: {greeting!r}")
            self.execute("qmp_capabilities")
            return

        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(
            f"could not connect to QMP: {last_error}\nQEMU stderr:\n{stderr}"
        )

    def _read(self) -> dict[str, Any]:
        line = self.stream.readline()
        if not line:
            raise RuntimeError("QMP connection closed")
        message = json.loads(line)
        if not isinstance(message, dict):
            raise RuntimeError(f"invalid QMP response: {message!r}")
        return message

    def execute(self, command: str,
                arguments: dict[str, Any] | None = None) -> Any:
        request: dict[str, Any] = {"execute": command}
        if arguments:
            request["arguments"] = arguments
        self.stream.write(json.dumps(request).encode("utf-8") + b"\n")
        while True:
            response = self._read()
            if "event" in response:
                continue
            if "error" in response:
                raise RuntimeError(
                    f"QMP {command} failed: {response['error']!r}"
                )
            if "return" in response:
                return response["return"]

    def hmp(self, command: str, *, cpu_index: int | None = None) -> str:
        arguments: dict[str, Any] = {"command-line": command}
        if cpu_index is not None:
            arguments["cpu-index"] = cpu_index
        result = self.execute("human-monitor-command", arguments)
        if not isinstance(result, str):
            raise RuntimeError(f"HMP returned non-string {result!r}")
        return result

    def close(self) -> None:
        self.stream.close()
        self.socket.close()


class QTest:
    def __init__(self, path: Path, process: subprocess.Popen[bytes],
                 stderr_path: Path, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        last_error: OSError | None = None

        while time.monotonic() < deadline:
            if process.poll() is not None:
                stderr = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                raise RuntimeError(
                    f"QEMU exited with status {process.returncode}:\n{stderr}"
                )
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                client.connect(str(path))
            except OSError as error:
                last_error = error
                client.close()
                time.sleep(0.01)
                continue
            self.socket = client
            self.stream = client.makefile("rwb", buffering=0)
            return

        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")
        raise RuntimeError(
            f"could not connect to qtest: {last_error}\nQEMU stderr:\n{stderr}"
        )

    def command(self, line: str) -> str:
        self.stream.write(line.encode("ascii") + b"\n")
        reply = self.stream.readline()
        if not reply:
            raise RuntimeError(f"qtest closed while waiting for {line!r}")
        return reply.decode("ascii", errors="replace").strip()

    def readl(self, address: int) -> int:
        reply = self.command(f"readl 0x{address:x}")
        fields = reply.split()
        if len(fields) != 2 or fields[0] != "OK":
            raise RuntimeError(f"unexpected qtest reply: {reply!r}")
        return int(fields[1], 0)

    def close(self) -> None:
        self.stream.close()
        self.socket.close()


def half(value: int) -> bytes:
    return struct.pack("<H", value & 0xFFFF)


def word(value: int) -> bytes:
    return struct.pack("<I", value & 0xFFFFFFFF)


def vc4_mov32(register: int, value: int) -> bytes:
    return half(0xE800 | (register & 0x1F)) + word(value)


def vc4_memory_offset(store: bool, register: int, base: int,
                      offset: int, fmt: int = 0) -> bytes:
    if not -2048 <= offset <= 2047:
        raise ValueError(f"VC4 memory offset out of range: {offset}")
    raw = offset & 0xFFF
    i1 = 0xA200 | (0x20 if store else 0) | \
         ((fmt & 3) << 6) | (register & 0x1F)
    if raw & 0x800:
        i1 |= 0x100
    i2 = ((base & 0x1F) << 11) | (raw & 0x7FF)
    return half(i1) + half(i2)


def vc4_branch_register(register: int) -> bytes:
    return half(0x0040 | (register & 0x1F))


def a64_movz(register: int, immediate: int, shift: int = 0,
             *, sf: bool = True) -> int:
    base = 0xD2800000 if sf else 0x52800000
    return base | ((shift // 16) << 21) | \
           ((immediate & 0xFFFF) << 5) | register


def a64_movk(register: int, immediate: int, shift: int = 0,
             *, sf: bool = True) -> int:
    base = 0xF2800000 if sf else 0x72800000
    return base | ((shift // 16) << 21) | \
           ((immediate & 0xFFFF) << 5) | register


def build_arm_payload() -> bytes:
    return b"".join((
        word(a64_movz(0, ARM_MARKER_ADDR)),
        word(a64_movz(1, ARM_MARKER & 0xFFFF, sf=False)),
        word(a64_movk(1, ARM_MARKER >> 16, shift=16, sf=False)),
        word(0xB9000001),            # str w1, [x0]
        word(0x14000000),            # b .
    ))


def build_vc4_payload() -> bytes:
    code = bytearray()

    # Select AArch64, one-gigabyte ARM memory, pass-through APROT, and full
    # peripheral access through the VideoCore-visible ARM control block.
    code += vc4_mov32(0, VC_VIEW_ARM_CONTROL)
    code += vc4_mov32(1, CONTROL0_VALUE)
    code += vc4_memory_offset(True, 1, 0, 0x000)
    code += vc4_memory_offset(False, 2, 0, 0x44C)  # ARM_ID

    # Execute the firmware power-domain sequence.  The power model supplies
    # hardware-owned POWOK and MRDONE response bits.
    code += vc4_mov32(3, VC_VIEW_PM)
    for value in (0x00, 0x01, 0x05, 0x0D, 0x2D, 0x6D):
        code += vc4_mov32(1, 0x5A000000 | value)
        code += vc4_memory_offset(True, 1, 3, 0x110)
    code += vc4_memory_offset(False, 4, 3, 0x110)

    # Clear REQSTOP after PM_PROC reports the ARM domain operational.
    code += vc4_mov32(1, CONTROL1_VALUE)
    code += vc4_memory_offset(True, 1, 0, 0x440)
    code += vc4_memory_offset(False, 5, 0, 0x440)
    code += vc4_memory_offset(False, 6, 0, 0x444)

    # Publish the observed state through the VC cached RAM alias.
    code += vc4_mov32(7, VC_VIEW_RAM + OBSERVATION_ADDR)
    code += vc4_memory_offset(True, 2, 7, 0x00)
    code += vc4_memory_offset(True, 4, 7, 0x04)
    code += vc4_memory_offset(True, 5, 7, 0x08)
    code += vc4_memory_offset(True, 6, 7, 0x0C)
    code += vc4_mov32(1, VC4_DONE)
    code += vc4_memory_offset(True, 1, 7, 0x10)

    # Stable completion sentinel, independent of board interrupt activity.
    loop_pc = VC4_ENTRY + len(code) + 6
    code += vc4_mov32(9, loop_pc)
    code += vc4_branch_register(9)
    return bytes(code)


def topology(cpus: Any) -> tuple[int, list[str]]:
    if not isinstance(cpus, list) or len(cpus) != 5:
        raise RuntimeError(f"expected four A53s plus one VPU, got {cpus!r}")
    qom_types = [
        str(cpu.get("qom-type", ""))
        for cpu in cpus if isinstance(cpu, dict)
    ]
    if sum("cortex-a53" in item for item in qom_types) != 4:
        raise RuntimeError(f"expected four Cortex-A53 CPUs: {qom_types!r}")
    for cpu in cpus:
        if not isinstance(cpu, dict):
            continue
        if "vc4" in str(cpu.get("qom-type", "")).lower():
            index = cpu.get("cpu-index")
            if not isinstance(index, int):
                raise RuntimeError(f"VC4 CPU has no index: {cpu!r}")
            return index, qom_types
    raise RuntimeError(f"no VC4 CPU in {qom_types!r}")


def parse_pc(registers: str) -> int:
    match = re.search(r"\bpc\s*=\s*([0-9a-fA-F]{8})\b", registers)
    if not match:
        raise RuntimeError(f"PC missing from registers:\n{registers}")
    return int(match.group(1), 16)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-arm-release-") as tmp_s:
        tmp = Path(tmp_s)
        arm_path = tmp / "arm.bin"
        vc4_path = tmp / "vc4.bin"
        qmp_path = tmp / "qmp.sock"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        arm_path.write_bytes(build_arm_payload())
        vc4_path.write_bytes(build_vc4_payload())

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-kernel", str(vc4_path),
            "-device",
            (f"loader,file={arm_path},addr=0x0,"
             "cpu-num=0,force-raw=on"),
            "-device",
            (f"loader,file={vc4_path},addr=0x{VC4_ENTRY:x},"
             "cpu-num=4,force-raw=on"),
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-S",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-d", "guest_errors,unimp",
            "-no-reboot",
        ]
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=stderr
            )

        qmp: QMP | None = None
        qtest: QTest | None = None
        try:
            qmp = QMP(qmp_path, process, stderr_path)
            qtest = QTest(qtest_path, process, stderr_path)
            vpu_index, qom_types = topology(qmp.execute("query-cpus-fast"))
            if vpu_index != 4:
                raise RuntimeError(f"VC4 CPU index is {vpu_index}, expected 4")

            initial = {
                "control1": qtest.readl(ARM_CONTROL1),
                "pm_proc": qtest.readl(PM_PROC),
                "marker": qtest.readl(ARM_MARKER_ADDR),
            }
            if initial != {"control1": 0x200, "pm_proc": 0, "marker": 0}:
                raise RuntimeError(f"incorrect ARM held-reset state: {initial!r}")

            qmp.hmp(f"set $pc = 0x{VC4_ENTRY:x}", cpu_index=vpu_index)
            vpu_registers = qmp.hmp("info registers", cpu_index=vpu_index)
            if parse_pc(vpu_registers) != VC4_ENTRY:
                raise RuntimeError(
                    "failed to redirect VPU to release payload:\n"
                    + vpu_registers
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 10.0
            marker = done = 0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stderr = stderr_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    raise RuntimeError(
                        f"QEMU exited with status {process.returncode}:\n{stderr}"
                    )
                marker = qtest.readl(ARM_MARKER_ADDR)
                done = qtest.readl(OBSERVATION_ADDR + 0x10)
                if marker == ARM_MARKER and done == VC4_DONE:
                    break
                time.sleep(0.005)
            else:
                raise RuntimeError(
                    "ARM release timed out: "
                    f"marker=0x{marker:08x} done=0x{done:08x}"
                )

            qmp.execute("stop")
            observations = {
                "arm_id": qtest.readl(OBSERVATION_ADDR + 0x00),
                "pm_proc": qtest.readl(OBSERVATION_ADDR + 0x04),
                "control1": qtest.readl(OBSERVATION_ADDR + 0x08),
                "status": qtest.readl(OBSERVATION_ADDR + 0x0C),
                "done": done,
            }
            registers = {
                "control0": qtest.readl(ARM_CONTROL0),
                "control1": qtest.readl(ARM_CONTROL1),
                "status": qtest.readl(ARM_STATUS),
                "arm_id": qtest.readl(ARM_ID),
                "pm_proc": qtest.readl(PM_PROC),
            }
            expected_observations = {
                "arm_id": ARM_ID_VALUE,
                "pm_proc": PM_PROC_VALUE,
                "control1": CONTROL1_VALUE,
                "status": 0,
                "done": VC4_DONE,
            }
            expected_registers = {
                "control0": CONTROL0_VALUE,
                "control1": CONTROL1_VALUE,
                "status": 0,
                "arm_id": ARM_ID_VALUE,
                "pm_proc": PM_PROC_VALUE,
            }
            if observations != expected_observations:
                raise RuntimeError(
                    f"VC4 observations differ: {observations!r} != "
                    f"{expected_observations!r}"
                )
            if registers != expected_registers:
                raise RuntimeError(
                    f"ARM bridge state differs: {registers!r} != "
                    f"{expected_registers!r}"
                )

            arm_registers = qmp.hmp("info registers", cpu_index=0)
            arm_pc_match = re.search(
                r"\bPC=([0-9a-fA-F]{16})\b", arm_registers
            )
            summary = {
                "schema_version": 1,
                "qom_types": qom_types,
                "vpu_cpu_index": vpu_index,
                "initial": initial,
                "marker": f"0x{marker:08x}",
                "observations": {
                    key: f"0x{value:08x}"
                    for key, value in observations.items()
                },
                "registers": {
                    key: f"0x{value:08x}"
                    for key, value in registers.items()
                },
                "arm_pc": (
                    f"0x{int(arm_pc_match.group(1), 16):016x}"
                    if arm_pc_match else "unparsed"
                ),
            }
            rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
            if args.json:
                args.json.write_text(rendered)
            print(rendered, end="")
            qmp.execute("quit")
            process.wait(timeout=5)
            return 0
        except Exception:
            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            if diagnostics:
                print("--- qemu stderr ---", file=os.sys.stderr)
                print(diagnostics, file=os.sys.stderr)
            raise
        finally:
            if qtest is not None:
                qtest.close()
            if qmp is not None:
                qmp.close()
            stop_process(process)


if __name__ == "__main__":
    raise SystemExit(main())
