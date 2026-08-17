#!/usr/bin/env python3
"""Exercise both VideoCore IV software-interrupt encodings.

The focused image runs on the real heterogeneous Raspberry Pi 3 machine so it
also verifies that the VPU CPU is wired to IC0.  A generic loader places the
image in the VPU-private 128 KiB boot cache and selects that CPU's address
space, avoiding the 0x3c000000 carve-out/peripheral decode collision.  The
machine's ``-kernel`` path is retained only to satisfy its firmware source
requirement; QMP redirects the stopped VPU to the boot-cache harness before it
is allowed to execute.  The image programs IC0_VADDR, enters immediate and
register-form SWIs, and checks the architectural exception frame through QMP.

The test deliberately uses branch-to-self sentinels rather than BKPT/SLEEP as
its completion signal.  Board interrupt activity may wake a halted VPU while
the monitor samples it; a stable architectural loop makes the SWI and RTI
checks independent of that unrelated scheduler behaviour.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import socket
import struct
import subprocess
import tempfile
import time
from typing import Any

IMAGE_BASE = 0x00004000
HANDLER = 0x00006000
VECTOR_BASE = 0x00008000
EXCEPTION_STACK_TOP = 0x0001F000
IC0_VADDR = 0x7E002030
VC4_SR_S = 1 << 29
IMMEDIATE_MAGIC = 0x51A10020
REGISTER_MAGIC = 0x51A10021


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
        value = json.loads(line)
        if not isinstance(value, dict):
            raise RuntimeError(f"invalid QMP response: {value!r}")
        return value

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


def find_vc4_cpu(cpus: Any) -> int:
    if not isinstance(cpus, list):
        raise RuntimeError(f"query-cpus-fast returned {cpus!r}")

    qom_types: list[str] = []
    for cpu in cpus:
        if not isinstance(cpu, dict):
            continue
        qom_type = str(cpu.get("qom-type", ""))
        qom_types.append(qom_type)
        if "vc4" not in qom_type.lower():
            continue
        index = cpu.get("cpu-index")
        if isinstance(index, int):
            return index
        raise RuntimeError(f"VC4 CPU has no integer cpu-index: {cpu!r}")

    raise RuntimeError(f"no VC4 CPU in qom-types={qom_types!r}")


def mov32(register: int, value: int) -> bytes:
    if not 0 <= register < 32:
        raise ValueError(register)
    return struct.pack("<HHH", 0xE800 | register,
                       value & 0xFFFF, value >> 16)


def store_word(source: int, base: int) -> bytes:
    if not 0 <= source < 16 or not 0 <= base < 16:
        raise ValueError((source, base))
    return struct.pack("<H", 0x0900 | (base << 4) | source)


def branch_register(register: int) -> bytes:
    if not 0 <= register < 32:
        raise ValueError(register)
    return struct.pack("<H", 0x0040 | register)


def common_prefix() -> bytes:
    return b"".join((
        mov32(0, IC0_VADDR),
        mov32(1, VECTOR_BASE),
        store_word(1, 0),
        mov32(28, EXCEPTION_STACK_TOP),
    ))


def loop_sentinel(address: int, magic: int) -> bytes:
    """Set r3 to *magic* and branch forever at address + 12."""
    loop = address + 12
    return b"".join((
        mov32(3, magic),
        mov32(4, loop),
        branch_register(4),
    ))


def make_image(register_form: bool) -> tuple[bytes, int, int, int, int]:
    image = bytearray(VECTOR_BASE - IMAGE_BASE + 0x1000)
    code = bytearray(common_prefix())

    if register_form:
        code.extend(mov32(2, 1))
        code.extend(struct.pack("<H", 0x0022))  # swi r2
        vector = 33
        return_pc = IMAGE_BASE + len(code)
        sentinel = loop_sentinel(return_pc, REGISTER_MAGIC)
        code.extend(sentinel)
        final_pc = return_pc + 12
        magic = REGISTER_MAGIC
        image[HANDLER - IMAGE_BASE:HANDLER - IMAGE_BASE + 2] = \
            struct.pack("<H", 0x000A)  # rti
    else:
        code.extend(struct.pack("<H", 0x01C0))  # swi 0
        vector = 32
        return_pc = IMAGE_BASE + len(code)
        # A return from the immediate case is a test failure, but leave a
        # deterministic loop behind it rather than falling through zero RAM.
        code.extend(loop_sentinel(return_pc, 0xBAD00020))
        handler = loop_sentinel(HANDLER, IMMEDIATE_MAGIC)
        image[HANDLER - IMAGE_BASE:HANDLER - IMAGE_BASE + len(handler)] = handler
        final_pc = HANDLER + 12
        magic = IMMEDIATE_MAGIC

    image[:len(code)] = code
    struct.pack_into("<I", image,
                     VECTOR_BASE - IMAGE_BASE + vector * 4,
                     HANDLER | 1)
    return bytes(image), vector, return_pc, final_pc, magic


def parse_register(output: str, name: str) -> int:
    match = re.search(rf"\b{name}=([0-9a-fA-F]{{8}})\b", output)
    if not match:
        raise RuntimeError(f"{name} missing from registers:\n{output}")
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


def run_case(qemu: Path, register_form: bool) -> dict[str, Any]:
    image, vector, return_pc, final_pc, magic = make_image(register_form)
    name = "register" if register_form else "immediate"

    with tempfile.TemporaryDirectory(prefix=f"vc4-swi-{name}-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "swi.bin"
        qmp_path = tmp / "qmp.sock"
        log_path = tmp / "qemu.log"
        stderr_path = tmp / "qemu.stderr"
        stack_path = tmp / "stack.bin"
        image_path.write_bytes(image)

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-accel", "tcg,thread=single,one-insn-per-tb=on",
            "-kernel", str(image_path),
            "-device",
            (f"loader,file={image_path},addr=0x{IMAGE_BASE:x},"
             "cpu-num=4,force-raw=on"),
            "-S",
            "-display", "none",
            "-serial", "none",
            "-monitor", "none",
            "-qmp", f"unix:{qmp_path},server=on,wait=off",
            "-d", "int,guest_errors",
            "-D", str(log_path),
            "-no-reboot",
        ]
        with stderr_path.open("wb") as stderr:
            process = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=stderr
            )

        qmp: QMP | None = None
        registers = ""
        try:
            qmp = QMP(qmp_path, process, stderr_path)
            cpu_index = find_vc4_cpu(qmp.execute("query-cpus-fast"))
            if cpu_index != 4:
                raise RuntimeError(f"VC4 CPU index is {cpu_index}, expected 4")

            # The machine-level -kernel path establishes the required firmware
            # source and initially selects the VCRAM address.  The focused raw
            # image was separately loaded into the VPU-private cache, so point
            # only the stopped VPU at that entry before allowing execution.
            qmp.hmp(f"set $pc = 0x{IMAGE_BASE:x}", cpu_index=cpu_index)
            registers = qmp.hmp("info registers", cpu_index=cpu_index)
            if parse_register(registers, "pc") != IMAGE_BASE:
                raise RuntimeError(
                    "failed to redirect VPU to the boot-cache harness:\n"
                    + registers
                )

            qmp.execute("cont")
            deadline = time.monotonic() + 8.0
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    stderr = stderr_path.read_text(
                        encoding="utf-8", errors="replace"
                    )
                    raise RuntimeError(
                        f"QEMU exited with status {process.returncode}:\n{stderr}"
                    )
                registers = qmp.hmp("info registers", cpu_index=cpu_index)
                if (parse_register(registers, "r3") == magic and
                        parse_register(registers, "pc") == final_pc):
                    break
                time.sleep(0.005)
            else:
                log = log_path.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(
                    f"CPU did not reach {name} sentinel "
                    f"pc=0x{final_pc:08x} magic=0x{magic:08x}:\n"
                    f"{registers}\nQEMU log:\n{log}"
                )

            qmp.execute("stop")
            qmp.hmp(
                f"memsave 0x{EXCEPTION_STACK_TOP - 8:x} 8 {stack_path}",
                cpu_index=cpu_index,
            )
        finally:
            if qmp is not None:
                qmp.close()
            stop_process(process)

        if not stack_path.is_file():
            raise RuntimeError("memsave did not create the stack image")
        saved_sr, saved_pc = struct.unpack("<II", stack_path.read_bytes())
        log = log_path.read_text(encoding="utf-8", errors="replace")
        stderr = stderr_path.read_text(encoding="utf-8", errors="replace")

        if saved_sr != 0 or saved_pc != return_pc:
            raise RuntimeError(
                f"{name} SWI saved sr=0x{saved_sr:08x} "
                f"pc=0x{saved_pc:08x}, expected sr=0 "
                f"pc=0x{return_pc:08x}"
            )
        if f"VideoCore IV: SWI vector={vector} " not in log:
            raise RuntimeError(f"{name} SWI entry was not logged:\n{log}")

        pc = parse_register(registers, "pc")
        sr = parse_register(registers, "sr")
        sp = parse_register(registers, "r25")
        exception_sp = parse_register(registers, "r28")
        observed_magic = parse_register(registers, "r3")
        if pc != final_pc or observed_magic != magic:
            raise RuntimeError(
                f"{name} sentinel pc=0x{pc:08x} "
                f"magic=0x{observed_magic:08x}"
            )
        if exception_sp != EXCEPTION_STACK_TOP:
            raise RuntimeError(
                f"{name} r28 0x{exception_sp:08x}, expected "
                f"0x{EXCEPTION_STACK_TOP:08x}"
            )

        if register_form:
            if sr != 0 or sp != 0:
                raise RuntimeError(
                    "register SWI did not restore sr/sp: "
                    f"sr=0x{sr:08x} sp=0x{sp:08x}"
                )
            if "VideoCore IV: RTI " not in log:
                raise RuntimeError("register SWI did not execute RTI")
        elif sr != VC4_SR_S or sp != EXCEPTION_STACK_TOP - 8:
            raise RuntimeError(
                f"immediate SWI entry state sr=0x{sr:08x} "
                f"sp=0x{sp:08x}"
            )

        return {
            "case": name,
            "vector": vector,
            "return_pc": f"0x{return_pc:08x}",
            "final_pc": f"0x{pc:08x}",
            "magic": f"0x{observed_magic:08x}",
            "saved_sr": f"0x{saved_sr:08x}",
            "saved_pc": f"0x{saved_pc:08x}",
            "sr": f"0x{sr:08x}",
            "sp": f"0x{sp:08x}",
            "r28": f"0x{exception_sp:08x}",
            "stderr": stderr,
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", type=Path)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    summary = {
        "schema_version": 1,
        "cases": [
            run_case(qemu, register_form=False),
            run_case(qemu, register_form=True),
        ],
    }
    rendered = json.dumps(summary, indent=2, sort_keys=True) + "\n"
    if args.json:
        args.json.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
