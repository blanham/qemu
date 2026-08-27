#!/usr/bin/env python3
"""Exercise the initial BCM2835 V3D register and clear-render slice."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import socket
import struct
import subprocess
import tempfile
import time


V3D_BASE = 0x3FC00000
V3D_IDENT0 = V3D_BASE + 0x000
V3D_IDENT1 = V3D_BASE + 0x004
V3D_SCRATCH = V3D_BASE + 0x010
V3D_INTCTL = V3D_BASE + 0x030
V3D_INTENA = V3D_BASE + 0x034
V3D_CT1CS = V3D_BASE + 0x104
V3D_CT1EA = V3D_BASE + 0x10C
V3D_CT1CA = V3D_BASE + 0x114
V3D_CT1RA0 = V3D_BASE + 0x11C
V3D_CT1LC = V3D_BASE + 0x124
V3D_RFC = V3D_BASE + 0x138
V3D_ERRSTAT = V3D_BASE + 0xF20

V3D_EXPECTED_IDENT0 = (2 << 24) | (ord("D") << 16) | (ord("3") << 8) | ord("V")
V3D_INT_FRDONE = 1 << 0
V3D_CTRSTA = 1 << 15
V3D_CTSUBS = 1 << 4
V3D_CTERR = 1 << 3

VC4_PACKET_HALT = 0
VC4_PACKET_BRANCH = 16
VC4_PACKET_BRANCH_TO_SUB_LIST = 17
VC4_PACKET_RETURN_FROM_SUB_LIST = 18
VC4_PACKET_STORE_MS_TILE_BUFFER_EOF = 25
VC4_PACKET_STORE_TILE_BUFFER_GENERAL = 28
VC4_PACKET_GL_ARRAY_PRIMITIVE = 33
VC4_PACKET_TILE_RENDERING_MODE_CONFIG = 113
VC4_PACKET_CLEAR_COLORS = 114
VC4_PACKET_TILE_COORDINATES = 115
VC4_RENDER_CONFIG_FORMAT_RGBA8888 = 1 << 2

CL_ADDRESS = 0x00080000
BAD_CL_ADDRESS = 0x00081000
SUB_LIST_ADDRESS = 0x00082000
CONTROL_FLOW_CL_ADDRESS = 0x00083000
BRANCH_CL_ADDRESS = 0x00084000
RETURN_ERROR_CL_ADDRESS = 0x00085000
NESTED_MAIN_ADDRESS = 0x00086000
NESTED_LEVEL_1_ADDRESS = 0x00087000
NESTED_LEVEL_2_ADDRESS = 0x00088000
FRAMEBUFFER_ADDRESS = 0x00100000
WIDTH = 64
HEIGHT = 64
CLEAR_COLOR = 0xFF3366CC


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, text: str) -> str:
        try:
            self.file.write(text.encode("ascii") + b"\n")
            reply = self.file.readline()
        except OSError as exc:
            raise RuntimeError(
                f"qtest transport failed while handling {text!r}: {exc}"
            ) from exc
        if not reply:
            raise RuntimeError(f"qtest closed while waiting for {text!r}")
        result = reply.decode("ascii", errors="replace").strip()
        if not result.startswith("OK"):
            raise RuntimeError(f"qtest rejected {text!r}: {result!r}")
        return result

    def readl(self, address: int) -> int:
        fields = self.command(f"readl 0x{address:x}").split()
        if len(fields) != 2:
            raise RuntimeError(f"malformed qtest read reply: {fields!r}")
        return int(fields[1], 0)

    def writel(self, address: int, value: int) -> None:
        reply = self.command(
            f"writel 0x{address:x} 0x{value & 0xffffffff:x}"
        )
        if reply != "OK":
            raise RuntimeError(f"malformed qtest write reply: {reply!r}")

    def write_blob(self, address: int, data: bytes) -> None:
        padded = data + bytes((-len(data)) & 3)
        for offset in range(0, len(padded), 4):
            word = int.from_bytes(padded[offset:offset + 4], "little")
            self.writel(address + offset, word)

    def close(self) -> None:
        try:
            self.file.close()
        finally:
            self.sock.close()


def wait_for_qtest(path: Path, proc: subprocess.Popen[bytes],
                   timeout: float = 15.0) -> QTest:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(
                f"QEMU exited before qtest connected (status {proc.returncode})"
            )
        try:
            return QTest(path)
        except (FileNotFoundError, ConnectionRefusedError) as exc:
            last_error = exc
            time.sleep(0.02)
    raise TimeoutError(f"qtest socket did not appear: {path}") from last_error


def expect(label: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise RuntimeError(
            f"{label}: got 0x{actual:08x}, expected 0x{expected:08x}"
        )


def build_clear_rcl() -> bytes:
    data = bytearray()
    data.append(VC4_PACKET_TILE_RENDERING_MODE_CONFIG)
    data += struct.pack(
        "<IHHH",
        FRAMEBUFFER_ADDRESS,
        WIDTH,
        HEIGHT,
        VC4_RENDER_CONFIG_FORMAT_RGBA8888,
    )
    data.append(VC4_PACKET_CLEAR_COLORS)
    data += struct.pack("<III", CLEAR_COLOR, CLEAR_COLOR, 0x00FFFFFF)
    data.append(0)

    # Trigger the new clear values without writing a buffer.
    data += bytes((VC4_PACKET_TILE_COORDINATES, 0, 0))
    data.append(VC4_PACKET_STORE_TILE_BUFFER_GENERAL)
    data += struct.pack("<HI", 0, 0)

    data += bytes((VC4_PACKET_TILE_COORDINATES, 0, 0))
    data.append(VC4_PACKET_STORE_MS_TILE_BUFFER_EOF)
    data.append(VC4_PACKET_HALT)
    return bytes(data)


def build_control_flow_rcl() -> tuple[bytes, bytes]:
    main = bytearray()
    main.append(VC4_PACKET_TILE_RENDERING_MODE_CONFIG)
    main += struct.pack(
        "<IHHH",
        FRAMEBUFFER_ADDRESS,
        WIDTH,
        HEIGHT,
        VC4_RENDER_CONFIG_FORMAT_RGBA8888,
    )
    main.append(VC4_PACKET_CLEAR_COLORS)
    main += struct.pack("<III", CLEAR_COLOR, CLEAR_COLOR, 0x00FFFFFF)
    main.append(0)
    main.append(VC4_PACKET_BRANCH_TO_SUB_LIST)
    main += struct.pack("<I", SUB_LIST_ADDRESS)
    main.append(VC4_PACKET_HALT)

    sub_list = bytearray((VC4_PACKET_TILE_COORDINATES, 0, 0))
    sub_list.append(VC4_PACKET_STORE_MS_TILE_BUFFER_EOF)
    sub_list.append(VC4_PACKET_RETURN_FROM_SUB_LIST)
    return bytes(main), bytes(sub_list)


def build_branch_rcl(address: int) -> bytes:
    skipped = bytes((VC4_PACKET_GL_ARRAY_PRIMITIVE,)) + bytes(9)
    target = address + 5 + len(skipped)
    return (
        bytes((VC4_PACKET_BRANCH,)) + struct.pack("<I", target) +
        skipped + bytes((VC4_PACKET_HALT,))
    )


def build_nested_sub_lists() -> tuple[bytes, bytes, bytes]:
    main = (
        bytes((VC4_PACKET_BRANCH_TO_SUB_LIST,)) +
        struct.pack("<I", NESTED_LEVEL_1_ADDRESS) +
        bytes((VC4_PACKET_HALT,))
    )
    level_1 = (
        bytes((VC4_PACKET_BRANCH_TO_SUB_LIST,)) +
        struct.pack("<I", NESTED_LEVEL_2_ADDRESS) +
        bytes((VC4_PACKET_RETURN_FROM_SUB_LIST,))
    )
    level_2 = (
        bytes((VC4_PACKET_BRANCH_TO_SUB_LIST,)) +
        struct.pack("<I", SUB_LIST_ADDRESS) +
        bytes((VC4_PACKET_RETURN_FROM_SUB_LIST,))
    )
    return main, level_1, level_2


def build_unsupported_rcl() -> bytes:
    # Primitive execution is intentionally rejected until binning/QPU support
    # exists.  The test prevents a future regression into fake completions.
    return bytes((VC4_PACKET_GL_ARRAY_PRIMITIVE,)) + bytes(9)


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def exercise(qtest: QTest) -> None:
    expect("V3D identity", qtest.readl(V3D_IDENT0), V3D_EXPECTED_IDENT0)
    ident1 = qtest.readl(V3D_IDENT1)
    if ((ident1 >> 4) & 0xF) == 0 or ((ident1 >> 8) & 0xF) == 0:
        raise RuntimeError(f"implausible V3D_IDENT1 value: 0x{ident1:08x}")

    qtest.writel(V3D_SCRATCH, 0xA5C35A3C)
    expect("scratch round trip", qtest.readl(V3D_SCRATCH), 0xA5C35A3C)

    clear_rcl = build_clear_rcl()
    qtest.write_blob(CL_ADDRESS, clear_rcl)
    qtest.writel(FRAMEBUFFER_ADDRESS, 0)
    qtest.writel(FRAMEBUFFER_ADDRESS + (WIDTH * HEIGHT - 1) * 4, 0)

    qtest.writel(V3D_INTENA, V3D_INT_FRDONE)
    qtest.writel(V3D_CT1CA, CL_ADDRESS)
    qtest.writel(V3D_CT1EA, CL_ADDRESS + len(clear_rcl))

    expect("render control-list current address",
           qtest.readl(V3D_CT1CA), CL_ADDRESS + len(clear_rcl))
    expect("render frame count", qtest.readl(V3D_RFC), 1)
    expect("render thread status", qtest.readl(V3D_CT1CS), 0)
    expect("render-done interrupt", qtest.readl(V3D_INTCTL),
           V3D_INT_FRDONE)
    expect("top-left clear pixel", qtest.readl(FRAMEBUFFER_ADDRESS),
           CLEAR_COLOR)
    expect("center clear pixel",
           qtest.readl(FRAMEBUFFER_ADDRESS +
                       ((HEIGHT // 2) * WIDTH + WIDTH // 2) * 4),
           CLEAR_COLOR)
    expect("bottom-right clear pixel",
           qtest.readl(FRAMEBUFFER_ADDRESS + (WIDTH * HEIGHT - 1) * 4),
           CLEAR_COLOR)

    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)
    expect("render-done acknowledgement", qtest.readl(V3D_INTCTL), 0)

    main_rcl, sub_list = build_control_flow_rcl()
    qtest.write_blob(CONTROL_FLOW_CL_ADDRESS, main_rcl)
    qtest.write_blob(SUB_LIST_ADDRESS, sub_list)
    qtest.writel(FRAMEBUFFER_ADDRESS, 0)
    qtest.writel(FRAMEBUFFER_ADDRESS + (WIDTH * HEIGHT - 1) * 4, 0)
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT1CA, CONTROL_FLOW_CL_ADDRESS)
    qtest.writel(V3D_CT1EA, CONTROL_FLOW_CL_ADDRESS + len(main_rcl))
    expect("sub-list render frame count", qtest.readl(V3D_RFC), 2)
    expect("sub-list thread status", qtest.readl(V3D_CT1CS), 0)
    expect("sub-list return-address cleanup", qtest.readl(V3D_CT1RA0), 0)
    expect("sub-list counter cleanup", qtest.readl(V3D_CT1LC), 0)
    expect("sub-list top-left clear", qtest.readl(FRAMEBUFFER_ADDRESS),
           CLEAR_COLOR)
    expect("sub-list bottom-right clear",
           qtest.readl(FRAMEBUFFER_ADDRESS + (WIDTH * HEIGHT - 1) * 4),
           CLEAR_COLOR)
    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)

    branch_rcl = build_branch_rcl(BRANCH_CL_ADDRESS)
    qtest.write_blob(BRANCH_CL_ADDRESS, branch_rcl)
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT1CA, BRANCH_CL_ADDRESS)
    qtest.writel(V3D_CT1EA, BRANCH_CL_ADDRESS + len(branch_rcl))
    expect("bounded branch frame count", qtest.readl(V3D_RFC), 3)
    expect("bounded branch status", qtest.readl(V3D_CT1CS), 0)
    expect("bounded branch ERRSTAT", qtest.readl(V3D_ERRSTAT), 0)
    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    bad_rcl = build_unsupported_rcl()
    qtest.write_blob(BAD_CL_ADDRESS, bad_rcl)
    qtest.writel(V3D_CT1CA, BAD_CL_ADDRESS)
    qtest.writel(V3D_CT1EA, BAD_CL_ADDRESS + len(bad_rcl))
    if not (qtest.readl(V3D_CT1CS) & V3D_CTERR):
        raise RuntimeError("unsupported primitive did not set CTERR")
    if qtest.readl(V3D_ERRSTAT) == 0:
        raise RuntimeError("unsupported primitive did not set ERRSTAT")
    expect("failed render did not increment frame count",
           qtest.readl(V3D_RFC), 3)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    unmatched_return = bytes((VC4_PACKET_RETURN_FROM_SUB_LIST,))
    qtest.write_blob(RETURN_ERROR_CL_ADDRESS, unmatched_return)
    qtest.writel(V3D_CT1CA, RETURN_ERROR_CL_ADDRESS)
    qtest.writel(V3D_CT1EA,
                 RETURN_ERROR_CL_ADDRESS + len(unmatched_return))
    if not (qtest.readl(V3D_CT1CS) & V3D_CTERR):
        raise RuntimeError("unmatched sub-list return did not set CTERR")
    if qtest.readl(V3D_ERRSTAT) == 0:
        raise RuntimeError("unmatched sub-list return did not set ERRSTAT")
    expect("unmatched return frame count", qtest.readl(V3D_RFC), 3)

    nested_main, nested_1, nested_2 = build_nested_sub_lists()
    qtest.write_blob(NESTED_MAIN_ADDRESS, nested_main)
    qtest.write_blob(NESTED_LEVEL_1_ADDRESS, nested_1)
    qtest.write_blob(NESTED_LEVEL_2_ADDRESS, nested_2)
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT1CA, NESTED_MAIN_ADDRESS)
    qtest.writel(V3D_CT1EA, NESTED_MAIN_ADDRESS + len(nested_main))
    nested_status = qtest.readl(V3D_CT1CS)
    if not (nested_status & V3D_CTERR):
        raise RuntimeError("excessive sub-list nesting did not set CTERR")
    if not (nested_status & V3D_CTSUBS):
        raise RuntimeError("failed nested sub-list lost CTSUBS state")
    expect("failed nested sub-list depth", qtest.readl(V3D_CT1LC), 2)
    if qtest.readl(V3D_CT1RA0) == 0:
        raise RuntimeError("failed nested sub-list lost return address")
    if qtest.readl(V3D_ERRSTAT) == 0:
        raise RuntimeError("excessive sub-list nesting did not set ERRSTAT")
    expect("nested sub-list frame count", qtest.readl(V3D_RFC), 3)

    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    expect("nested reset thread status", qtest.readl(V3D_CT1CS), 0)
    expect("nested reset return address", qtest.readl(V3D_CT1RA0), 0)
    expect("nested reset list counter", qtest.readl(V3D_CT1LC), 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-aarch64"),
        help="path to qemu-system-aarch64",
    )
    args = parser.parse_args()

    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-v3d-") as temp_s:
        temp = Path(temp_s)
        qtest_path = temp / "qtest.sock"
        stderr_path = temp / "qemu.stderr"
        vpu_image = temp / "vpu-halt.bin"
        vpu_image.write_bytes(bytes(4))

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-accel", "tcg,thread=single",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-no-reboot",
            "-S",
            "-kernel", str(vpu_image),
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qtest: QTest | None = None
        failure: BaseException | None = None
        try:
            qtest = wait_for_qtest(qtest_path, proc)
            exercise(qtest)
            if proc.poll() is not None:
                raise RuntimeError(
                    f"QEMU exited during the smoke test "
                    f"(status {proc.returncode})"
                )
        except BaseException as exc:
            failure = exc
        finally:
            if qtest is not None:
                try:
                    qtest.close()
                except OSError:
                    pass
            stop_process(proc)

        stderr_text = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
        if failure is not None:
            diagnostics = stderr_text.rstrip() or "<no QEMU diagnostics>"
            raise RuntimeError(
                "BCM2835 V3D smoke failed\n"
                f"command: {shlex.join(command)}\n"
                f"QEMU status: {proc.returncode}\n"
                f"QEMU stderr:\n{diagnostics}"
            ) from failure

        unexpected = [
            line for line in stderr_text.splitlines()
            if "bcm2835-v3d: unimplemented read" in line or
               "bcm2835-v3d: unimplemented write" in line
        ]
        if unexpected:
            raise RuntimeError(
                "modeled V3D accesses reached unimplemented registers:\n" +
                "\n".join(unexpected)
            )

    print("BCM2835 V3D register, clear-render, and control-flow smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
