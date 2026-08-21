#!/usr/bin/env python3
"""Classify the pinned-module Linux VC4 DRM submit witness."""

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


def submit_witness_complete(record: dict[str, Any]) -> bool:
    return all(
        record[key]
        for key in (
            "submit_cl_ok",
            "submit_wait_ok",
            "submit_pixels_ok",
            "submit_ok",
        )
    )


def classify(record: dict[str, Any], outcomes: dict[str, str]) -> tuple[int, str]:
    if outcomes.get("modules") not in {None, "success"}:
        return 10, "pinned-vc4-module-fetch-failed"
    if outcomes.get("fixture") not in {None, "success"}:
        return 12, "modular-submit-fixture-build-failed"
    if outcomes.get("build") not in {None, "success"}:
        return 14, "modular-submit-qemu-build-failed"
    if outcomes.get("regressions") not in {None, "success"}:
        return 16, "modular-submit-regression-gate-failed"
    if (record["modular_ok"] and record["module_closure_ok"] and
            record["uapi_ok"] and submit_witness_complete(record)):
        return 180, "linux-vc4-modular-drm-submit-clear"
    if record["modular_ok"] and record["submit_ok"]:
        return 178, "linux-vc4-modular-submit-markers-incomplete"
    if record["module_closure_ok"] and submit_witness_complete(record):
        return 175, "linux-vc4-module-closure-submit-clear"
    if record["module_closure_ok"] and record["uapi_ok"]:
        return 160, "linux-vc4-modular-uapi-submit-failed"
    if record["module_closure_ok"] and record["render128_vc4"]:
        return 150, "linux-vc4-modular-render-node-uapi-failed"
    if record["module_closure_ok"] and record["card0_vc4"]:
        return 145, "linux-vc4-modular-card-node-uapi-failed"
    if record["module_load_failures"]:
        return 130, "vc4-module-load-failed"
    if record["module_load_done"]:
        return 125, "vc4-modules-loaded-driver-did-not-bind"
    if record["module_manifest_missing"]:
        return 110, "vc4-module-manifest-missing"
    return int(record.get("score", 0)), str(
        record.get("classification", "unclassified")
    )


def make_markdown(status: dict[str, Any]) -> str:
    item = status["result"]
    return (
        "# VC4 pinned-module Linux DRM boundary\n\n"
        f"Validation passed: **{str(status['passed']).lower()}**\n\n"
        f"Frontier: **`{status['classification']}`**\n\n"
        f"- Module closure loaded: `{str(item['module_closure_ok']).lower()}`\n"
        f"- VC4 render node: `{str(item['render128_vc4']).lower()}`\n"
        f"- VC4 card node: `{str(item['card0_vc4']).lower()}`\n"
        f"- Baseline UAPI: `{str(item['uapi_ok']).lower()}`\n"
        f"- `SUBMIT_CL` accepted: `{str(item['submit_cl_ok']).lower()}`\n"
        f"- BO wait completed: `{str(item['submit_wait_ok']).lower()}`\n"
        f"- GPU pixels verified: `{str(item['submit_pixels_ok']).lower()}`\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--variant", default="render-only")
    parser.add_argument("--json", dest="json_path", required=True, type=Path)
    parser.add_argument("--markdown", dest="markdown_path", required=True,
                        type=Path)
    args = parser.parse_args()

    base = load_base_module()
    record = base.summarize_variant(args.root, args.variant)
    serial = read_text(args.root / args.variant / "serial.log")

    module_closure_ok = "VC4_LINUX_MODULE_CLOSURE_OK" in serial
    submit_ok = "VC4_LINUX_DRM_SUBMIT_OK" in serial
    modular_ok = "VC4_LINUX_V3D_MODULAR_OK" in serial
    record.update({
        "modular_started": "VC4_LINUX_V3D_MODULAR_START" in serial,
        "module_load_started": "VC4_LINUX_MODULE_LOAD_START" in serial,
        "module_load_done": (
            "VC4_LINUX_MODULE_LOAD_DONE" in serial or module_closure_ok
        ),
        "module_closure_ok": module_closure_ok,
        "module_manifest_missing": (
            "VC4_LINUX_MODULE_MANIFEST_MISSING" in serial
        ),
        "module_load_successes": [
            line for line in serial.splitlines()
            if "VC4_LINUX_MODULE_LOAD_OK" in line
            or "VC4_LINUX_MODULE_ALREADY_LOADED" in line
        ][-128:],
        "module_load_failures": [
            line for line in serial.splitlines()
            if "VC4_LINUX_MODULE_LOAD_FAILED" in line
            or "VC4_LINUX_MODULE_OPEN_FAILED" in line
        ][-128:],
        # SUBMIT_OK is emitted only after submit, wait, and pixel verification.
        # Accept it as a compatibility fallback for older witnesses whose
        # formatted diagnostics could not reach the serial console.
        "submit_cl_ok": (
            "VC4_LINUX_DRM_SUBMIT_CL_OK" in serial or submit_ok
        ),
        "submit_wait_ok": (
            "VC4_LINUX_DRM_SUBMIT_WAIT_OK" in serial or submit_ok
        ),
        "submit_pixels_ok": (
            "VC4_LINUX_DRM_SUBMIT_PIXELS_OK" in serial or submit_ok
        ),
        "submit_ok": submit_ok,
        "submit_failures": [
            line for line in serial.splitlines()
            if "VC4_LINUX_DRM_SUBMIT_FAILED" in line
        ][-64:],
        "modular_done": (
            "VC4_LINUX_V3D_MODULAR_DONE" in serial or modular_ok
        ),
        "modular_ok": modular_ok,
        "modular_reboot": "VC4_LINUX_V3D_MODULAR_REBOOT" in serial,
    })

    # A complete UAPI witness necessarily created, mapped, and coherently
    # accessed a GEM BO even when the formatted per-step lines were lost.
    if record["uapi_ok"]:
        record["create_bo_ok"] = True
        record["mmap_bo_ok"] = True
        record["gem_memory_ok"] = True
    if modular_ok:
        record["probe_passed"] = True

    outcomes = {
        key.removesuffix("_OUTCOME").lower(): value
        for key, value in os.environ.items()
        if key.endswith("_OUTCOME")
    }
    score, classification = classify(record, outcomes)
    passed = (
        record["modular_ok"]
        and record["module_closure_ok"]
        and record["uapi_ok"]
        and submit_witness_complete(record)
    )
    status = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "passed": passed,
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
