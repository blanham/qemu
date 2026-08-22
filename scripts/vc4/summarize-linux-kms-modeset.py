#!/usr/bin/env python3
"""Summarize the supervised native Linux VC4 KMS modeset frontier."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from typing import Any


MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
    "VC4_LINUX_KMS_MODESET_SUPERVISOR_START",
    "VC4_LINUX_KMS_MODESET_START",
    "VC4_LINUX_KMS_MODESET_CONNECTOR_OK",
    "VC4_LINUX_KMS_MODESET_DUMB_OK",
    "VC4_LINUX_KMS_MODESET_MAP_OK",
    "VC4_LINUX_KMS_MODESET_FB_OK",
    "VC4_LINUX_KMS_MODESET_SETCRTC_OK",
    "VC4_LINUX_KMS_MODESET_OK",
    "VC4_LINUX_KMS_MODESET_TIMEOUT",
    "VC4_LINUX_KMS_MODESET_SUPERVISOR_DONE",
)
FAIL_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_FAILED stage=(?P<stage>[^ ]+) "
    r"errno=(?P<errno>-?\d+)"
)
CONNECTOR_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_CONNECTOR_OK connector=(?P<connector>\d+) "
    r"crtc=(?P<crtc>\d+) mode=(?P<width>\d+)x(?P<height>\d+) "
    r"clock=(?P<clock>\d+)"
)
DUMB_RE = re.compile(
    r"VC4_LINUX_KMS_MODESET_DUMB_OK handle=(?P<handle>\d+) "
    r"pitch=(?P<pitch>\d+) size=(?P<size>\d+)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", required=True, type=Path)
    parser.add_argument("--serial", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--outcome", action="append", default=[])
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    return parser.parse_args()


def parse_outcomes(values: list[str]) -> dict[str, str]:
    outcomes: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        key, result = value.split("=", 1)
        if not key or not result:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        outcomes[key] = result
    return outcomes


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def classify(
    markers: dict[str, bool], outcomes: dict[str, str], failures: list[dict[str, Any]]
) -> str:
    failed_steps = [key for key, value in outcomes.items() if value != "success"]
    if failed_steps:
        return "workflow-" + "-".join(failed_steps) + "-failed"
    if not markers["VC4_LINUX_KMS_TOPOLOGY_OK"]:
        return "kms-topology-regression"
    if not markers["VC4_LINUX_KMS_MODESET_SUPERVISOR_START"]:
        return "modeset-probe-not-invoked"
    if not markers["VC4_LINUX_KMS_MODESET_START"]:
        return "modeset-child-not-started"
    if not markers["VC4_LINUX_KMS_MODESET_CONNECTOR_OK"]:
        return "modeset-connector-selection-failed"
    if not markers["VC4_LINUX_KMS_MODESET_DUMB_OK"]:
        return "modeset-dumb-buffer-create-failed"
    if not markers["VC4_LINUX_KMS_MODESET_MAP_OK"]:
        return "modeset-dumb-buffer-map-failed"
    if not markers["VC4_LINUX_KMS_MODESET_FB_OK"]:
        return "modeset-framebuffer-create-failed"
    if not markers["VC4_LINUX_KMS_MODESET_SETCRTC_OK"]:
        if markers["VC4_LINUX_KMS_MODESET_TIMEOUT"]:
            return "modeset-setcrtc-timeout"
        if failures:
            last = failures[-1]
            return f"modeset-{last['stage']}-errno-{last['errno']}"
        return "modeset-setcrtc-unavailable"
    if not markers["VC4_LINUX_KMS_MODESET_OK"]:
        return "modeset-post-setcrtc-incomplete"
    return "linux-vc4-kms-modeset-clear"


def main() -> int:
    args = parse_args()
    outcomes = parse_outcomes(args.outcome)
    result = read_json(args.result)
    serial = args.serial.read_text(errors="replace") if args.serial.is_file() else ""
    markers = {marker: marker in serial for marker in MARKERS}
    failures = [
        {
            "stage": match.group("stage"),
            "errno": int(match.group("errno")),
        }
        for match in FAIL_RE.finditer(serial)
    ]
    connector_match = CONNECTOR_RE.search(serial)
    dumb_match = DUMB_RE.search(serial)
    connector = (
        {key: int(value) for key, value in connector_match.groupdict().items()}
        if connector_match
        else None
    )
    dumb = (
        {key: int(value) for key, value in dumb_match.groupdict().items()}
        if dumb_match
        else None
    )
    classification = classify(markers, outcomes, failures)
    passed = classification == "linux-vc4-kms-modeset-clear"
    interesting = [
        line
        for line in serial.splitlines()
        if any(
            token in line.lower()
            for token in (
                "vc4_linux_kms_modeset",
                "vc4-drm",
                "drm",
                "hvs",
                "pixelvalve",
                "hdmi",
                "vblank",
                "failed",
                "error",
                "timeout",
                "oops",
                "panic",
            )
        )
    ][-240:]
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
        "failures": failures,
        "connector": connector,
        "dumb_buffer": dumb,
        "probe_result": result,
        "interesting_serial": interesting,
        "serial_tail": serial.splitlines()[-360:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 pinned Linux native KMS modeset frontier",
        "",
        f"- Validation passed: **`{passed}`**",
        f"- Frontier: **`{classification}`**",
        f"- Connector selected: `{markers['VC4_LINUX_KMS_MODESET_CONNECTOR_OK']}`",
        f"- Dumb buffer created: `{markers['VC4_LINUX_KMS_MODESET_DUMB_OK']}`",
        f"- Buffer mapped and written: `{markers['VC4_LINUX_KMS_MODESET_MAP_OK']}`",
        f"- DRM framebuffer created: `{markers['VC4_LINUX_KMS_MODESET_FB_OK']}`",
        f"- SETCRTC completed: `{markers['VC4_LINUX_KMS_MODESET_SETCRTC_OK']}`",
        f"- Supervised timeout: `{markers['VC4_LINUX_KMS_MODESET_TIMEOUT']}`",
        "",
        f"- Connector/mode: `{connector}`",
        f"- Dumb buffer: `{dumb}`",
        f"- Failures: `{failures}`",
        "",
        "## Relevant serial evidence",
        "",
    ]
    lines.extend(f"- `{line}`" for line in interesting)
    args.markdown.write_text("\n".join(lines) + "\n")
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
