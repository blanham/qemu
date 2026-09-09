#!/usr/bin/env python3
"""Exercise BCM2835 V3D RGBA8888 T and LT render-target stores."""

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
V3D_INTCTL = V3D_BASE + 0x030
V3D_INTENA = V3D_BASE + 0x034
V3D_CT1CS = V3D_BASE + 0x104
V3D_CT1EA = V3D_BASE + 0x10C
V3D_CT1CA = V3D_BASE + 0x114
V3D_RFC = V3D_BASE + 0x138
V3D_ERRSTAT = V3D_BASE + 0xF20

V3D_INT_FRDONE = 1 << 0
V3D_CTRSTA = 1 << 15
V3D_CTSUBS = 1 << 4

VC4_PACKET_HALT = 0
VC4_PACKET_STORE_MS_TILE_BUFFER_EOF = 25
VC4_PACKET_TILE_RENDERING_MODE_CONFIG = 113
VC4_PACKET_CLEAR_COLORS = 114
VC4_PACKET_TILE_COORDINATES = 115

VC4_RENDER_CONFIG_FORMAT_RGBA8888 = 1 << 2
VC4_TILING_FORMAT_T = 1
VC4_TILING_FORMAT_LT = 2

CL_ADDRESS = 0x00090000
T_FRAMEBUFFER_ADDRESS = 0x00200000
LT_FRAMEBUFFER_ADDRESS = 0x00300000
WIDTH = 128
HEIGHT = 64
COLOR_LEFT = 0xFF102030
COLOR_RIGHT = 0xFF90A0B0
COLOR_LT_LEFT = 0xFF213243
COLOR_LT_RIGHT = 0xFFB1C2D3


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, text: str) -> str:
        self.file.write(text.encode("ascii") + b"\n")
        reply = self.file.readline()
        if not reply:
            raise RuntimeError(f"qtest closed after {text!r}")
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
            self.writel(
                address + offset,
                int.from_bytes(padded[offset:offset + 4], "little"),
            )

    def close(self) -> None:
        try:
            self.file.close()
        finally:
            self.sock.close()


def wait_for_qtest(path: Path, process: subprocess.Popen[bytes],
                   timeout: float = 15.0) -> QTest:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"QEMU exited before qtest connected: {process.returncode}"
            )
        try:
            return QTest(path)
        except (FileNotFoundError, ConnectionRefusedError) as error:
            last_error = error
            time.sleep(0.02)
    raise TimeoutError(f"qtest socket did not appear: {path}") from last_error


def expect(label: str, actual: int, expected: int) -> None:
    if actual != expected:
        raise RuntimeError(
            f"{label}: got 0x{actual:08x}, expected 0x{expected:08x}"
        )


def build_store_list(base: int, tiling: int, tile_x: int,
                     color: int) -> bytes:
    data = bytearray((VC4_PACKET_TILE_RENDERING_MODE_CONFIG,))
    data += struct.pack(
        "<IHHH",
        base,
        WIDTH,
        HEIGHT,
        VC4_RENDER_CONFIG_FORMAT_RGBA8888 | (tiling << 6),
    )
    data.append(VC4_PACKET_CLEAR_COLORS)
    data += struct.pack("<III", color, color, 0x00FFFFFF)
    data.append(0)
    data += bytes((VC4_PACKET_TILE_COORDINATES, tile_x, 0))
    data += bytes((VC4_PACKET_STORE_MS_TILE_BUFFER_EOF, VC4_PACKET_HALT))
    return bytes(data)


def submit(qtest: QTest, data: bytes, expected_rfc: int) -> None:
    qtest.writel(V3D_CT1CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_INTCTL, V3D_INT_FRDONE)
    qtest.write_blob(CL_ADDRESS, data)
    qtest.writel(V3D_CT1CA, CL_ADDRESS)
    qtest.writel(V3D_CT1EA, CL_ADDRESS + len(data))
    expect("render frame count", qtest.readl(V3D_RFC), expected_rfc)
    expect("render thread halt", qtest.readl(V3D_CT1CS), V3D_CTSUBS)
    expect("render error state", qtest.readl(V3D_ERRSTAT), 0)
    expect("render-done interrupt", qtest.readl(V3D_INTCTL),
           V3D_INT_FRDONE)


