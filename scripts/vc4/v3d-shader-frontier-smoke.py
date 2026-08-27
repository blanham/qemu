#!/usr/bin/env python3
"""Exercise bounded VC4 shader-record and QPU frontier diagnostics."""

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
V3D_CT0CS = V3D_BASE + 0x100
V3D_CT0EA = V3D_BASE + 0x108
V3D_CT0CA = V3D_BASE + 0x110
V3D_BFC = V3D_BASE + 0x134
V3D_ERRSTAT = V3D_BASE + 0xF20

V3D_CTRSTA = 1 << 15
V3D_CTERR = 1 << 3
V3D_ERR_UNSUPPORTED = 1 << 4

VC4_PACKET_START_TILE_BINNING = 6
VC4_PACKET_GL_ARRAY_PRIMITIVE = 33
VC4_PACKET_GL_SHADER_STATE = 64
VC4_PACKET_TILE_BINNING_MODE_CONFIG = 112
VC4_PRIMITIVE_TRIANGLES = 4
VC4_QPU_SIG_PROGRAM_END = 3

BCL_ADDRESS = 0x00090000
SHADER_RECORD_ADDRESS = 0x00092000
FS_CODE_ADDRESS = 0x00094000
FS_UNIFORMS_ADDRESS = 0x00095000
VS_CODE_ADDRESS = 0x00096000
VS_UNIFORMS_ADDRESS = 0x00097000
CS_CODE_ADDRESS = 0x00098000
CS_UNIFORMS_ADDRESS = 0x00099000
ATTRIBUTE_ADDRESS = 0x0009A000
TILE_ALLOC_ADDRESS = 0x000A0000
TILE_STATE_ADDRESS = 0x000B0000


class QTest:
    def __init__(self, path: Path) -> None:
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb", buffering=0)

    def command(self, text: str) -> str:
        self.file.write(text.encode("ascii") + b"\n")
        reply = self.file.readline()
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
        if self.command(
            f"writel 0x{address:x} 0x{value & 0xffffffff:x}"
        ) != "OK":
            raise RuntimeError("malformed qtest write reply")

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


def wait_for_qtest(path: Path, proc: subprocess.Popen[bytes]) -> QTest:
    deadline = time.monotonic() + 15.0
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


def build_shader_record() -> bytes:
    record = bytearray(44)
    record[0] = 1 << 2  # Enable clipping.
    record[3] = 0       # Constant-color fragment shader has no varyings.
    struct.pack_into("<I", record, 4, FS_CODE_ADDRESS)
    struct.pack_into("<I", record, 8, FS_UNIFORMS_ADDRESS)
    record[14] = 1
    record[15] = 2
    struct.pack_into("<I", record, 16, VS_CODE_ADDRESS)
    struct.pack_into("<I", record, 20, VS_UNIFORMS_ADDRESS)
    record[26] = 1
    record[27] = 2
    struct.pack_into("<I", record, 28, CS_CODE_ADDRESS)
    struct.pack_into("<I", record, 32, CS_UNIFORMS_ADDRESS)

    struct.pack_into("<I", record, 36, ATTRIBUTE_ADDRESS)
    record[40] = 7  # Eight bytes minus one.
    record[41] = 8
    record[42] = 0
    record[43] = 0
    return bytes(record)


def qpu_program(tag: int) -> bytes:
    words = (
        (VC4_QPU_SIG_PROGRAM_END << 60) | tag,
        0x1000000000000000 | tag,
        0x2000000000000000 | tag,
    )
    return b"".join(struct.pack("<Q", word) for word in words)


def build_bcl() -> bytes:
    data = bytearray((VC4_PACKET_TILE_BINNING_MODE_CONFIG,))
    data += struct.pack(
        "<IIIBBB",
        TILE_ALLOC_ADDRESS,
        0x00010000,
        TILE_STATE_ADDRESS,
        1,
        1,
        0,
    )
    data.append(VC4_PACKET_START_TILE_BINNING)
    data.append(VC4_PACKET_GL_SHADER_STATE)
    data += struct.pack("<I", SHADER_RECORD_ADDRESS | 1)
    data += bytes((VC4_PACKET_GL_ARRAY_PRIMITIVE,
                   VC4_PRIMITIVE_TRIANGLES))
    data += struct.pack("<II", 3, 0)
    return bytes(data)


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=3)


