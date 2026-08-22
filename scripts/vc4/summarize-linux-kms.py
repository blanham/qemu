#!/usr/bin/env python3
"""Summarize the pinned Linux VC4 KMS frontier from serial evidence."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import re
from typing import Any

EXPECTED_MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
    "VC4_LINUX_KMS_RESOURCES_OK",
    "VC4_LINUX_KMS_CRTC_OK",
    "VC4_LINUX_KMS_CONNECTOR_OBJECT_OK",
    "VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK",
    "VC4_LINUX_KMS_CONNECTED_OK",
    "VC4_LINUX_KMS_MODE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
)

KMS_DONE_RE = re.compile(
    r"VC4_LINUX_KMS_DONE crtcs=(?P<crtcs>\d+) "
    r"connector_objects=(?P<connector_objects>\d+) "
    r"physical=(?P<physical>\d+) connected=(?P<connected>\d+) "
    r"modes=(?P<modes>\d+)"
)
CONNECTOR_RE = re.compile(
    r"VC4_LINUX_KMS_CONNECTOR id=(?P<id>\d+) "
    r"type=(?P<type>\d+) type_id=(?P<type_id>\d+) "
    r"connection=(?P<connection>\d+) encoder=(?P<encoder>\d+) "
    r"modes=(?P<modes>\d+) encoders=(?P<encoders>\d+) "
    r"props=(?P<props>\d+) physical=(?P<physical>\d+)"
)
BIND_RE = re.compile(
    r"vc4-drm soc:gpu: bound (?P<device>\S+) "
    r"\(ops (?P<ops>\S+) \[vc4\]\)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result", required=True, type=pathlib.Path)
    parser.add_argument("--serial", required=True, type=pathlib.Path)
    parser.add_argument("--json", required=True, type=pathlib.Path)
    parser.add_argument("--markdown", required=True, type=pathlib.Path)
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


def classify(markers: dict[str, bool], outcomes: dict[str, str]) -> str:
    failed_steps = [key for key, value in outcomes.items() if value != "success"]
    if failed_steps:
        return "workflow-" + "-".join(failed_steps) + "-failed"

    # The workflow proves module loading and SUBMIT_CL in a dedicated
    # render-only boot.  Do not reinterpret absent render markers in
    # the separate native-KMS boot as a renderer regression.
    if "render_regression" not in outcomes:
        if not markers["VC4_LINUX_MODULE_CLOSURE_OK"]:
            return "vc4-module-closure-unavailable"
        if not markers["VC4_LINUX_DRM_SUBMIT_OK"]:
            return "vc4-render-submit-regression"
    if not markers["VC4_LINUX_KMS_RESOURCES_OK"]:
        return "vc4-component-bind-frontier"
    if not markers["VC4_LINUX_KMS_CRTC_OK"]:
        return "vc4-kms-crtc-unavailable"
    if not markers["VC4_LINUX_KMS_CONNECTOR_OBJECT_OK"]:
        return "vc4-kms-connector-object-unavailable"
    if not markers["VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK"]:
        return "vc4-kms-physical-connector-unavailable"
    if not markers["VC4_LINUX_KMS_CONNECTED_OK"]:
        return "vc4-kms-physical-connector-disconnected"
    if not markers["VC4_LINUX_KMS_MODE_OK"]:
        return "vc4-kms-connected-without-modes"
    if not markers["VC4_LINUX_KMS_TOPOLOGY_OK"]:
        return "vc4-kms-topology-incomplete"
    return "linux-vc4-kms-topology-clear"


def main() -> int:
    args = parse_args()
    outcomes = parse_outcomes(args.outcome)
    result: dict[str, Any] = {}
    if args.result.is_file():
        result = json.loads(args.result.read_text())
    serial = args.serial.read_text(errors="replace") if args.serial.is_file() else ""

    markers = {marker: marker in serial for marker in EXPECTED_MARKERS}
    counts = {
        "crtcs": None,
        "connector_objects": None,
        "physical": None,
        "connected": None,
        "modes": None,
    }
    match = KMS_DONE_RE.search(serial)
    if match:
        counts = {key: int(value) for key, value in match.groupdict().items()}

    connectors = [
        {key: int(value) for key, value in match.groupdict().items()}
        for match in CONNECTOR_RE.finditer(serial)
    ]
    components = [match.groupdict() for match in BIND_RE.finditer(serial)]
    classification = classify(markers, outcomes)
    passed = classification == "linux-vc4-kms-topology-clear"
    render_submission_preserved = (
        outcomes["render_regression"] == "success"
        if "render_regression" in outcomes
        else markers["VC4_LINUX_DRM_SUBMIT_OK"]
    )

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
        "counts": counts,
        "components": components,
        "connectors": connectors,
        "probe_result": result,
        "serial_tail": serial.splitlines()[-320:],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 pinned Linux KMS frontier",
        "",
        f"Validation passed: **{'true' if passed else 'false'}**",
        "",
        f"Frontier: **`{classification}`**",
        "",
        f"- Render submission preserved: `{render_submission_preserved}`",
        f"- CRTC discovered: `{markers['VC4_LINUX_KMS_CRTC_OK']}`",
        f"- Physical connector discovered: `{markers['VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK']}`",
        f"- Physical connector connected: `{markers['VC4_LINUX_KMS_CONNECTED_OK']}`",
        f"- Display mode discovered: `{markers['VC4_LINUX_KMS_MODE_OK']}`",
        f"- Complete KMS topology: `{markers['VC4_LINUX_KMS_TOPOLOGY_OK']}`",
        "",
        "## Resource counts",
        "",
        f"- CRTCs: `{counts['crtcs']}`",
        f"- Connector objects: `{counts['connector_objects']}`",
        f"- Physical connectors: `{counts['physical']}`",
        f"- Connected physical connectors: `{counts['connected']}`",
        f"- Modes on connected physical connectors: `{counts['modes']}`",
    ]
    if components:
        lines.extend(("", "## Bound VC4 components", ""))
        lines.extend(
            f"- `{component['device']}` via `{component['ops']}`"
            for component in components
        )
    if connectors:
        lines.extend(("", "## DRM connectors", ""))
        lines.extend(
            "- id `{id}`, type `{type}:{type_id}`, connection `{connection}`, "
            "modes `{modes}`, physical `{physical}`".format(**connector)
            for connector in connectors
        )
    args.markdown.write_text("\n".join(lines) + "\n")

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
