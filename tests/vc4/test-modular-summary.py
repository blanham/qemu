#!/usr/bin/env python3
"""Regression tests for the pinned-module VC4 witness summarizer."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SUMMARIZER = ROOT / "scripts/vc4/summarize-linux-v3d-modular.py"

SUCCESS_SERIAL = """\
Linux version 6.18.44-v8+
VC4_LINUX_INIT_OK
VC4_LINUX_V3D_MODULAR_START
VC4_LINUX_MODULE_LOAD_START
VC4_LINUX_MODULE_CLOSURE_OK
VC4_LINUX_DRM_RENDER128_OK
VC4_LINUX_DRM_UAPI_START
VC4_LINUX_DRM_IDENT_OK
VC4_LINUX_DRM_UAPI_OK
VC4_LINUX_DRM_SUBMIT_START
VC4_LINUX_DRM_SUBMIT_PIXELS_OK
VC4_LINUX_DRM_SUBMIT_OK
VC4_LINUX_V3D_MODULAR_OK
VC4_LINUX_V3D_MODULAR_REBOOT
"""


def run_summary(root: Path, serial: str, name: str) -> tuple[int, dict[str, object]]:
    variant = root / name / "render-only"
    variant.mkdir(parents=True)
    (variant / "serial.log").write_text(serial, encoding="utf-8")
    (variant / "qemu.stderr").write_text("", encoding="utf-8")
    (variant / "return-code").write_text("2\n", encoding="utf-8")
    (variant / "result.json").write_text(
        json.dumps({
            "passed": False,
            "elapsed_seconds": 120.0,
            "linux_version_seen": True,
            "init_seen": True,
        }) + "\n",
        encoding="utf-8",
    )

    json_path = root / name / "status.json"
    markdown_path = root / name / "status.md"
    env = os.environ.copy()
    for key in (
        "MODULES_OUTCOME",
        "FIXTURE_OUTCOME",
        "BUILD_OUTCOME",
        "REGRESSIONS_OUTCOME",
    ):
        env[key] = "success"
    completed = subprocess.run(
        [
            sys.executable,
            str(SUMMARIZER),
            "--root", str(root / name),
            "--variant", "render-only",
            "--json", str(json_path),
            "--markdown", str(markdown_path),
        ],
        check=False,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if not json_path.is_file():
        raise AssertionError(
            f"summarizer wrote no JSON (rc={completed.returncode}): "
            f"{completed.stderr}"
        )
    return completed.returncode, json.loads(json_path.read_text(encoding="utf-8"))


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="vc4-modular-summary-") as temp_s:
        root = Path(temp_s)

        rc, status = run_summary(root, SUCCESS_SERIAL, "success")
        assert rc == 0, status
        assert status["passed"] is True, status
        assert status["classification"] == "linux-vc4-modular-drm-submit-clear"
        result = status["result"]
        for key in (
            "probe_passed",
            "module_load_done",
            "module_closure_ok",
            "render128_vc4",
            "uapi_ok",
            "create_bo_ok",
            "mmap_bo_ok",
            "gem_memory_ok",
            "submit_cl_ok",
            "submit_wait_ok",
            "submit_pixels_ok",
            "submit_ok",
            "modular_done",
            "modular_ok",
            "modular_reboot",
        ):
            assert result[key] is True, (key, result)

        # The final submit marker is the transitive proof that submit, wait,
        # and pixel verification all completed in older serial witnesses.
        legacy = SUCCESS_SERIAL.replace(
            "VC4_LINUX_DRM_SUBMIT_PIXELS_OK\n", ""
        ).replace("VC4_LINUX_V3D_MODULAR_REBOOT\n", "")
        rc, status = run_summary(root, legacy, "legacy")
        assert rc == 0, status
        assert status["passed"] is True, status
        assert status["result"]["submit_cl_ok"] is True
        assert status["result"]["submit_wait_ok"] is True
        assert status["result"]["submit_pixels_ok"] is True

        incomplete = SUCCESS_SERIAL.replace(
            "VC4_LINUX_DRM_SUBMIT_OK\n", ""
        )
        rc, status = run_summary(root, incomplete, "incomplete")
        assert rc == 2, status
        assert status["passed"] is False, status

    print("VC4 modular summary regression tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
