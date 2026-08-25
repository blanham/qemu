#!/usr/bin/env python3
"""Capture and verify the native VC4 KMS page-flip frame before fbdev takeover."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace
from typing import Any

VISUAL_READY_MARKER = "VC4_LINUX_KMS_PAGEFLIP_VISUAL_READY"
REQUIRED_COMPLETION_MARKERS = (
    "VC4_LINUX_KMS_PAGEFLIP_OK",
    "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
)


def load_sibling(filename: str, module_name: str) -> ModuleType:
    path = Path(__file__).with_name(filename)
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, record: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("linux-direct",), default="linux-direct"
    )
    parser.add_argument("--qemu", required=True, type=Path)
    parser.add_argument("--kernel", required=True, type=Path)
    parser.add_argument("--dtb", required=True, type=Path)
    parser.add_argument("--initrd", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--seconds", type=float, default=130.0)
    parser.add_argument("--sample-interval", type=float, default=5.0)
    parser.add_argument("--icount-shift", type=int)
    args = parser.parse_args()

    if args.seconds <= 0 or args.sample_interval <= 0:
        parser.error("--seconds and --sample-interval must be positive")
    if args.icount_shift is not None and not 0 <= args.icount_shift <= 20:
        parser.error("--icount-shift must be between 0 and 20")
    for label, path in (
        ("qemu", args.qemu),
        ("kernel", args.kernel),
        ("dtb", args.dtb),
        ("initrd", args.initrd),
    ):
        if not path.is_file():
            parser.error(f"--{label} must name a file: {path}")

    base = load_sibling("raspi3-linux-probe.py", "vc4_linux_probe")
    image = load_sibling("kms-pageflip-image.py", "vc4_pageflip_image")

    out_dir = args.out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    serial_path = out_dir / "serial.log"
    stderr_path = out_dir / "qemu.stderr"
    screenshot_path = out_dir / "framebuffer.ppm"
    image_record_path = out_dir / "pageflip-image.json"
    result_path = out_dir / "result.json"

    command_args = SimpleNamespace(
        qemu=args.qemu,
        mode=args.mode,
        kernel=args.kernel,
        dtb=args.dtb,
        initrd=args.initrd,
        icount_shift=args.icount_shift,
    )
    result: dict[str, Any] = {
        "schema_version": 1,
        "mode": "linux-direct-pageflip-visual",
        "seconds_requested": args.seconds,
        "sample_interval": args.sample_interval,
        "visual_ready_seen": False,
        "visual_capture_completed": False,
        "required_completion_markers": {
            marker: False for marker in REQUIRED_COMPLETION_MARKERS
        },
        "progress": [],
    }

    with tempfile.TemporaryDirectory(prefix="vc4-pageflip-visual-") as temp_s:
        temp = Path(temp_s)
        command = base.build_command(
            command_args, temp, serial_path, image_path=None
        )
        result["qemu_command"] = command

        with stderr_path.open("wb") as stderr:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
            )

        qmp = None
        qtest = None
        started = time.monotonic()
        next_sample = 0.0
        image_record: dict[str, Any] | None = None
        try:
            qmp = base.wait_for(temp / "qmp.sock", proc, "qmp")
            qtest = base.wait_for(temp / "qtest.sock", proc, "qtest")
            deadline = started + args.seconds

            while time.monotonic() < deadline:
                elapsed = time.monotonic() - started
                serial = base.read_serial(serial_path)
                result["visual_ready_seen"] = VISUAL_READY_MARKER in serial
                result["required_completion_markers"] = {
                    marker: marker in serial
                    for marker in REQUIRED_COMPLETION_MARKERS
                }

                if elapsed >= next_sample:
                    result["progress"].append(
                        base.progress_sample(qtest, qmp, elapsed)
                    )
                    next_sample += args.sample_interval

                if (
                    result["visual_ready_seen"]
                    and not result["visual_capture_completed"]
                ):
                    qmp.execute("stop")
                    try:
                        try:
                            result["visual_hvs_snapshot"] = (
                                base.hvs_snapshot(qtest)
                            )
                        except Exception as exc:
                            result["visual_hvs_snapshot_error"] = (
                                f"{type(exc).__name__}: {exc}"
                            )
                        qmp.execute(
                            "screendump", {"filename": str(screenshot_path)}
                        )
                        try:
                            image_record = image.verify(
                                screenshot_path,
                                tolerance=0,
                                max_mismatches=0,
                            )
                        except Exception as exc:
                            image_record = {
                                "schema_version": 1,
                                "pattern": image.PATTERN_NAME,
                                "image": str(screenshot_path),
                                "passed": False,
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        write_json(image_record_path, image_record)
                        result["visual_image"] = image_record
                        result["visual_capture_completed"] = True
                    finally:
                        qmp.execute("cont")

                if all(result["required_completion_markers"].values()):
                    break
                if proc.poll() is not None:
                    break
                time.sleep(0.02)

            if proc.poll() is None:
                try:
                    qmp.execute("stop")
                except Exception:
                    pass
            try:
                result["final_hvs_snapshot"] = base.hvs_snapshot(qtest)
            except Exception as exc:
                result["final_hvs_snapshot_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
            try:
                result["final_cpu_snapshot"] = base.cpu_snapshot(
                    qmp, full_registers=True
                )
            except Exception as exc:
                result["final_cpu_snapshot_error"] = (
                    f"{type(exc).__name__}: {exc}"
                )
        except Exception as exc:
            result["probe_error"] = f"{type(exc).__name__}: {exc}"
        finally:
            result["elapsed_seconds"] = time.monotonic() - started
            result["qemu_returncode_before_stop"] = proc.poll()
            serial = base.read_serial(serial_path)
            result["serial_tail"] = serial.splitlines()[-600:]
            result["visual_ready_seen"] = VISUAL_READY_MARKER in serial
            result["required_completion_markers"] = {
                marker: marker in serial
                for marker in REQUIRED_COMPLETION_MARKERS
            }
            if stderr_path.is_file():
                diagnostics = stderr_path.read_text(
                    encoding="utf-8", errors="replace"
                )
                result["qemu_diagnostics_tail"] = diagnostics.splitlines()[-500:]
            if image_record is None and image_record_path.is_file():
                try:
                    image_record = json.loads(
                        image_record_path.read_text(encoding="utf-8")
                    )
                except Exception as exc:
                    result["visual_image_read_error"] = (
                        f"{type(exc).__name__}: {exc}"
                    )
            result["visual_image"] = image_record
            result["passed"] = all((
                result.get("visual_ready_seen") is True,
                result.get("visual_capture_completed") is True,
                image_record is not None,
                image_record is not None and image_record.get("passed") is True,
                all(result["required_completion_markers"].values()),
            ))
            write_json(result_path, result)
            print(json.dumps(result, indent=2, sort_keys=True))

            if qmp is not None:
                try:
                    if proc.poll() is None:
                        qmp.execute("quit")
                except Exception:
                    pass
                try:
                    qmp.close()
                except OSError:
                    pass
            if qtest is not None:
                try:
                    qtest.close()
                except OSError:
                    pass
            base.stop_process(proc)

    return 0 if result.get("passed") is True else 1


if __name__ == "__main__":
    sys.exit(main())
