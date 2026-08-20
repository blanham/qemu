#!/usr/bin/env python3
"""Summarize the deterministic Linux VC4 DRM UAPI overlay matrix."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from typing import Any

DEFAULT_VARIANTS = ("base", "render-only", "fkms", "kms")
DRIVER_RE = re.compile(
    r"(?:vc4|v3d|drm|component|3fc00000|7ec00000)", re.IGNORECASE
)
ERROR_RE = re.compile(
    r"(?:fail|error|warn|defer|timeout|invalid|missing|unimplemented)",
    re.IGNORECASE,
)


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def contains(serial: str, marker: str) -> bool:
    return marker in serial


def classify(record: dict[str, Any]) -> tuple[int, str]:
    if record["uapi_ok"]:
        return 100, "linux-vc4-drm-uapi"
    if record["render128_vc4"]:
        return 85, "vc4-render-node-uapi-failed"
    if record["card0_vc4"]:
        return 75, "vc4-card-node-uapi-failed"
    if record["vc4_v3d_driver_path"]:
        return 60, "vc4-v3d-component-present-master-unbound"
    if record["v3d_device_path"]:
        return 50, "v3d-platform-device-present-driver-unbound"
    if record["dt_v3d_okay"]:
        return 40, "v3d-dt-enabled-device-missing"
    if record["init_seen"]:
        return 25, "vc4-drm-unavailable-after-init"
    if record["linux_version_seen"]:
        return 15, "linux-boot-before-init"
    return 0, "linux-boot-failed"


def summarize_variant(root: Path, variant: str) -> dict[str, Any]:
    directory = root / variant
    result = load_json(directory / "result.json")
    serial = read_text(directory / "serial.log")
    stderr = read_text(directory / "qemu.stderr")
    return_code = read_text(directory / "return-code").strip() or None

    driver_lines = [
        line for line in serial.splitlines()
        if DRIVER_RE.search(line) and ERROR_RE.search(line)
    ][-120:]
    qemu_lines = [
        line for line in stderr.splitlines()
        if DRIVER_RE.search(line)
    ][-120:]
    uapi_failures = [
        line for line in serial.splitlines()
        if "VC4_LINUX_DRM_UAPI_FAILED" in line
    ][-32:]

    record: dict[str, Any] = {
        "variant": variant,
        "probe_return_code": return_code,
        "probe_passed": result.get("passed"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "linux_version_seen": bool(
            result.get("linux_version_seen") or "Linux version" in serial
        ),
        "init_seen": bool(
            result.get("init_seen") or "VC4_LINUX_INIT_OK" in serial
        ),
        "framebuffer_seen": contains(serial, "VC4_LINUX_FB_OK"),
        "card0_vc4": contains(serial, "VC4_LINUX_DRM_CARD0_OK"),
        "render128_vc4": contains(serial, "VC4_LINUX_DRM_RENDER128_OK"),
        "uapi_started": contains(serial, "VC4_LINUX_DRM_UAPI_START"),
        "ident_ok": contains(serial, "VC4_LINUX_DRM_IDENT_OK"),
        "create_bo_ok": contains(serial, "VC4_LINUX_DRM_CREATE_BO_OK"),
        "mmap_bo_ok": contains(serial, "VC4_LINUX_DRM_MMAP_BO_OK"),
        "gem_memory_ok": contains(serial, "VC4_LINUX_DRM_GEM_MEMORY_OK"),
        "uapi_ok": contains(serial, "VC4_LINUX_DRM_UAPI_OK"),
        "vc4_v3d_driver_path": any(
            contains(serial, marker)
            for marker in (
                "VC4_LINUX_PATH_OK label=VC4_V3D_DRIVER",
                "VC4_LINUX_PATH_OK label=V3D_DRIVER",
            )
        ),
        "v3d_device_path": contains(
            serial, "VC4_LINUX_PATH_OK label=V3D_DEVICE"
        ),
        "dt_v3d_okay": contains(
            serial, "VC4_LINUX_TEXT_OK label=DT_V3D_STATUS value=okay"
        ),
        "uapi_failures": uapi_failures,
        "driver_error_lines": driver_lines,
        "qemu_v3d_lines": qemu_lines,
        "serial_tail": serial.splitlines()[-320:],
    }
    score, classification = classify(record)
    record["score"] = score
    record["classification"] = classification
    return record


def markdown(status: dict[str, Any]) -> str:
    rows = [
        "| Variant | Classification | card0 | renderD128 | IDENT | GEM/mmap | UAPI | fb |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for item in status["variants"]:
        rows.append(
            "| {variant} | `{classification}` | {card} | {render} | "
            "{ident} | {gem} | {uapi} | {fb} |".format(
                variant=item["variant"],
                classification=item["classification"],
                card=str(item["card0_vc4"]).lower(),
                render=str(item["render128_vc4"]).lower(),
                ident=str(item["ident_ok"]).lower(),
                gem=str(
                    item["create_bo_ok"]
                    and item["mmap_bo_ok"]
                    and item["gem_memory_ok"]
                ).lower(),
                uapi=str(item["uapi_ok"]).lower(),
                fb=str(item["framebuffer_seen"]).lower(),
            )
        )
    best = status["best"]
    return (
        "# VC4 Linux DRM UAPI matrix\n\n"
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
    parser.add_argument(
        "--json", dest="json_path", required=True, type=Path
    )
    parser.add_argument(
        "--markdown", dest="markdown_path", required=True, type=Path
    )
    args = parser.parse_args()

    variants = [summarize_variant(args.root, item) for item in args.variants]
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
        "passed": any(item["uapi_ok"] for item in variants),
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
    args.markdown_path.write_text(markdown(status), encoding="utf-8")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
