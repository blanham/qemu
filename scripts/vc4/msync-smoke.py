#!/usr/bin/env python3
"""Exercise the BCM2835 multicore-synchronization MMIO block."""

from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import socket
import subprocess
import tempfile
import time


MSYNC_BASE = 0x3F000000
MS_SEMA_3 = MSYNC_BASE + 0x00C
MS_SEMA_4 = MSYNC_BASE + 0x010
MS_STATUS = MSYNC_BASE + 0x080
MS_IREQ_0 = MSYNC_BASE + 0x084
MS_ICSET_0 = MSYNC_BASE + 0x090
MS_ICCLR_0 = MSYNC_BASE + 0x098
MS_MBOX_0 = MSYNC_BASE + 0x0A0
MS_MBOX_7 = MSYNC_BASE + 0x0BC
MS_VPUSEMA_0 = MSYNC_BASE + 0x0C0
MS_VPU_STAT = MSYNC_BASE + 0x0C8


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
    expect("initial semaphore status", qtest.readl(MS_STATUS), 0)

    expect("semaphore 3 first claim", qtest.readl(MS_SEMA_3), 0)
    expect("semaphore 3 claimed status",
           qtest.readl(MS_STATUS), 1 << 3)
    expect("semaphore 3 repeated claim", qtest.readl(MS_SEMA_3), 1)
    qtest.writel(MS_SEMA_3, 0xDEADBEEF)
    expect("semaphore 3 released status", qtest.readl(MS_STATUS), 0)

    qtest.writel(MS_IREQ_0, 1 << 4)
    expect("IRQ request mask", qtest.readl(MS_IREQ_0), 1 << 4)
    expect("semaphore 4 first claim", qtest.readl(MS_SEMA_4), 0)
    expect("semaphore 4 claimed status",
           qtest.readl(MS_STATUS), 1 << 4)
    qtest.writel(MS_SEMA_4, 0)
    expect("semaphore 4 released status", qtest.readl(MS_STATUS), 0)

    qtest.writel(MS_ICSET_0, 1)
    expect("inter-core request set", qtest.readl(MS_ICSET_0), 1)
    qtest.writel(MS_ICCLR_0, 1)
    expect("inter-core request clear", qtest.readl(MS_ICCLR_0), 0)

    qtest.writel(MS_MBOX_0, 0x01234567)
    qtest.writel(MS_MBOX_7, 0x89ABCDEF)
    expect("mailbox zero round trip",
           qtest.readl(MS_MBOX_0), 0x01234567)
    expect("mailbox seven round trip",
           qtest.readl(MS_MBOX_7), 0x89ABCDEF)

    expect("VPU semaphore first claim", qtest.readl(MS_VPUSEMA_0), 0)
    expect("VPU semaphore repeated claim", qtest.readl(MS_VPUSEMA_0), 1)
    qtest.writel(MS_VPUSEMA_0, 0xFFFFFFFF)
    expect("VPU semaphore claim after release",
           qtest.readl(MS_VPUSEMA_0), 0)
    qtest.writel(MS_VPUSEMA_0, 0)

    expect("single-VPU status", qtest.readl(MS_VPU_STAT), 0)


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

    with tempfile.TemporaryDirectory(prefix="vc4-msync-") as temp_s:
        temp = Path(temp_s)
        qtest_path = temp / "qtest.sock"
        stderr_path = temp / "qemu.stderr"
        vpu_image = temp / "vpu-halt.bin"

        # Supplying an image bypasses the board's real SD boot-ROM path.  The
        # VM remains stopped, so these bytes are never executed; they merely
        # let machine realization finish without requiring a FAT SD image.
        vpu_image.write_bytes(b"\x00\x00\x00\x00")

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
                "BCM2835 multicore-sync smoke failed\n"
                f"command: {shlex.join(command)}\n"
                f"QEMU status: {proc.returncode}\n"
                f"QEMU stderr:\n{diagnostics}"
            ) from failure

        unexpected = [
            line for line in stderr_text.splitlines()
            if "bcm2835-msync: unimplemented" in line
        ]
        if unexpected:
            raise RuntimeError(
                "modeled multicore-sync accesses reached unimplemented "
                "registers:\n" + "\n".join(unexpected)
            )

        if stderr_text:
            print(stderr_text, end="")

    print("BCM2835 multicore-sync smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
