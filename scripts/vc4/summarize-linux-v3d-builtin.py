#!/usr/bin/env python3
"""Classify the firmware-matched built-in-VC4 Linux submit run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
from typing import Any


def load_base_module() -> Any:
    path = Path(__file__).with_name("summarize-linux-v3d-uapi.py")
    spec = importlib.util.spec_from_file_location("vc4_uapi_summary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load UAPI summary module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def classify(record: dict[str, Any], outcomes: dict[str, str]) -> tuple[int, str]:
    if outcomes.get("source") not in {None, "success"}:
        return 5, "firmware-kernel-source-resolution-failed"
    if outcomes.get("kernel") not in {None, "success"}:
        return 10, "builtin-vc4-kernel-build-failed"
    if outcomes.get("fixture") not in {None, "success"}:
        return 12, "builtin-vc4-submit-fixture-build-failed"
    if outcomes.get("build") not in {None, "success"}:
        return 14, "builtin-vc4-qemu-build-failed"
    if outcomes.get("regressions") not in {None, "success"}:
        return 16, "builtin-vc4-regression-gate-failed"
    if record["submit_ok"]:
        return 200, "linux-builtin-vc4-drm-submit-clear"
    if record["submit_pixels_ok"]:
        return 195, "builtin-vc4-submit-pixels-without-final-marker"
    if record["submit_wait_ok"]:
        return 185, "builtin-vc4-submit-completed-pixel-mismatch"
    if record["submit_cl_ok"]:
        return 180, "builtin-vc4-submit-completion-failed"
    if record["submit_failures"]:
        failure = record["submit_failures"][-1]
        if "stage=submit-cl" in failure:
            return 170, "builtin-vc4-submit-cl-rejected"
        if "stage=wait-bo" in failure:
            return 175, "builtin-vc4-submit-timeout"
        if "stage=pixel-verify" in failure:
            return 178, "builtin-vc4-submit-pixel-verification-failed"
        return 165, "builtin-vc4-submit-setup-failed"
    if record["uapi_ok"]:
        return 160, "builtin-vc4-uapi-submit-not-reached"
    if record["render128_vc4"]:
        return 150, "builtin-vc4-render-node-uapi-failed"
    if record["card0_vc4"]:
        return 145, "builtin-vc4-card-node-uapi-failed"
    if record["vc4_v3d_driver_path"]:
        return 135, "builtin-vc4-component-present-master-unbound"
    if record["v3d_device_path"]:
        return 130, "builtin-vc4-v3d-device-driver-unbound"
    if record["dt_v3d_okay"]:
        return 125, "builtin-vc4-v3d-dt-enabled-device-missing"
    if record["init_seen"]:
        return 100, "builtin-vc4-kernel-init-no-drm"
    return int(record.get("score", 0)), str(
        record.get("classification", "unclassified")
    )


def make_markdown(status: dict[str, Any]) -> str:
    item = status["result"]
    return (
        "# VC4 built-in-driver Linux boundary\n\n"
        f"Validation passed: **{str(status['passed']).lower()}**\n\n"
        f"Frontier: **`{status['classification']}`**\n\n"
        f"- Linux reached init: `{str(item['init_seen']).lower()}`\n"
        f"- VC4 render node: `{str(item['render128_vc4']).lower()}`\n"
        f"- Baseline UAPI: `{str(item['uapi_ok']).lower()}`\n"
        f"- `SUBMIT_CL` accepted: `{str(item['submit_cl_ok']).lower()}`\n"
        f"- GPU pixels verified: `{str(item['submit_pixels_ok']).lower()}`\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--variant", default="builtin")
    parser.add_argument("--json", dest="json_path", required=True, type=Path)
    parser.add_argument("--markdown", dest="markdown_path", required=True,
                        type=Path)
    args = parser.parse_args()

    base = load_base_module()
    record = base.summarize_variant(args.root, args.variant)
    serial = read_text(args.root / args.variant / "serial.log")
    record.update({
        "submit_started": "VC4_LINUX_DRM_SUBMIT_START" in serial,
        "submit_cl_ok": "VC4_LINUX_DRM_SUBMIT_CL_OK" in serial,
        "submit_wait_ok": "VC4_LINUX_DRM_SUBMIT_WAIT_OK" in serial,
        "submit_pixels_ok": "VC4_LINUX_DRM_SUBMIT_PIXELS_OK" in serial,
        "submit_ok": "VC4_LINUX_DRM_SUBMIT_OK" in serial,
        "submit_failures": [
            line for line in serial.splitlines()
            if "VC4_LINUX_DRM_SUBMIT_FAILED" in line
        ][-64:],
        "submit_samples": [
            line for line in serial.splitlines()
            if "VC4_LINUX_DRM_SUBMIT_SAMPLES" in line
        ][-16:],
    })
    outcomes = {
        key.removesuffix("_OUTCOME").lower(): value
        for key, value in os.environ.items()
        if key.endswith("_OUTCOME")
    }
    score, classification = classify(record, outcomes)
    status = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "passed": record["submit_ok"],
        "variant": args.variant,
        "classification": classification,
        "score": score,
        "outcomes": outcomes,
        "result": record,
    }
    args.json_path.write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.markdown_path.write_text(make_markdown(status), encoding="utf-8")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
