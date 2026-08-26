#!/usr/bin/env python3
"""Summarize the pinned Linux VC4 independent-master modeset/page-flip witness."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

CLEAR = "linux-vc4-kms-independent-master-modeset-pageflip-visual-clear"

MODESET_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_FAILED stage=(\S+) errno=(\d+)"
)
PAGEFLIP_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_PAGEFLIP_FAILED stage=(\S+) errno=(\d+)"
)
MASTER_REACQUIRE_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_MASTER_REACQUIRE_FAILED stage=(\S+) errno=(\d+)"
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
            "error": "master-reacquisition image result is not a JSON object",
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
        return "vc4-kms-master-reacquire-baseline-pageflip-timeout"
    if evidence["pageflip_failure"]:
        return (
            "vc4-kms-master-reacquire-baseline-pageflip-"
            + evidence["pageflip_failure"]["stage"]
        )
    if not (
        evidence["baseline_pageflip_ok"]
        and evidence["baseline_pageflip_supervisor_ok"]
    ):
        return "vc4-kms-master-reacquire-baseline-pageflip-regression"

    if evidence["timed_out"]:
        return "vc4-kms-master-reacquire-timeout"
    if evidence["master_reacquire_failure"]:
        return (
            "vc4-kms-master-reacquire-"
            + evidence["master_reacquire_failure"]["stage"]
        )
    if not evidence["started"]:
        return "vc4-kms-master-reacquire-not-reached"
    if not evidence["original_master_dropped"]:
        return "vc4-kms-master-reacquire-original-drop-incomplete"
    if not evidence["inherited_fd_closed"]:
        return "vc4-kms-master-reacquire-inherited-close-incomplete"
    if not evidence["card_reopened"]:
        return "vc4-kms-master-reacquire-reopen-incomplete"
    if not evidence["new_file_master"]:
        return "vc4-kms-master-reacquire-set-master-incomplete"
    if not evidence["selection_ok"]:
        return "vc4-kms-master-reacquire-selection-incomplete"
    if not evidence["baseline_crtc_readable"]:
        return "vc4-kms-master-reacquire-baseline-crtc-incomplete"
    if not evidence["independent_setcrtc_ok"]:
        return "vc4-kms-master-reacquire-setcrtc-incomplete"
    if not evidence["independent_modeset_current_fb_ok"]:
        return "vc4-kms-master-reacquire-modeset-current-fb-incomplete"
    if not evidence["independent_modeset_ok"]:
        return "vc4-kms-master-reacquire-independent-modeset-incomplete"
    if not evidence["active_crtc_ok"]:
        return "vc4-kms-master-reacquire-active-crtc-incomplete"
    if not evidence["pageflip_queued"]:
        return "vc4-kms-master-reacquire-pageflip-not-queued"
    if not evidence["flip_event_ok"]:
        return "vc4-kms-master-reacquire-event-incomplete"
    if not evidence["current_fb_ok"]:
        return "vc4-kms-master-reacquire-current-fb-incomplete"
    if not evidence["visual_ready"]:
        return "vc4-kms-master-reacquire-visual-not-ready"
    if not evidence["image_present"]:
        return "vc4-kms-master-reacquire-image-missing"
    if not evidence["visual_pixels_ok"]:
        return "vc4-kms-master-reacquire-image-mismatch"
    if not evidence["child_master_dropped"]:
        return "vc4-kms-master-reacquire-child-drop-incomplete"
    if not evidence["witness_ok"]:
        return "vc4-kms-master-reacquire-incomplete"
    if not evidence["original_master_restored"]:
        return "vc4-kms-master-reacquire-original-restore-incomplete"
    if not evidence["supervisor_ok"]:
        return "vc4-kms-master-reacquire-supervisor-incomplete"
    if evidence["probe_return_code"] != 0:
        return "vc4-kms-master-reacquire-probe-return-code"
    return CLEAR


def measure(
    serial: str,
    manifest: str,
    image_present: bool,
    image: dict[str, Any],
    probe_return_code: int | None,
) -> dict[str, Any]:
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
        "original_master_dropped": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED"
        ),
        "inherited_fd_closed": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED"
        ),
        "card_reopened": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK"
        ),
        "new_file_master": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK"
        ),
        "selection_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK"
        ),
        "baseline_crtc_readable": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK"
        ),
        "independent_setcrtc_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK"
        ),
        "independent_modeset_current_fb_ok": marker(
            serial,
            "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK",
        ),
        "independent_modeset_ok": marker(
            serial,
            "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK",
        ),
        "active_crtc_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_ACTIVE_OK"
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
        "supervisor_ok": marker(
            serial, "VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK"
        ),
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
    ][-800:]


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
        "schema_version": 2,
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
    lines = [
        "# VC4 independent DRM-master reacquisition",
        "",
        f"Validation passed: **{'true' if record['passed'] else 'false'}**",
        "",
        f"Frontier: **`{record['classification']}`**",
        "",
        f"- Probe return code: `{record['probe_return_code']}`",
        f"- DDC supplier root present: `{record['ddc_supplier_root_present']}`",
        f"- Module closure loaded: `{record['module_closure_ok']}`",
        f"- Native topology clear: `{record['native_kms_topology_clear']}`",
        f"- Existing render submission preserved: `{record['render_submission_preserved']}`",
        f"- Initial modeset completed: `{record['modeset_ok']}`",
        f"- Inherited-file page flip completed: `{record['baseline_pageflip_ok']}`",
        f"- Original drm_file dropped master: `{record['original_master_dropped']}`",
        f"- Child closed the inherited descriptor: `{record['inherited_fd_closed']}`",
        f"- Child reopened card0: `{record['card_reopened']}`",
        f"- New drm_file acquired master: `{record['new_file_master']}`",
        f"- Connector and mode selected on new file: `{record['selection_ok']}`",
        f"- Baseline CRTC state read: `{record['baseline_crtc_readable']}`",
        f"- New drm_file programmed SETCRTC: `{record['independent_setcrtc_ok']}`",
        f"- GETCRTC reports its initial FB: `{record['independent_modeset_current_fb_ok']}`",
        f"- Independent-file modeset completed: `{record['independent_modeset_ok']}`",
        f"- Active CRTC reflects the new file: `{record['active_crtc_ok']}`",
        f"- Independent-file page flip queued: `{record['pageflip_queued']}`",
        f"- Flip-complete event received: `{record['flip_event_ok']}`",
        f"- GETCRTC reports the new FB: `{record['current_fb_ok']}`",
        f"- Visual-ready hold reached: `{record['visual_ready']}`",
        f"- Exact reacquired-master pixels verified: `{record['visual_pixels_ok']}`",
        f"- Child explicitly dropped master: `{record['child_master_dropped']}`",
        f"- Reacquisition witness completed: `{record['witness_ok']}`",
        f"- Original drm_file reacquired master: `{record['original_master_restored']}`",
        f"- Supervisor completed: `{record['supervisor_ok']}`",
        f"- Timeout: `{record['timed_out']}`",
        f"- Failure stage: `{record['failure_stage']}`",
        f"- Failure errno: `{record['failure_errno']}`",
    ]
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
            "After the established inherited-file modeset and page-flip "
            "baseline, PID 1 drops DRM master on its original card file. A "
            "bounded child closes that inherited descriptor before reopening "
            "/dev/dri/card0, explicitly acquires master on the new drm_file, "
            "enumerates the connector and mode, creates an initial framebuffer, "
            "programs it with DRM_IOCTL_MODE_SETCRTC, and verifies GETCRTC. It "
            "then creates a second framebuffer, queues an event-driven page "
            "flip, consumes DRM_EVENT_FLIP_COMPLETE, and verifies the final "
            "framebuffer ID. The host freezes QEMU at the independent-file "
            "visual-ready marker and requires every captured XRGB8888 pixel to "
            "match the final pattern. The child then drops master and exits, "
            "after which the original drm_file must reacquire master before "
            "the render witness continues.",
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
