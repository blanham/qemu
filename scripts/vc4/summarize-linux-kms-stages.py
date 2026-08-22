#!/usr/bin/env python3
"""Summarize staged Linux VC4 KMS component-binding probes."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import re
from typing import Any


BIND_RE = re.compile(
    r"vc4-drm soc:gpu: bound (?P<device>\S+) "
    r"\(ops (?P<ops>\S+) \[vc4\]\)"
)
FAILED_BIND_RE = re.compile(
    r"vc4-drm soc:gpu: failed to bind (?P<device>\S+) "
    r"\(ops (?P<ops>\S+) \[vc4\]\): (?P<error>-?\d+)"
)
MARKERS = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_KMS_RESOURCES_OK",
    "VC4_LINUX_KMS_CRTC_OK",
    "VC4_LINUX_KMS_CONNECTOR_OBJECT_OK",
    "VC4_LINUX_KMS_PHYSICAL_CONNECTOR_OK",
    "VC4_LINUX_KMS_CONNECTED_OK",
    "VC4_LINUX_KMS_MODE_OK",
    "VC4_LINUX_KMS_TOPOLOGY_OK",
)
INTERESTING = (
    "vc4-drm",
    "of_clk_hw_onecell_get",
    "failed",
    "error",
    "clock",
    "ddc",
    "edid",
    "hpd",
    "deferred probe",
    "waiting for supplier",
    "kernel panic",
    "oops",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--stage",
        action="append",
        default=[],
        metavar="NAME=DIR",
        help="stage name and raspi3-linux-probe output directory",
    )
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    return parser.parse_args()


def load_stage(value: str) -> tuple[str, Path]:
    if "=" not in value:
        raise SystemExit(f"invalid --stage value: {value!r}")
    name, directory = value.split("=", 1)
    if not name or not directory:
        raise SystemExit(f"invalid --stage value: {value!r}")
    return name, Path(directory)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def classify(serial: str, markers: dict[str, bool]) -> str:
    lower = serial.lower()
    if "kernel panic" in lower or "oops:" in lower:
        return "guest-kernel-failure"
    if not markers["VC4_LINUX_MODULE_CLOSURE_OK"]:
        return "module-closure-unavailable"
    if markers["VC4_LINUX_KMS_TOPOLOGY_OK"]:
        return "kms-topology-clear"
    if markers["VC4_LINUX_KMS_RESOURCES_OK"]:
        return "drm-device-registered"
    failed = list(FAILED_BIND_RE.finditer(serial))
    if failed:
        last = failed[-1].groupdict()
        return f"component-bind-failed:{last['ops']}:{last['error']}"
    if "adev bind failed" in serial or "probe with driver vc4-drm failed" in serial:
        return "component-master-bind-failed"
    return "component-master-not-registered"


def summarize(name: str, directory: Path) -> dict[str, Any]:
    serial_path = directory / "serial.log"
    serial = serial_path.read_text(errors="replace") if serial_path.is_file() else ""
    result = read_json(directory / "result.json")
    markers = {marker: marker in serial for marker in MARKERS}
    components = [match.groupdict() for match in BIND_RE.finditer(serial)]
    failed_components = [
        {
            **match.groupdict(),
            "error": int(match.group("error")),
        }
        for match in FAILED_BIND_RE.finditer(serial)
    ]
    interesting = [
        line
        for line in serial.splitlines()
        if any(token in line.lower() for token in INTERESTING)
    ][-160:]
    return {
        "name": name,
        "directory": str(directory),
        "classification": classify(serial, markers),
        "markers": markers,
        "components": components,
        "failed_components": failed_components,
        "interesting_serial": interesting,
        "probe_result": result,
    }


def main() -> int:
    args = parse_args()
    stages = [summarize(*load_stage(value)) for value in args.stage]
    by_name = {stage["name"]: stage for stage in stages}

    render = by_name.get("render", {})
    crtc = by_name.get("crtc", {})
    hdmi = by_name.get("hdmi", {})
    full = by_name.get("full", {})
    comparison = {
        "render_registers_drm": bool(
            render.get("markers", {}).get("VC4_LINUX_KMS_RESOURCES_OK")
        ),
        "crtc_registers_drm": bool(
            crtc.get("markers", {}).get("VC4_LINUX_KMS_RESOURCES_OK")
        ),
        "hdmi_registers_drm": bool(
            hdmi.get("markers", {}).get("VC4_LINUX_KMS_RESOURCES_OK")
        ),
        "full_topology_clear": bool(
            full.get("markers", {}).get("VC4_LINUX_KMS_TOPOLOGY_OK")
        ),
    }
    comparison["hdmi_is_first_regression"] = (
        comparison["crtc_registers_drm"]
        and not comparison["hdmi_registers_drm"]
    )

    record = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "stages": stages,
        "comparison": comparison,
        "passed": comparison["full_topology_clear"],
    }
    args.json.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")

    lines = [
        "# VC4 Linux KMS staged component frontier",
        "",
        f"- Source SHA: `{args.source_sha}`",
        f"- Full topology clear: `{record['passed']}`",
        f"- HDMI is the first staged regression: `{comparison['hdmi_is_first_regression']}`",
        "",
        "## Stage results",
        "",
    ]
    for stage in stages:
        lines.extend(
            (
                f"### {stage['name']}",
                "",
                f"- Frontier: **`{stage['classification']}`**",
                f"- DRM registered: `{stage['markers']['VC4_LINUX_KMS_RESOURCES_OK']}`",
                f"- KMS topology clear: `{stage['markers']['VC4_LINUX_KMS_TOPOLOGY_OK']}`",
                "- Bound components: "
                + (
                    ", ".join(
                        f"`{component['device']}`/`{component['ops']}`"
                        for component in stage["components"]
                    )
                    or "none"
                ),
                "",
            )
        )
        if stage["failed_components"]:
            lines.append("Failed component binds:")
            lines.extend(
                f"- `{item['device']}` / `{item['ops']}`: `{item['error']}`"
                for item in stage["failed_components"]
            )
            lines.append("")
        if stage["interesting_serial"]:
            lines.append("Relevant serial tail:")
            lines.extend(f"- `{line}`" for line in stage["interesting_serial"][-40:])
            lines.append("")
    args.markdown.write_text("\n".join(lines) + "\n")

    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
