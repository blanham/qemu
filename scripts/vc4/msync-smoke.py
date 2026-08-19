#!/usr/bin/env python3
"""Exercise the BCM2835 multicore-synchronization MMIO block."""

from __future__ import annotations

import argparse
from pathlib import Path
import socket
import subprocess
import tempfile
import time


MSYNC_BASE = 0x3F000000
MS_STATUS = MSYNC_BASE + 0x080
MS_IREQ_0 = MSYNC_BASE + 0x084
MS_ICSET_0 = MSYNC_BASE + 0x090
MS_ICCLR_0 = MSYNC_BASE + 0x098
MS_MBOX_3 = MSYNC_BASE + 0x0AC
MS_VPUSEMA_0 = MSYNC_BASE + 0x0C0
MS_VPU_STAT = MSYNC_BASE + 0x0C8


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
        return reply.decode("ascii", errors="replace").strip()

    def readl(self, address: int) -> int:
        reply = self.command(f"readl 0x{address:x}")
        fields = reply.split()
        if len(fields) != 2 or fields[0] != "OK":
            raise RuntimeError(f"unexpected qtest read reply: {reply!r}")
        return int(fields[1], 0)

    def writel(self, address: int, value: int) -> None:
        reply = self.command(f"writel 0x{address:x} 0x{value:x}")
        if reply != "OK":
            raise RuntimeError(f"unexpected qtest write reply: {reply!r}")

    def close(self) -> None:
        self.file.close()
        self.sock.close()


def wait_for_qtest(path: Path, proc: subprocess.Popen[bytes],
                   timeout: float = 10.0) -> QTest:
    deadline = time.monotonic() + timeout
    last_error: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"QEMU exited early with status {proc.returncode}")
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
        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-accel", "qtest",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
        ]
        proc = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        qtest: QTest | None = None
        try:
            qtest = wait_for_qtest(qtest_path, proc)

            expect("initial semaphore status", qtest.readl(MS_STATUS), 0)
            expect("semaphore first claim", qtest.readl(MSYNC_BASE), 0)
            expect("semaphore claimed status", qtest.readl(MS_STATUS), 1)
            expect("semaphore second claim", qtest.readl(MSYNC_BASE), 1)
            qtest.writel(MSYNC_BASE, 0xDEADBEEF)
            expect("semaphore released status", qtest.readl(MS_STATUS), 0)

            expect("VPU semaphore first claim", qtest.readl(MS_VPUSEMA_0), 0)
            expect("VPU semaphore second claim", qtest.readl(MS_VPUSEMA_0), 1)
            qtest.writel(MS_VPUSEMA_0, 0xFFFFFFFF)
            expect("VPU semaphore claim after release",
                   qtest.readl(MS_VPUSEMA_0), 0)
            qtest.writel(MS_VPUSEMA_0, 0)

            qtest.writel(MS_MBOX_3, 0x12345678)
            expect("mailbox round trip", qtest.readl(MS_MBOX_3), 0x12345678)

            qtest.writel(MS_IREQ_0, 0xA5A55A5A)
            expect("IRQ request mask", qtest.readl(MS_IREQ_0), 0xA5A55A5A)

            qtest.writel(MS_ICSET_0, 1)
            expect("inter-core set", qtest.readl(MS_ICSET_0), 1)
            qtest.writel(MS_ICCLR_0, 1)
            expect("inter-core clear", qtest.readl(MS_ICSET_0), 0)

            expect("single-VPU status", qtest.readl(MS_VPU_STAT), 0)
        finally:
            if qtest is not None:
                qtest.close()
            stop_process(proc)

        if proc.stderr is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace")
            proc.stderr.close()
            if stderr:
                print(stderr, end="")

    print("BCM2835 multicore-sync smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
