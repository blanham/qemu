#!/usr/bin/env python3
"""Exercise BCM2835 USB power and DWC2 PHY sideband registers."""

from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
from types import ModuleType

PM_ARM_BASE = 0x3F100000
PM_GPU_BASE = 0x7E100000
PM_USB = 0x5C
PM_PASSWORD = 0x5A000000

USB_ARM_BASE = 0x3F980000
USB_GPU_BASE = 0x7E980000
GMDIOCSR = 0x80
GMDIOGEN = 0x84
GVBUSDRV = 0x88
GMDIO_RSVD = 0x8C

GMDIO_BUSY = 1 << 31
GMDIO_ENABLE = 1 << 18
MDIO_WRITE = 0x50020000
MDIO_READ = 0x60020000
GVBUSDRV_MASK = 0x000FFFFF


def load_handoff_module() -> ModuleType:
    path = Path(__file__).with_name("raspi3-bootrom-0200-smoke.py")
    spec = importlib.util.spec_from_file_location("vc4_bootrom_0200", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load 0x200 handoff module from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def stop_process(proc: subprocess.Popen[bytes]) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=2)


def mdio_command(kind: int, reg: int, value: int = 0) -> int:
    return kind | ((reg & 0x1F) << 18) | (value & 0xFFFF)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("qemu", help="path to qemu-system-aarch64")
    args = parser.parse_args()

    qemu = Path(args.qemu).resolve()
    if not qemu.is_file():
        parser.error(f"not a file: {qemu}")

    handoff = load_handoff_module()
    smoke = handoff.load_legacy_smoke()
    handoff.install_real_handoff(smoke)

    with tempfile.TemporaryDirectory(prefix="vc4-raspi3-usb-phy-") as tmp_s:
        tmp = Path(tmp_s)
        image_path = tmp / "usb-phy-sd.img"
        qtest_path = tmp / "qtest.sock"
        stderr_path = tmp / "qemu.stderr"
        smoke.build_sd_image(image_path, smoke.build_bootcode())

        command = [
            str(qemu),
            "-M", "raspi3b-vc4-hetero",
            "-m", "1G",
            "-smp", "5",
            "-drive", f"file={image_path},format=raw,if=sd",
            "-accel", "tcg,thread=single",
            "-d", "unimp,guest_errors",
            "-display", "none",
            "-monitor", "none",
            "-serial", "none",
            "-qtest", f"unix:{qtest_path},server=on,wait=off",
            "-S",
        ]

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qtest = None
        try:
            smoke.wait_for_socket(qtest_path, proc, 10.0)
            qtest = smoke.LineSocket(qtest_path)

            def readl(address: int) -> int:
                return smoke.parse_qtest_value(
                    qtest.send_line(f"readl 0x{address:x}")
                )

            def writel(address: int, value: int) -> None:
                smoke.qtest_writel(qtest, address, value)

            def check_aliases(arm_address: int, gpu_address: int,
                              expected: int, label: str) -> None:
                arm = readl(arm_address)
                gpu = readl(gpu_address)
                if (arm, gpu) != (expected, expected):
                    raise RuntimeError(
                        f"{label} alias mismatch: arm=0x{arm:08x} "
                        f"gpu=0x{gpu:08x} expected=0x{expected:08x}"
                    )

            pm_arm = PM_ARM_BASE + PM_USB
            pm_gpu = PM_GPU_BASE + PM_USB
            check_aliases(pm_arm, pm_gpu, 0, "PM_USB reset")

            # The power-manager password protects PM_USB like the other PM
            # registers, and the generated register mask exposes only CTRLEN.
            writel(pm_arm, 1)
            check_aliases(pm_arm, pm_gpu, 0, "PM_USB password rejection")
            writel(pm_gpu, PM_PASSWORD | 0x00FFFFFF)
            check_aliases(pm_arm, pm_gpu, 1, "PM_USB enable")
            writel(pm_arm, PM_PASSWORD)
            check_aliases(pm_arm, pm_gpu, 0, "PM_USB disable")

            csr_arm = USB_ARM_BASE + GMDIOCSR
            gen_arm = USB_ARM_BASE + GMDIOGEN
            vbus_arm = USB_ARM_BASE + GVBUSDRV
            reserved_arm = USB_ARM_BASE + GMDIO_RSVD
            csr_gpu = USB_GPU_BASE + GMDIOCSR
            gen_gpu = USB_GPU_BASE + GMDIOGEN
            vbus_gpu = USB_GPU_BASE + GVBUSDRV
            reserved_gpu = USB_GPU_BASE + GMDIO_RSVD

            for arm_address, gpu_address, label in (
                (csr_arm, csr_gpu, "GMDIOCSR reset"),
                (gen_arm, gen_gpu, "GMDIOGEN reset"),
                (vbus_arm, vbus_gpu, "GVBUSDRV reset"),
                (reserved_arm, reserved_gpu, "MDIO reserved reset"),
            ):
                check_aliases(arm_address, gpu_address, 0, label)

            # Firmware writes bit 18 before the first transaction.  Hardware
            # BUSY is asynchronous; this model completes before the next load.
            writel(csr_gpu, GMDIO_BUSY | GMDIO_ENABLE)
            check_aliases(csr_arm, csr_gpu, GMDIO_ENABLE,
                          "GMDIOCSR synchronous completion")

            reg = 0x15
            value = 0x0110

            # Match the hardware erratum sequence used by open firmware:
            # all-ones preamble, command, and zero dummy write.
            writel(gen_arm, 0xFFFFFFFF)
            check_aliases(gen_arm, gen_gpu, 0xFFFFFFFF,
                          "GMDIO preamble")
            writel(gen_gpu, mdio_command(MDIO_WRITE, reg, value))
            written_csr = GMDIO_ENABLE | value
            check_aliases(csr_arm, csr_gpu, written_csr,
                          "MDIO write completion")
            writel(gen_arm, 0)
            check_aliases(gen_arm, gen_gpu, 0, "GMDIO dummy write")
            check_aliases(csr_arm, csr_gpu, written_csr,
                          "MDIO write persistence")

            writel(gen_gpu, mdio_command(MDIO_READ, reg))
            check_aliases(csr_arm, csr_gpu, written_csr,
                          "MDIO readback")

            # PHY register 0x1b is polled by bootcode with command 0x606e0000.
            # Its reset value has bit 7 clear, so PHY settling may proceed.
            settle_command = mdio_command(MDIO_READ, 0x1B)
            if settle_command != 0x606E0000:
                raise AssertionError(
                    f"stock MDIO command changed: 0x{settle_command:08x}"
                )
            writel(gen_arm, settle_command)
            settled = readl(csr_gpu)
            if settled != GMDIO_ENABLE:
                raise RuntimeError(
                    "PHY settle register did not reset clear: "
                    f"0x{settled:08x}"
                )

            # Broadcom firmware uses bits 16:19 despite the older generated
            # width declaration, so preserve the complete observed field.
            writel(vbus_gpu, 0xFFFFFFFF)
            check_aliases(vbus_arm, vbus_gpu, GVBUSDRV_MASK,
                          "GVBUSDRV mask")
            vbus = (GVBUSDRV_MASK & 0xFFF0FFFF) | 0x000D0000
            vbus &= ~(1 << 7)
            writel(vbus_arm, vbus)
            check_aliases(vbus_arm, vbus_gpu, vbus, "GVBUSDRV update")

            # 0x8c is inside the Broadcom sideband window but has no retained
            # public definition.  Treat it as reserved rather than producing
            # guest-error noise or inventing undocumented state.
            writel(reserved_gpu, 0xFFFFFFFF)
            check_aliases(reserved_arm, reserved_gpu, 0,
                          "MDIO reserved register")

            diagnostics = stderr_path.read_text(
                encoding="utf-8", errors="replace"
            )
            unexpected = (
                "Unknown offset 0x0000005c",
                "Bad offset 0x80",
                "Bad offset 0x84",
                "Bad offset 0x88",
                "Bad offset 0x8c",
            )
            found = [entry for entry in unexpected if entry in diagnostics]
            if found:
                raise RuntimeError(
                    "USB sideband accesses still produced diagnostics: "
                    + ", ".join(found)
                )

            print(
                "BCM2835 USB power and PHY sideband passed: "
                "pm-usb=0x00000000 "
                f"mdio-write=0x{written_csr:08x} "
                f"mdio-settle=0x{settled:08x} "
                f"vbus=0x{vbus:08x} reserved=0x00000000"
            )
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
            stop_process(proc)


if __name__ == "__main__":
    raise SystemExit(main())
