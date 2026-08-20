#!/usr/bin/env python3
"""Require Linux VC4 DRM render-node and basic GEM UAPI bring-up."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType
from typing import Any


def load_base_probe() -> ModuleType:
    path = Path(__file__).with_name("raspi3-linux-probe.py")
    spec = importlib.util.spec_from_file_location("vc4_linux_probe", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import Linux probe from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def argument_path(name: str) -> Path:
    try:
        index = sys.argv.index(name)
    except ValueError as exc:
        raise RuntimeError(f"required argument is missing: {name}") from exc
    if index + 1 >= len(sys.argv):
        raise RuntimeError(f"argument has no value: {name}")
    return Path(sys.argv[index + 1]).resolve()


def serial_contains(serial: str, marker: str) -> bool:
    return marker in serial


def failure_stage(serial: str) -> str | None:
    prefix = "VC4_LINUX_DRM_UAPI_FAILED stage="
    for line in serial.splitlines():
        if prefix in line:
            return line.split(prefix, 1)[1].strip().split()[0]
    return None


def classify(result: dict[str, Any]) -> str:
    if result.get("passed") is True:
        return "linux-vc4-drm-uapi"
    if result.get("init_seen") is not True:
        if result.get("linux_version_seen") is True:
            return "linux-vc4-before-init"
        return "linux-vc4-before-kernel-entry"
    if result.get("dt_v3d_enabled") is not True:
        return "linux-v3d-device-tree-disabled"
    if result.get("v3d_platform_device_seen") is not True:
        return "linux-v3d-platform-device-missing"
    if result.get("card0_seen") is not True:
        return "linux-vc4-component-bind-blocked"
    if result.get("render128_seen") is not True:
        return "linux-vc4-render-node-missing"
    stage = result.get("uapi_failure_stage")
    if stage:
        return f"linux-vc4-{stage}-blocked"
    if result.get("uapi_started") is not True:
        return "linux-vc4-uapi-probe-not-executed"
    return "linux-vc4-uapi-unclassified"


def main() -> int:
    base = load_base_probe()
    base.LINUX_CMDLINE += (
        " clk_ignore_unused"
        " drm.debug=0x1ff"
    )

    base_return_code = base.main()
    out_dir = argument_path("--out-dir")
    result_path = out_dir / "result.json"
    serial_path = out_dir / "serial.log"
    if not result_path.is_file():
        raise RuntimeError(f"base probe did not produce {result_path}")

    result = json.loads(result_path.read_text(encoding="utf-8"))
    serial = (
        serial_path.read_text(encoding="utf-8", errors="replace")
        if serial_path.is_file() else ""
    )

    framebuffer_passed = result.get("passed") is True
    card0_seen = serial_contains(serial, "VC4_LINUX_DRM_CARD0_OK")
    render128_seen = serial_contains(serial, "VC4_LINUX_DRM_RENDER128_OK")
    uapi_started = serial_contains(serial, "VC4_LINUX_DRM_UAPI_START")
    ident_ok = serial_contains(serial, "VC4_LINUX_DRM_IDENT_OK")
    bo_ok = serial_contains(serial, "VC4_LINUX_DRM_BO_OK")
    uapi_ok = serial_contains(serial, "VC4_LINUX_DRM_UAPI_OK")
    driver_ok = serial_contains(serial, "VC4_LINUX_V3D_DRIVER_OK")
    dt_v3d_enabled = serial_contains(
        serial, "VC4_LINUX_TEXT_OK label=DT_V3D_STATUS value=okay"
    )
    v3d_platform_device_seen = serial_contains(
        serial, "VC4_LINUX_PATH_OK label=V3D_DEVICE"
    )

    result.update({
        "base_probe_return_code": base_return_code,
        "framebuffer_passed": framebuffer_passed,
        "card0_seen": card0_seen,
        "render128_seen": render128_seen,
        "uapi_started": uapi_started,
        "ident_ok": ident_ok,
        "bo_ok": bo_ok,
        "uapi_ok": uapi_ok,
        "driver_ok": driver_ok,
        "dt_v3d_enabled": dt_v3d_enabled,
        "v3d_platform_device_seen": v3d_platform_device_seen,
        "uapi_failure_stage": failure_stage(serial),
    })
    result["passed"] = bool(
        framebuffer_passed
        and card0_seen
        and render128_seen
        and ident_ok
        and bo_ok
        and uapi_ok
        and driver_ok
    )
    result["classification"] = classify(result)

    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))

    if result["passed"]:
        return 0
    if result.get("render128_seen"):
        return 2
    if result.get("card0_seen"):
        return 3
    if result.get("init_seen"):
        return 4
    if result.get("linux_version_seen"):
        return 5
    return 6


if __name__ == "__main__":
    raise SystemExit(main())
