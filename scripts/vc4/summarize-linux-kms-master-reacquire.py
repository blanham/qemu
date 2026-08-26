#!/usr/bin/env python3
"""Summarize the explicit VC4 DRM-master handoff/modeset/page-flip witness."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

CLEAR = (
    "linux-vc4-kms-explicit-master-handoff-"
    "modeset-pageflip-visual-clear"
)

MODESET_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_FAILED stage=(\S+) errno=(\d+)"
)
PAGEFLIP_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_PAGEFLIP_FAILED stage=(\S+) errno=(\d+)"
)
MASTER_REACQUIRE_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_MASTER_REACQUIRE_FAILED stage=(\S+) errno=(\d+)"
)

WITNESS_MARKER_ORDER = (
    "VC4_LINUX_KMS_MASTER_REACQUIRE_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_READY",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_ORDER_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--serial", required=True, type=pathlib.Path)
    parser.add_argument("--manifest", required=True, type=pathlib.Path)
    parser.add_argument("--image", required=True, type=pathlib.Path)
    parser.add_argument("--return-code", required=True, type=pathlib.Path)
    parser.add_argument("--json", required=True, type=pathlib.Path)
    parser.add_argument("--markdown", required=True, type=pathlib.Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--require-pass", action="store_true")
    return parser.parse_args()


def read_text(path: pathlib.Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def read_return_code(path: pathlib.Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text(errors="replace").strip())
    except ValueError:
        return None


def read_image(path: pathlib.Path) -> tuple[bool, dict[str, Any]]:
    if not path.is_file():
        return False, {}
    try:
        value = json.loads(path.read_text(errors="replace"))
    except (OSError, json.JSONDecodeError) as exc:
        return True, {
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(value, dict):
        return True, {
            "passed": False,
            "error": "master-handoff image result is not a JSON object",
        }
    return True, value


def marker(serial: str, name: str) -> bool:
    return name in serial


def failure_record(match: re.Match[str] | None) -> dict[str, Any] | None:
    if match is None:
        return None
    return {
        "stage": match.group(1),
        "errno": int(match.group(2)),
    }


def marker_positions(serial: str) -> dict[str, int]:
    return {name: serial.find(name) for name in WITNESS_MARKER_ORDER}


def marker_order_valid(positions: dict[str, int]) -> bool:
    values = [positions[name] for name in WITNESS_MARKER_ORDER]
    return all(value >= 0 for value in values) and all(
        before < after for before, after in zip(values, values[1:])
    )


def classify(evidence: dict[str, Any]) -> str:
    if not evidence["ddc_supplier_root_present"]:
        return "vc4-kms-ddc-supplier-fixture-missing"
    if not evidence["module_closure_ok"]:
        return "vc4-kms-module-closure-regression"
    if not evidence["native_kms_topology_clear"]:
        return "vc4-kms-topology-regression"
    if not evidence["render_submission_preserved"]:
        return "vc4-render-submit-regression"

    if not (
        evidence["modeset_ok"] and evidence["modeset_supervisor_ok"]
    ):
        failure = evidence["modeset_failure"]
        if failure:
            return "vc4-kms-modeset-" + failure["stage"]
        return "vc4-kms-modeset-regression"

    if evidence["baseline_pageflip_timed_out"]:
        return "vc4-kms-master-handoff-baseline-pageflip-timeout"
    if evidence["pageflip_failure"]:
        return (
            "vc4-kms-master-handoff-baseline-pageflip-"
            + evidence["pageflip_failure"]["stage"]
        )
    if not (
        evidence["baseline_pageflip_ok"]
        and evidence["baseline_pageflip_supervisor_ok"]
    ):
        return "vc4-kms-master-handoff-baseline-pageflip-regression"

    if evidence["timed_out"]:
        return "vc4-kms-master-handoff-timeout"
    if evidence["master_reacquire_failure"]:
        return (
            "vc4-kms-master-handoff-"
            + evidence["master_reacquire_failure"]["stage"]
        )
    if not evidence["started"]:
        return "vc4-kms-master-handoff-not-reached"
    if not evidence["inherited_fd_closed"]:
        return "vc4-kms-master-handoff-inherited-close-incomplete"
    if not evidence["card_opened_before_drop"]:
        return "vc4-kms-master-handoff-open-before-drop-incomplete"
    if not evidence["set_master_busy_proved"]:
        return "vc4-kms-master-handoff-busy-proof-incomplete"
    if not evidence["handoff_ready"]:
        return "vc4-kms-master-handoff-ready-incomplete"
    if not evidence["original_master_dropped"]:
        return "vc4-kms-master-handoff-original-drop-incomplete"
    if not evidence["new_file_master"]:
        return "vc4-kms-master-handoff-set-master-incomplete"
    if not evidence["selection_ok"]:
        return "vc4-kms-master-handoff-selection-incomplete"
    if not evidence["baseline_state_read"]:
        return "vc4-kms-master-handoff-baseline-state-incomplete"
    if not evidence["independent_modeset_dumb_ok"]:
        return "vc4-kms-master-handoff-modeset-dumb-incomplete"
    if not evidence["independent_modeset_map_ok"]:
        return "vc4-kms-master-handoff-modeset-map-incomplete"
    if not evidence["independent_modeset_fb_ok"]:
        return "vc4-kms-master-handoff-modeset-fb-incomplete"
    if not evidence["setcrtc_started"]:
        return "vc4-kms-master-handoff-setcrtc-not-started"
    if not evidence["setcrtc_ok"]:
        return "vc4-kms-master-handoff-setcrtc-incomplete"
    if not evidence["modeset_current_fb_ok"]:
        return "vc4-kms-master-handoff-modeset-current-fb-incomplete"
    if not evidence["independent_modeset_ok"]:
        return "vc4-kms-master-handoff-independent-modeset-incomplete"
    if not evidence["pageflip_dumb_ok"]:
        return "vc4-kms-master-handoff-pageflip-dumb-incomplete"
    if not evidence["pageflip_map_ok"]:
        return "vc4-kms-master-handoff-pageflip-map-incomplete"
    if not evidence["pageflip_fb_ok"]:
        return "vc4-kms-master-handoff-pageflip-fb-incomplete"
    if not evidence["pageflip_ioctl_started"]:
        return "vc4-kms-master-handoff-pageflip-ioctl-not-started"
    if not evidence["pageflip_queued"]:
        return "vc4-kms-master-handoff-pageflip-not-queued"
    if not evidence["flip_event_ok"]:
        return "vc4-kms-master-handoff-event-incomplete"
    if not evidence["current_fb_ok"]:
        return "vc4-kms-master-handoff-current-fb-incomplete"
    if not evidence["visual_ready"]:
        return "vc4-kms-master-handoff-visual-not-ready"
    if not evidence["image_present"]:
        return "vc4-kms-master-handoff-image-missing"
    if not evidence["visual_pixels_ok"]:
        return "vc4-kms-master-handoff-image-mismatch"
    if not evidence["child_master_dropped"]:
        return "vc4-kms-master-handoff-child-drop-incomplete"
    if not evidence["witness_ok"]:
        return "vc4-kms-master-handoff-incomplete"
    if not evidence["original_master_restored"]:
        return "vc4-kms-master-handoff-original-restore-incomplete"
    if not evidence["handoff_order_reported"]:
        return "vc4-kms-master-handoff-order-proof-incomplete"
    if not evidence["supervisor_ok"]:
        return "vc4-kms-master-handoff-supervisor-incomplete"
    if not evidence["marker_order_valid"]:
        return "vc4-kms-master-handoff-marker-order-invalid"
    if evidence["probe_return_code"] != 0:
        return "vc4-kms-master-handoff-probe-return-code"
    return CLEAR


def measure(
    serial: str,
    manifest: str,
    image_present: bool,
    image: dict[str, Any],
    probe_return_code: int | None,
) -> dict[str, Any]:
    positions = marker_positions(serial)
    evidence: dict[str, Any] = {
        "ddc_supplier_root_present": "i2c-bcm2835" in manifest,
        "module_closure_ok": marker(serial, "VC4_LINUX_MODULE_CLOSURE_OK"),
        "native_kms_topology_clear": marker(
            serial, "VC4_LINUX_KMS_TOPOLOGY_OK"
        ),
        "render_submission_preserved": marker(
            serial, "VC4_LINUX_DRM_SUBMIT_OK"
        ),
        "modeset_ok": marker(serial, "VC4_LINUX_KMS_MODESET_OK"),
        "modeset_supervisor_ok": marker(
            serial, "VC4_LINUX_KMS_MODESET_SUPERVISOR_OK"
        ),
        "baseline_pageflip_ok": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_OK"
        ),
        "baseline_pageflip_supervisor_ok": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK"
        ),
        "baseline_pageflip_timed_out": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_TIMEOUT"
        ),
        "started": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_START"
        ),
        "inherited_fd_closed": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED"
        ),
        "card_opened_before_drop": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK"
        ),
        "set_master_busy_proved": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK"
        ),
        "handoff_ready": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_READY"
        ),
        "original_master_dropped": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED"
        ),
        "new_file_master": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK"
        ),
        "selection_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK"
        ),
        "baseline_state_read": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK"
        ),
        "independent_modeset_dumb_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK"
        ),
        "independent_modeset_map_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK"
        ),
        "independent_modeset_fb_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK"
        ),
        "setcrtc_started": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START"
        ),
        "setcrtc_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK"
        ),
        "modeset_current_fb_ok": marker(
            serial,
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK",
        ),
        "independent_modeset_ok": marker(
            serial,
            "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK",
        ),
        "pageflip_dumb_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK"
        ),
        "pageflip_map_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK"
        ),
        "pageflip_fb_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK"
        ),
        "pageflip_ioctl_started": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START"
        ),
        "pageflip_queued": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED"
        ),
        "flip_event_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK"
        ),
        "current_fb_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK"
        ),
        "visual_ready": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY"
        ),
        "visual_pixels_ok": image.get("passed") is True,
        "image_present": image_present,
        "child_master_dropped": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED"
        ),
        "witness_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_OK"
        ),
        "original_master_restored": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED"
        ),
        "handoff_order_reported": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_ORDER_OK"
        ),
        "supervisor_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK"
        ),
        "marker_order_valid": marker_order_valid(positions),
        "timed_out": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT"
        ),
        "probe_return_code": probe_return_code,
        "modeset_failure": failure_record(MODESET_FAILURE_RE.search(serial)),
        "pageflip_failure": failure_record(
            PAGEFLIP_FAILURE_RE.search(serial)
        ),
        "master_reacquire_failure": failure_record(
            MASTER_REACQUIRE_FAILURE_RE.search(serial)
        ),
    }
    evidence["classification"] = classify(evidence)
    evidence["passed"] = evidence["classification"] == CLEAR
    return evidence


def relevant_tail(serial: str) -> list[str]:
    return [
        line
        for line in serial.splitlines()
        if "VC4_LINUX_KMS_MODESET" in line
        or "VC4_LINUX_KMS_PAGEFLIP" in line
        or "VC4_LINUX_KMS_MASTER_REACQUIRE" in line
        or "VC4_LINUX_KMS_TOPOLOGY" in line
        or "VC4_LINUX_DRM_SUBMIT" in line
        or "timeout" in line.lower()
        or "failed" in line.lower()
    ][-1000:]


def build_record(
    serial: str,
    manifest: str,
    image_present: bool,
    image: dict[str, Any],
    probe_return_code: int | None,
    source_sha: str | None,
    run_id: str | None,
    run_attempt: str | None,
) -> dict[str, Any]:
    evidence = measure(
        serial,
        manifest,
        image_present,
        image,
        probe_return_code,
    )
    modeset_failure = evidence.pop("modeset_failure")
    pageflip_failure = evidence.pop("pageflip_failure")
    master_failure = evidence.pop("master_reacquire_failure")
    failure = master_failure or pageflip_failure or modeset_failure
    image_witness = image if image_present else None

    return {
        "schema_version": 3,
        "source_sha": source_sha,
        "run_id": run_id,
        "run_attempt": run_attempt,
        **evidence,
        "failure_stage": failure["stage"] if failure else None,
        "failure_errno": failure["errno"] if failure else None,
        "visual_image": image_witness,
        "relevant_tail": relevant_tail(serial),
    }


def write_markdown(path: pathlib.Path, record: dict[str, Any]) -> None:
    image = record.get("visual_image")
    image = image if isinstance(image, dict) else {}
    checks = (
        ("Probe return code", record["probe_return_code"]),
        ("DDC supplier root present", record["ddc_supplier_root_present"]),
        ("Module closure loaded", record["module_closure_ok"]),
        ("Native topology clear", record["native_kms_topology_clear"]),
        ("Existing render submission preserved", record["render_submission_preserved"]),
        ("Initial inherited-file modeset completed", record["modeset_ok"]),
        ("Inherited-file page flip completed", record["baseline_pageflip_ok"]),
        ("Child closed inherited descriptor", record["inherited_fd_closed"]),
        ("Child opened card0 before the drop", record["card_opened_before_drop"]),
        ("Pre-drop SET_MASTER returned EBUSY", record["set_master_busy_proved"]),
        ("Child reached the handoff gate", record["handoff_ready"]),
        ("Original drm_file dropped master", record["original_master_dropped"]),
        ("Same new drm_file acquired master", record["new_file_master"]),
        ("New drm_file selected connector/mode", record["selection_ok"]),
        ("Pre-modeset CRTC state read", record["baseline_state_read"]),
        (
            "Independent modeset dumb buffer created",
            record["independent_modeset_dumb_ok"],
        ),
        (
            "Independent modeset dumb buffer mapped",
            record["independent_modeset_map_ok"],
        ),
        (
            "Independent modeset framebuffer created",
            record["independent_modeset_fb_ok"],
        ),
        ("Independent SETCRTC started", record["setcrtc_started"]),
        ("Independent SETCRTC completed", record["setcrtc_ok"]),
        ("GETCRTC verified independent modeset", record["modeset_current_fb_ok"]),
        ("Independent modeset witness completed", record["independent_modeset_ok"]),
        ("Page-flip dumb buffer created", record["pageflip_dumb_ok"]),
        ("Page-flip dumb buffer mapped", record["pageflip_map_ok"]),
        ("Page-flip framebuffer created", record["pageflip_fb_ok"]),
        ("Independent page-flip ioctl started", record["pageflip_ioctl_started"]),
        ("Independent page flip queued", record["pageflip_queued"]),
        ("Flip-complete event received", record["flip_event_ok"]),
        ("GETCRTC reports flipped framebuffer", record["current_fb_ok"]),
        ("Visual-ready hold reached", record["visual_ready"]),
        ("Exact final pixels verified", record["visual_pixels_ok"]),
        ("Child explicitly dropped master", record["child_master_dropped"]),
        ("Child witness completed", record["witness_ok"]),
        ("Original drm_file reacquired master", record["original_master_restored"]),
        ("Runtime reported handoff order", record["handoff_order_reported"]),
        ("Recorded marker order is valid", record["marker_order_valid"]),
        ("Supervisor completed", record["supervisor_ok"]),
        ("Timeout", record["timed_out"]),
        ("Failure stage", record["failure_stage"]),
        ("Failure errno", record["failure_errno"]),
    )
    lines = [
        "# VC4 explicit DRM-master handoff, modeset, and page flip",
        "",
        f"Validation passed: **{'true' if record['passed'] else 'false'}**",
        "",
        f"Frontier: **`{record['classification']}`**",
        "",
    ]
    lines.extend(f"- {name}: `{value}`" for name, value in checks)
    if image:
        lines.extend(
            (
                "",
                "## Exact image witness",
                "",
                f"- Pattern: `{image.get('pattern')}`",
                f"- Dimensions: `{image.get('width')}x{image.get('height')}`",
                f"- Total pixels: `{image.get('total_pixels')}`",
                f"- Mismatched pixels: `{image.get('mismatched_pixels')}`",
                f"- Maximum channel error: `{image.get('max_channel_error')}`",
                f"- Matching fraction: `{image.get('matching_fraction')}`",
                f"- SHA-256: `{image.get('sha256')}`",
            )
        )
    lines.extend(
        (
            "",
            "The child opens the primary DRM node while PID 1 still owns "
            "master and proves that SET_MASTER fails with EBUSY. PID 1 then "
            "drops master, and the same already-open child drm_file must "
            "explicitly acquire it. That new file independently enumerates "
            "a connector and mode, creates a first framebuffer, programs "
            "SETCRTC, and verifies the resulting CRTC state. It then creates "
            "a second framebuffer, queues an event-driven page flip, consumes "
            "DRM_EVENT_FLIP_COMPLETE, and verifies both GETCRTC and every "
            "captured XRGB8888 pixel. The child drops master before exiting, "
            "and PID 1 must reacquire it before the render witness continues.",
        )
    )
    path.write_text("\n".join(lines) + "\n")


def main() -> int:
    args = parse_args()
    serial = read_text(args.serial)
    manifest = read_text(args.manifest)
    image_present, image = read_image(args.image)
    probe_return_code = read_return_code(args.return_code)
    record = build_record(
        serial,
        manifest,
        image_present,
        image,
        probe_return_code,
        args.source_sha,
        args.run_id,
        args.run_attempt,
    )
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    write_markdown(args.markdown, record)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 1 if args.require_pass and not record["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