def exercise(qtest: QTest) -> None:
    qtest.writel(V3D_INTENA, V3D_INT_FRDONE)

    submit(
        qtest,
        build_store_list(
            T_FRAMEBUFFER_ADDRESS, VC4_TILING_FORMAT_T, 0, COLOR_LEFT
        ),
        1,
    )
    submit(
        qtest,
        build_store_list(
            T_FRAMEBUFFER_ADDRESS, VC4_TILING_FORMAT_T, 1, COLOR_RIGHT
        ),
        2,
    )

    # A 128-pixel RGBA8888 level has four 4 KiB T tiles per tile row.
    # The second tile row reverses horizontal tile order.  These raw physical
    # samples therefore prove both the selected T layout and its odd-row walk.
    for offset, color in (
        (0x0000, COLOR_LEFT),
        (0x1000, COLOR_LEFT),
        (0x2000, COLOR_RIGHT),
        (0x3000, COLOR_RIGHT),
        (0x4000, COLOR_RIGHT),
        (0x5000, COLOR_RIGHT),
        (0x6000, COLOR_LEFT),
        (0x7000, COLOR_LEFT),
    ):
        expect(
            f"T tile block at +0x{offset:04x}",
            qtest.readl(T_FRAMEBUFFER_ADDRESS + offset),
            color,
        )

    submit(
        qtest,
        build_store_list(
            LT_FRAMEBUFFER_ADDRESS, VC4_TILING_FORMAT_LT, 0,
            COLOR_LT_LEFT,
        ),
        3,
    )
    submit(
        qtest,
        build_store_list(
            LT_FRAMEBUFFER_ADDRESS, VC4_TILING_FORMAT_LT, 1,
            COLOR_LT_RIGHT,
        ),
        4,
    )

    # LT stores walk 4x4-pixel utiles.  At y=0, logical x=64 starts at
    # byte offset 16 utiles * 64 bytes = 0x400 rather than linear +0x100.
    expect("LT logical x=0,y=0", qtest.readl(LT_FRAMEBUFFER_ADDRESS),
           COLOR_LT_LEFT)
    expect("LT logical x=64,y=0",
           qtest.readl(LT_FRAMEBUFFER_ADDRESS + 0x400),
           COLOR_LT_RIGHT)
    expect("LT logical x=0,y=4",
           qtest.readl(LT_FRAMEBUFFER_ADDRESS + 0x800),
           COLOR_LT_LEFT)
    expect("LT logical x=64,y=4",
           qtest.readl(LT_FRAMEBUFFER_ADDRESS + 0xC00),
           COLOR_LT_RIGHT)


def stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu",
        type=Path,
        default=Path("build/qemu-system-aarch64"),
    )
    args = parser.parse_args()
    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-v3d-tiling-") as temp_s:
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
            process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qtest: QTest | None = None
        failure: BaseException | None = None
        try:
            qtest = wait_for_qtest(qtest_path, process)
            exercise(qtest)
            if process.poll() is not None:
                raise RuntimeError(
                    f"QEMU exited during tiling smoke: {process.returncode}"
                )
        except BaseException as error:
            failure = error
        finally:
            if qtest is not None:
                try:
                    qtest.close()
                except OSError:
                    pass
            stop_process(process)

        diagnostics = stderr_path.read_text(
            encoding="utf-8", errors="replace"
        )
        if failure is not None:
            raise RuntimeError(
                "BCM2835 V3D tiling smoke failed\n"
                f"command: {shlex.join(command)}\n"
                f"QEMU status: {process.returncode}\n"
                f"QEMU stderr:\n{diagnostics.rstrip() or '<none>'}"
            ) from failure
        if "render-store unsupported" in diagnostics:
            raise RuntimeError(
                "T/LT render config unexpectedly reached unsupported path"
            )

    print("BCM2835 V3D T/LT render-target smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