def submit(qtest: QTest, bcl: bytes) -> None:
    qtest.writel(V3D_CT0CS, V3D_CTRSTA)
    qtest.writel(V3D_ERRSTAT, 0xFFFFFFFF)
    qtest.writel(V3D_CT0CA, BCL_ADDRESS)
    qtest.writel(V3D_CT0EA, BCL_ADDRESS + len(bcl))
    if not (qtest.readl(V3D_CT0CS) & V3D_CTERR):
        raise RuntimeError("shader frontier did not retain CTERR")
    if qtest.readl(V3D_ERRSTAT) != V3D_ERR_UNSUPPORTED:
        raise RuntimeError(
            "shader frontier changed the guest-visible error boundary: "
            f"0x{qtest.readl(V3D_ERRSTAT):08x}"
        )
    if qtest.readl(V3D_BFC) != 0:
        raise RuntimeError("unsupported primitive incremented bin frame count")


def require_once(text: str, marker: str) -> None:
    count = text.count(marker)
    if count != 1:
        raise RuntimeError(
            f"expected one {marker!r} diagnostic, found {count}\n{text}"
        )


def exercise(qtest: QTest) -> bytes:
    bcl = build_bcl()
    qtest.write_blob(BCL_ADDRESS, bcl)
    qtest.write_blob(SHADER_RECORD_ADDRESS, build_shader_record())
    qtest.write_blob(FS_CODE_ADDRESS, qpu_program(0x11))
    qtest.write_blob(VS_CODE_ADDRESS, qpu_program(0x22))
    qtest.write_blob(CS_CODE_ADDRESS, qpu_program(0x33))
    qtest.write_blob(
        ATTRIBUTE_ADDRESS,
        struct.pack("<ffffff", -1.0, -1.0, 3.0, -1.0, -1.0, 3.0),
    )
    for address, seed in (
        (FS_UNIFORMS_ADDRESS, 0xF0000000),
        (VS_UNIFORMS_ADDRESS, 0xA0000000),
        (CS_UNIFORMS_ADDRESS, 0xC0000000),
    ):
        qtest.write_blob(
            address,
            b"".join(struct.pack("<I", seed + i) for i in range(16)),
        )

    submit(qtest, bcl)
    # A kernel timeout/retry must not flood the retained evidence transcript.
    submit(qtest, bcl)
    return bcl


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--qemu", type=Path, default=Path("build/qemu-system-aarch64")
    )
    args = parser.parse_args()
    qemu = args.qemu.resolve()
    if not qemu.is_file():
        parser.error(f"QEMU binary does not exist: {qemu}")

    with tempfile.TemporaryDirectory(prefix="vc4-shader-frontier-") as temp_s:
        temp = Path(temp_s)
        socket_path = temp / "qtest.sock"
        stderr_path = temp / "qemu.stderr"
        vpu_image = temp / "vpu-halt.bin"
        vpu_image.write_bytes(bytes(4))
        command = [
            str(qemu), "-M", "raspi3b-vc4-hetero", "-m", "1G",
            "-smp", "5", "-accel", "tcg,thread=single",
            "-display", "none", "-monitor", "none", "-serial", "none",
            "-no-reboot", "-S", "-kernel", str(vpu_image),
            "-qtest", f"unix:{socket_path},server=on,wait=off",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command, stdout=subprocess.DEVNULL, stderr=stderr
            )

        qtest: QTest | None = None
        failure: BaseException | None = None
        try:
            qtest = wait_for_qtest(socket_path, proc)
            exercise(qtest)
        except BaseException as exc:
            failure = exc
        finally:
            if qtest is not None:
                qtest.close()
            stop_process(proc)

        diagnostics = stderr_path.read_text(errors="replace")
        if failure is not None:
            raise RuntimeError(
                "VC4 shader frontier smoke failed\n"
                f"command: {shlex.join(command)}\n"
                f"QEMU status: {proc.returncode}\n"
                f"QEMU stderr:\n{diagnostics or '<none>'}"
            ) from failure

        require_once(
            diagnostics,
            "frontier primitive thread=0 packet=0x21 "
            "mode=4:triangles length=3 first=0",
        )
        require_once(
            diagnostics,
            "frontier shader record=0x00092000 raw=0x00092001 attrs=1",
        )
        require_once(
            diagnostics,
            "frontier attribute index=0 address=0x0009a000 bytes=8 stride=8",
        )
        require_once(diagnostics, "qpu frontier stage=fs index=0")
        require_once(diagnostics, "qpu frontier stage=vs index=0")
        require_once(diagnostics, "qpu frontier stage=cs index=0")
        require_once(
            diagnostics,
            "packet 0x21 requires binning/QPU execution at 0x00090016",
        )

    print("VC4 shader-record and QPU frontier smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
