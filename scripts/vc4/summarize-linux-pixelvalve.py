#!/usr/bin/env python3
"""Summarize the pinned Linux VC4 pixel-valve modeset frontier."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from typing import Any


EXPECTED_MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
    "VC4_LINUX_KMS_MODESET_SUPERVISOR_START",
    "VC4_LINUX_KMS_MODESET_CONNECTOR_OK",
    "VC4_LINUX_KMS_MODESET_DUMB_OK",
    "VC4_LINUX_KMS_MODESET_MAP_OK",
    "VC4_LINUX_KMS_MODESET_FB_OK",
    "VC4_LINUX_KMS_MODESET_SETCRTC_OK",
    "VC4_LINUX_KMS_MODESET_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
)

REQUIRED_OUTCOMES = (
    "dependencies",
    "source",
    "pinned",
    "fixture",
    "dtb",
    "build",
    "pixelvalve",
    "regressions",
    "render_regression",
    "kms_runtime",
)

MODESET_FAILURE_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_FAILED stage=(?P<stage>[^ ]+) "
    r"errno=(?P<errno>\d+)"
)
MODESET_CONNECTOR_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_CONNECTOR_OK connector=(?P<connector>\d+) "
    r"crtc=(?P<crtc>\d+) mode=(?P<width>\d+)x(?P<height>\d+) "
    r"clock=(?P<clock>\d+)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, type=Path)
    parser.add_argument("--return-code", type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--outcome", action="append", default=[])
    return parser.parse_args()


def parse_outcomes(values: list[str]) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        key, outcome = value.split("=", 1)
        if not key or not outcome:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        outcomes[key] = outcome
    return outcomes


def classify(
    markers: dict[str, bool],
    outcomes: dict[str, str],
    failure: dict[str, Any] | None,
) -> str:
    failed_steps = [
        step for step in REQUIRED_OUTCOMES
        if outcomes.get(step, "skipped") != "success"
    ]
    if failed_steps:
        return "workflow-" + "-".join(failed_steps) + "-failed"
    if not markers["VC4_LINUX_MODULE_CLOSURE_OK"]:
        return "vc4-module-closure-unavailable"
    if not markers["VC4_LINUX_KMS_TOPOLOGY_OK"]:
        return "vc4-kms-topology-frontier"
    if failure is not None:
        return f"vc4-kms-modeset-{failure['stage']}-failed"
    if markers["VC4_LINUX_KMS_MODESET_TIMEOUT"]:
        return "vc4-kms-modeset-timeout"
    if not markers["VC4_LINUX_KMS_MODESET_SETCRTC_OK"]:
        return "vc4-kms-setcrtc-unavailable"
    if not markers["VC4_LINUX_KMS_MODESET_OK"]:
        return "vc4-kms-modeset-incomplete"
    if not markers["VC4_LINUX_DRM_SUBMIT_OK"]:
        return "vc4-render-submit-regression-after-modeset"
    return "linux-vc4-pixelvalve-modeset-clear"


def read_return_code(path: Path | None) -> int | None:
    if path is None or not path.is_file():
        return None
    text = path.read_text().strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as error:
        raise SystemExit(f"invalid probe return code {text!r}") from error


def main() -> int:
    args = parse_args()
    outcomes = parse_outcomes(args.outcome)
    serial = (
        args.serial.read_text(errors="replace")
        if args.serial.is_file()
        else ""
    )

    markers = {marker: marker in serial for marker in EXPECTED_MARKERS}
    markers["VC4_LINUX_KMS_MODESET_TIMEOUT"] = (
        "VC4_LINUX_KMS_MODESET_TIMEOUT" in serial
    )

    failure_match = MODESET_FAILURE_RE.search(serial)
    failure = None
    if failure_match is not None:
        failure = {
            "stage": failure_match.group("stage"),
            "errno": int(failure_match.group("errno")),
        }

    connector_match = MODESET_CONNECTOR_RE.search(serial)
    connector = None
    if connector_match is not None:
        connector = {
            key: int(value)
            for key, value in connector_match.groupdict().items()
        }

    classification = classify(markers, outcomes, failure)
    passed = classification == "linux-vc4-pixelvalve-modeset-clear"
    probe_return_code = read_return_code(args.return_code)

    record = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        "passed": passed,
        "classification": classification,
        "outcomes": outcomes,
        "markers": markers,
        "modeset_failure": failure,
        "modeset_connector": connector,
        "probe_return_code": probe_return_code,
        "serial_tail": serial.splitlines()[-400:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 Linux pixel-valve modeset frontier",
        "",
        f"Validation passed: **{'true' if passed else 'false'}**",
        "",
        f"Frontier: **`{classification}`**",
        "",
        f"- Pixel-valve QTest: `{outcomes.get('pixelvalve', 'skipped')}`",
        f"- KMS topology: `{markers['VC4_LINUX_KMS_TOPOLOGY_OK']}`",
        f"- SETCRTC completed: `{markers['VC4_LINUX_KMS_MODESET_SETCRTC_OK']}`",
        f"- Modeset witness completed: `{markers['VC4_LINUX_KMS_MODESET_OK']}`",
        f"- Renderer submit preserved: `{markers['VC4_LINUX_DRM_SUBMIT_OK']}`",
        f"- Probe return code: `{probe_return_code}`",
    ]
    if connector is not None:
        lines.extend((
            "",
            "## Selected scanout",
            "",
            f"- Connector: `{connector['connector']}`",
            f"- CRTC: `{connector['crtc']}`",
            f"- Mode: `{connector['width']}x{connector['height']}`",
            f"- Pixel clock: `{connector['clock']} kHz`",
        ))
    if failure is not None:
        lines.extend((
            "",
            "## Modeset failure",
            "",
            f"- Stage: `{failure['stage']}`",
            f"- Errno: `{failure['errno']}`",
        ))
    args.markdown.write_text("\n".join(lines) + "\n")

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
