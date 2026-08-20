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
V3D_RFC = V3D_BASE + 0x138
V3D_ERRSTAT = V3D_BASE + 0xF20

V3D_EXPECTED_IDENT0 = (2 << 24) | (ord("D") << 16) | (ord("3") << 8) | ord("V")
V3D_INT_FRDONE = 1 << 0
V3D_CTRSTA = 1 << 15
V3D_CTERR = 1 << 3

VC4_PACKET_HALT = 0
VC4_PACKET_STORE_MS_TILE_BUFFER_EOF = 25
VC4_PACKET_STORE_TILE_BUFFER_GENERAL = 28
VC4_PACKET_GL_ARRAY_PRIMITIVE = 33
VC4_PACKET_TILE_RENDERING_MODE_CONFIG = 113
VC4_PACKET_CLEAR_COLORS = 114
VC4_PACKET_TILE_COORDINATES = 115
VC4_RENDER_CONFIG_FORMAT_RGBA8888 = 1 << 2

CL_ADDRESS = 0x00080000
BAD_CL_ADDRESS = 0x00081000
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
           qtest.readl(V3D_RFC), 1)


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

    print("BCM2835 V3D register and clear-render smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
