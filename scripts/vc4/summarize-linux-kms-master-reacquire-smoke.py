#!/usr/bin/env python3
"""Exercise durable VC4 explicit-master handoff/modeset classifications."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

PREREQUISITE_MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
    "VC4_LINUX_KMS_MODESET_OK",
    "VC4_LINUX_KMS_MODESET_SUPERVISOR_OK",
    "VC4_LINUX_KMS_PAGEFLIP_OK",
    "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK",
)

MISSING_CLASSIFICATIONS = {
    "VC4_LINUX_KMS_MASTER_REACQUIRE_START":
        "vc4-kms-master-handoff-not-reached",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED":
        "vc4-kms-master-handoff-inherited-close-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK":
        "vc4-kms-master-handoff-open-before-drop-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK":
        "vc4-kms-master-handoff-busy-proof-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_READY":
        "vc4-kms-master-handoff-ready-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED":
        "vc4-kms-master-handoff-original-drop-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK":
        "vc4-kms-master-handoff-set-master-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK":
        "vc4-kms-master-handoff-selection-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK":
        "vc4-kms-master-handoff-baseline-state-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK":
        "vc4-kms-master-handoff-modeset-dumb-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK":
        "vc4-kms-master-handoff-modeset-map-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK":
        "vc4-kms-master-handoff-modeset-fb-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START":
        "vc4-kms-master-handoff-setcrtc-not-started",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK":
        "vc4-kms-master-handoff-setcrtc-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK":
        "vc4-kms-master-handoff-modeset-current-fb-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK":
        "vc4-kms-master-handoff-independent-modeset-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK":
        "vc4-kms-master-handoff-pageflip-dumb-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK":
        "vc4-kms-master-handoff-pageflip-map-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK":
        "vc4-kms-master-handoff-pageflip-fb-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START":
        "vc4-kms-master-handoff-pageflip-ioctl-not-started",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED":
        "vc4-kms-master-handoff-pageflip-not-queued",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK":
        "vc4-kms-master-handoff-event-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK":
        "vc4-kms-master-handoff-current-fb-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY":
        "vc4-kms-master-handoff-visual-not-ready",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED":
        "vc4-kms-master-handoff-child-drop-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_OK":
        "vc4-kms-master-handoff-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED":
        "vc4-kms-master-handoff-original-restore-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_ORDER_OK":
        "vc4-kms-master-handoff-order-proof-incomplete",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK":
        "vc4-kms-master-handoff-supervisor-incomplete",
}


def load_reporter() -> ModuleType:
    path = Path(__file__).with_name(
        "summarize-linux-kms-master-reacquire.py"
    )
    spec = importlib.util.spec_from_file_location(
        "vc4_master_handoff_summary", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serial_from(
    module: ModuleType,
    *,
    missing: tuple[str, ...] = (),
    extra: str = "",
    witness_order: tuple[str, ...] | None = None,
) -> str:
    if witness_order is None:
        witness_order = module.WITNESS_MARKER_ORDER
    lines = [marker for marker in PREREQUISITE_MARKERS if marker not in missing]
    lines.extend(marker for marker in witness_order if marker not in missing)
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def image(passed: bool = True) -> dict[str, object]:
    return {
        "passed": passed,
        "pattern": "vc4-native-kms-master-reacquire-xrgb8888-v1",
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
    clear = serial_from(module)

    expect(module, module.CLEAR, serial=clear)
    expect(
        module,
        "vc4-kms-master-handoff-image-mismatch",
        serial=clear,
        image_record=image(False),
    )
    expect(
        module,
        "vc4-kms-master-handoff-image-missing",
        serial=clear,
        image_present=False,
        image_record={},
    )
    expect(
        module,
        "vc4-kms-master-handoff-probe-return-code",
        serial=clear,
        return_code=9,
    )
    expect(
        module,
        "vc4-render-submit-regression",
        serial=serial_from(
            module, missing=("VC4_LINUX_DRM_SUBMIT_OK",)
        ),
    )
    expect(
        module,
        "vc4-kms-master-handoff-baseline-pageflip-regression",
        serial=serial_from(
            module,
            missing=("VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK",),
        ),
    )
    expect(
        module,
        "vc4-kms-master-handoff-set-master-after-drop",
        serial=serial_from(
            module,
            missing=tuple(
                marker
                for marker in module.WITNESS_MARKER_ORDER
                if marker
                not in (
                    "VC4_LINUX_KMS_MASTER_REACQUIRE_START",
                    "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED",
                    "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK",
                    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK",
                    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_READY",
                    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED",
                )
            ),
            extra=(
                "VC4_LINUX_KMS_MASTER_REACQUIRE_FAILED "
                "stage=set-master-after-drop errno=13"
            ),
        ),
    )
    expect(
        module,
        "vc4-kms-master-handoff-timeout",
        serial=serial_from(
            module,
            extra="VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT",
        ),
    )

    for missing, expected in MISSING_CLASSIFICATIONS.items():
        expect(
            module,
            expected,
            serial=serial_from(module, missing=(missing,)),
        )

    reordered = list(module.WITNESS_MARKER_ORDER)
    start_index = reordered.index(
        "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START"
    )
    ok_index = reordered.index(
        "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK"
    )
    reordered[start_index], reordered[ok_index] = (
        reordered[ok_index],
        reordered[start_index],
    )
    expect(
        module,
        "vc4-kms-master-handoff-marker-order-invalid",
        serial=serial_from(module, witness_order=tuple(reordered)),
    )

    old_explicit_handoff = tuple(
        marker
        for marker in module.WITNESS_MARKER_ORDER
        if marker
        not in (
            "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK",
            "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK",
        )
    )
    expect(
        module,
        "vc4-kms-master-handoff-selection-incomplete",
        serial=serial_from(module, witness_order=old_explicit_handoff),
    )

    print(
        "VC4 explicit DRM-master handoff/modeset classifications: PASS"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
