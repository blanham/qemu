#!/usr/bin/env python3
"""Exercise the durable Linux VC4 page-flip classifications."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
    "VC4_LINUX_KMS_MODESET_OK",
    "VC4_LINUX_KMS_MODESET_SUPERVISOR_OK",
    "VC4_LINUX_KMS_PAGEFLIP_START",
    "VC4_LINUX_KMS_PAGEFLIP_ACTIVE_OK",
    "VC4_LINUX_KMS_PAGEFLIP_QUEUED",
    "VC4_LINUX_KMS_PAGEFLIP_EVENT_OK",
    "VC4_LINUX_KMS_PAGEFLIP_CURRENT_FB_OK",
    "VC4_LINUX_KMS_PAGEFLIP_VISUAL_READY",
    "VC4_LINUX_KMS_PAGEFLIP_OK",
    "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK",
)


def load_reporter() -> ModuleType:
    path = Path(__file__).with_name("summarize-linux-kms-pageflip.py")
    spec = importlib.util.spec_from_file_location("vc4_pageflip_summary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serial_without(*missing: str, extra: str = "") -> str:
    lines = [marker for marker in MARKERS if marker not in missing]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def image(passed: bool = True) -> dict[str, object]:
    return {
        "passed": passed,
        "pattern": "vc4-native-kms-pageflip-xrgb8888-v1",
        "width": 1280,
        "height": 800,
        "total_pixels": 1024000,
        "mismatched_pixels": 0 if passed else 1,
        "max_channel_error": 0 if passed else 255,
        "matching_fraction": 1.0 if passed else 0.999999,
        "sha256": "test",
    }


def expect(
    module: ModuleType,
    expected: str,
    *,
    serial: str,
    image_present: bool = True,
    image_record: dict[str, object] | None = None,
    return_code: int | None = 0,
    manifest: str = "i2c-bcm2835\nvc4.ko\n",
) -> None:
    if image_record is None:
        image_record = image()
    evidence = module.measure(
        serial,
        manifest,
        image_present,
        image_record,
        return_code,
    )
    actual = evidence["classification"]
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")
    if evidence["passed"] != (expected == module.CLEAR):
        raise AssertionError(
            f"classification {actual!r} has inconsistent pass state"
        )


def main() -> int:
    module = load_reporter()
    clear = serial_without()

    expect(module, module.CLEAR, serial=clear)
    expect(
        module,
        "vc4-kms-pageflip-image-mismatch",
        serial=clear,
        image_record=image(False),
    )
    expect(
        module,
        "vc4-kms-pageflip-probe-return-code",
        serial=clear,
        return_code=7,
    )
    expect(
        module,
        "vc4-render-submit-regression",
        serial=serial_without("VC4_LINUX_DRM_SUBMIT_OK"),
    )
    expect(
        module,
        "vc4-kms-pageflip-event-incomplete",
        serial=serial_without("VC4_LINUX_KMS_PAGEFLIP_EVENT_OK"),
    )
    expect(
        module,
        "vc4-kms-pageflip-timeout",
        serial=serial_without(extra="VC4_LINUX_KMS_PAGEFLIP_TIMEOUT"),
    )
    expect(
        module,
        "vc4-kms-pageflip-image-missing",
        serial=clear,
        image_present=False,
        image_record={},
    )
    expect(
        module,
        "vc4-kms-modeset-setcrtc",
        serial=serial_without(
            "VC4_LINUX_KMS_MODESET_OK",
            "VC4_LINUX_KMS_MODESET_SUPERVISOR_OK",
            extra="VC4_LINUX_KMS_MODESET_FAILED stage=setcrtc errno=22",
        ),
    )

    print("VC4 Linux KMS page-flip summary classifications: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
