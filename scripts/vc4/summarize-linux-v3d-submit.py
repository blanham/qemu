#!/usr/bin/env python3
"""Classify the Linux VC4 DRM clear-submit boundary."""

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


def classify(record: dict[str, Any], outcomes: dict[str, str],
             boundary_ready: bool) -> tuple[int, str]:
    if outcomes.get("fixture") not in {None, "success"}:
        return 4, "submit-fixture-build-failed"
    if not boundary_ready:
        return 8, "submit-blocked-before-linux-vc4-boundary"
    if outcomes.get("build") not in {None, "success"}:
        return 6, "submit-qemu-build-failed"
    if outcomes.get("regressions") not in {None, "success"}:
        return 7, "submit-regression-gate-failed"
    if record["submit_ok"]:
        return 150, "linux-vc4-drm-submit-clear"
    if record["submit_pixels_ok"]:
        return 145, "submit-pixels-without-final-marker"
    if record["submit_wait_ok"]:
        return 135, "vc4-submit-completed-pixel-mismatch"
    if record["submit_cl_ok"]:
        return 130, "vc4-submit-wait-failed"
    if record["submit_failures"]:
        failure = record["submit_failures"][-1]
        if "stage=submit-cl" in failure:
            return 122, "vc4-submit-cl-rejected"
        if "stage=pixel-verify" in failure:
            return 128, "vc4-submit-pixel-verification-failed"
        if "stage=wait-bo" in failure:
            return 126, "vc4-submit-completion-timeout"
        return 118, "vc4-submit-setup-failed"
    if record["uapi_ok"]:
        return 112, "linux-vc4-uapi-submit-not-reached"
    return int(record.get("score", 0)), str(
        record.get("classification", "unclassified")
    )


def make_markdown(status: dict[str, Any]) -> str:
    item = status["result"]
    return (
        "# VC4 Linux DRM submit boundary\n\n"
        f"Validation passed: **{str(status['passed']).lower()}**\n\n"
        f"DT variant: **`{status['variant']}`**\n\n"
        f"Frontier: **`{status['classification']}`**\n\n"
        f"- VC4 UAPI baseline: `{str(item['uapi_ok']).lower()}`\n"
        f"- `SUBMIT_CL` accepted: `{str(item['submit_cl_ok']).lower()}`\n"
        f"- BO completion observed: `{str(item['submit_wait_ok']).lower()}`\n"
        f"- GPU pixels verified: `{str(item['submit_pixels_ok']).lower()}`\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--variant", required=True)
    parser.add_argument("--boundary-ready", required=True,
                        choices=("true", "false"))
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
        "probe_done": "VC4_LINUX_DRM_SUBMIT_PROBE_DONE" in serial,
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
    boundary_ready = args.boundary_ready == "true"
    score, classification = classify(record, outcomes, boundary_ready)
    status = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "passed": record["submit_ok"],
        "variant": args.variant,
        "boundary_ready": boundary_ready,
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
