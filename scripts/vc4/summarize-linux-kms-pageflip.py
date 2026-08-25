#!/usr/bin/env python3
"""Summarize the pinned Linux VC4 inherited-master page-flip witness."""

from __future__ import annotations

import argparse
import json
import pathlib
import re
from typing import Any

CLEAR = "linux-vc4-kms-pageflip-visual-clear"

MODESET_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_FAILED stage=(\S+) errno=(\d+)"
)
PAGEFLIP_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_PAGEFLIP_FAILED stage=(\S+) errno=(\d+)"
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
            "error": "page-flip image result is not a JSON object",
        }
    return True, value


def marker(serial: str, name: str) -> bool:
    return name in serial


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
    if evidence["timed_out"]:
        return "vc4-kms-pageflip-timeout"
    if evidence["pageflip_failure"]:
        return "vc4-kms-pageflip-" + evidence["pageflip_failure"]["stage"]

    completed = all(
        evidence[name]
        for name in (
            "active_crtc_ok",
            "pageflip_queued",
            "flip_event_ok",
            "current_fb_ok",
            "pageflip_ok",
            "pageflip_supervisor_ok",
        )
    )
    if completed:
        if not evidence["visual_ready"]:
            return "vc4-kms-pageflip-visual-not-ready"
        if not evidence["image_present"]:
            return "vc4-kms-pageflip-image-missing"
        if not evidence["visual_pixels_ok"]:
            return "vc4-kms-pageflip-image-mismatch"
        if evidence["probe_return_code"] != 0:
            return "vc4-kms-pageflip-probe-return-code"
        return CLEAR
    if evidence["pageflip_queued"] and not evidence["flip_event_ok"]:
        return "vc4-kms-pageflip-event-incomplete"
    if evidence["pageflip_started"]:
        return "vc4-kms-pageflip-incomplete"
    return "vc4-kms-pageflip-not-reached"


def failure_record(match: re.Match[str] | None) -> dict[str, Any] | None:
    if match is None:
        return None
    return {
        "stage": match.group(1),
        "errno": int(match.group(2)),
    }


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
        "pageflip_started": marker(serial, "VC4_LINUX_KMS_PAGEFLIP_START"),
        "active_crtc_ok": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_ACTIVE_OK"
        ),
        "pageflip_queued": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_QUEUED"
        ),
        "flip_event_ok": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_EVENT_OK"
        ),
        "current_fb_ok": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_CURRENT_FB_OK"
        ),
        "visual_ready": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_VISUAL_READY"
        ),
        "visual_pixels_ok": image.get("passed") is True,
        "image_present": image_present,
        "pageflip_ok": marker(serial, "VC4_LINUX_KMS_PAGEFLIP_OK"),
        "pageflip_supervisor_ok": marker(
            serial, "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK"
        ),
        "timed_out": marker(serial, "VC4_LINUX_KMS_PAGEFLIP_TIMEOUT"),
        "probe_return_code": probe_return_code,
        "modeset_failure": failure_record(MODESET_FAILURE_RE.search(serial)),
        "pageflip_failure": failure_record(
            PAGEFLIP_FAILURE_RE.search(serial)
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
        or "VC4_LINUX_KMS_TOPOLOGY" in line
        or "VC4_LINUX_DRM_SUBMIT" in line
        or "timeout" in line.lower()
        or "failed" in line.lower()
    ][-600:]


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
    failure = pageflip_failure or modeset_failure
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
    lines = [
        "# VC4 inherited-master native KMS page flip",
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
        f"- Active CRTC inherited: `{record['active_crtc_ok']}`",
        f"- Page flip queued: `{record['pageflip_queued']}`",
        f"- Flip-complete event received: `{record['flip_event_ok']}`",
        f"- GETCRTC reports the new FB: `{record['current_fb_ok']}`",
        f"- Visual-ready hold reached: `{record['visual_ready']}`",
        f"- Exact flipped pixels verified: `{record['visual_pixels_ok']}`",
        f"- Page-flip witness completed: `{record['pageflip_ok']}`",
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
            "The modeset child leaves the first framebuffer on the shared "
            "drm_file. A second child creates a distinct framebuffer, queues "
            "DRM_IOCTL_MODE_PAGE_FLIP with the event flag, consumes "
            "DRM_EVENT_FLIP_COMPLETE, and checks that GETCRTC exposes the new "
            "framebuffer. The host stops QEMU at the visual-ready marker and "
            "requires every captured XRGB8888 pixel to match the deterministic "
            "page-flip pattern.",
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
