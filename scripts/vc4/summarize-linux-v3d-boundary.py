#!/usr/bin/env python3
"""Classify the Linux VC4 platform-bind, GEM, and WAIT_BO boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
from typing import Any

DEFAULT_VARIANTS = ("base", "render-only", "fkms", "kms")
VC4_RE = re.compile(r"(?:vc4|v3d|3fc00000|7ec00000)", re.IGNORECASE)


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


def extract_dump(serial: str, label: str) -> list[str]:
    begin = f"VC4_LINUX_DUMP_BEGIN label={label} "
    end = f"VC4_LINUX_DUMP_END label={label} "
    collecting = False
    lines: list[str] = []

    for line in serial.splitlines():
        if line.startswith(begin):
            collecting = True
            continue
        if collecting and line.startswith(end):
            break
        if collecting and VC4_RE.search(line):
            lines.append(line)
    return lines[-160:]


def boundary_classification(record: dict[str, Any]) -> tuple[int, str]:
    if record["boundary_ok"]:
        return 120, "linux-vc4-drm-wait-bo"
    if record["uapi_ok"] and record["wait_bo_started"]:
        return 108, "vc4-wait-bo-failed"
    if record["uapi_ok"]:
        return 104, "linux-vc4-drm-uapi-without-wait"
    if record["render128_vc4"]:
        return 88, "vc4-render-node-uapi-failed"
    if record["card0_vc4"]:
        return 78, "vc4-card-node-uapi-failed"
    if record["already_bound"]:
        return 68, "v3d-bound-drm-master-missing"
    if record["bind_write_ok"]:
        return 66, "v3d-manual-bind-no-drm-node"
    if record["bind_write_failures"]:
        return 64, "v3d-platform-bind-failed"
    if record["deferred_vc4_lines"]:
        return 62, "v3d-driver-deferred"
    score = int(record.get("score", 0))
    return score, str(record.get("classification", "unclassified"))


def summarize_variant(base: Any, root: Path, variant: str) -> dict[str, Any]:
    record = base.summarize_variant(root, variant)
    serial = read_text(root / variant / "serial.log")
    record.update({
        "boundary_started": "VC4_LINUX_V3D_BOUNDARY_START" in serial,
        "boundary_done": "VC4_LINUX_V3D_BOUNDARY_DONE" in serial,
        "boundary_ok": "VC4_LINUX_V3D_BOUNDARY_OK" in serial,
        "wait_bo_started": "VC4_LINUX_DRM_WAIT_BO_START" in serial,
        "wait_bo_ok": "VC4_LINUX_DRM_WAIT_BO_OK" in serial,
        "already_bound": "VC4_LINUX_V3D_ALREADY_BOUND" in serial,
        "bind_write_ok": "VC4_LINUX_V3D_BIND_WRITE_OK" in serial,
        "bind_write_failures": [
            line for line in serial.splitlines()
            if "VC4_LINUX_V3D_BIND_WRITE_FAILED" in line
        ][-32:],
        "bind_path_missing": [
            line for line in serial.splitlines()
            if "VC4_LINUX_V3D_BIND_PATH_MISSING" in line
        ][-32:],
        "deferred_vc4_lines": extract_dump(serial, "DEVICES_DEFERRED"),
        "component_vc4_lines": extract_dump(serial, "DEVICE_COMPONENT"),
        "clock_vc4_lines": extract_dump(serial, "CLOCK_SUMMARY"),
        "interrupt_vc4_lines": extract_dump(serial, "INTERRUPTS"),
        "modalias_vc4_lines": extract_dump(serial, "V3D_MODALIAS"),
        "uevent_vc4_lines": extract_dump(serial, "V3D_UEVENT"),
    })
    score, classification = boundary_classification(record)
    record["score"] = score
    record["classification"] = classification
    return record


def make_markdown(status: dict[str, Any]) -> str:
    rows = [
        "| Variant | Frontier | bound | render | UAPI | WAIT_BO | framebuffer |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for item in status["variants"]:
        rows.append(
            "| {variant} | `{frontier}` | {bound} | {render} | {uapi} | "
            "{wait} | {fb} |".format(
                variant=item["variant"],
                frontier=item["classification"],
                bound=str(
                    item["already_bound"] or item["bind_write_ok"]
                ).lower(),
                render=str(item["render128_vc4"]).lower(),
                uapi=str(item["uapi_ok"]).lower(),
                wait=str(item["wait_bo_ok"]).lower(),
                fb=str(item["framebuffer_seen"]).lower(),
            )
        )
    best = status["best"]
    return (
        "# VC4 Linux V3D kernel boundary\n\n"
        f"Validation passed: **{str(status['passed']).lower()}**\n\n"
        f"Best variant: **`{best['variant']}`**\n\n"
        f"Frontier: **`{best['classification']}`**\n\n"
        + "\n".join(rows)
        + "\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument(
        "--variants", nargs="+", default=list(DEFAULT_VARIANTS)
    )
    parser.add_argument("--json", dest="json_path", required=True, type=Path)
    parser.add_argument(
        "--markdown", dest="markdown_path", required=True, type=Path
    )
    args = parser.parse_args()

    base = load_base_module()
    variants = [
        summarize_variant(base, args.root, variant)
        for variant in args.variants
    ]
    best = max(
        variants,
        key=lambda item: (
            item["score"],
            -args.variants.index(item["variant"]),
        ),
    )
    status = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_sha": os.environ.get("GITHUB_SHA"),
        "run_id": os.environ.get("GITHUB_RUN_ID"),
        "run_attempt": os.environ.get("GITHUB_RUN_ATTEMPT"),
        "passed": any(item["boundary_ok"] for item in variants),
        "best": {
            "variant": best["variant"],
            "classification": best["classification"],
            "score": best["score"],
        },
        "variants": variants,
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
