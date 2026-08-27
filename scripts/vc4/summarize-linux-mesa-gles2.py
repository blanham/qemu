#!/usr/bin/env python3
"""Summarize the pinned in-guest Mesa VC4 GLES2 execution frontier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any


CLEAR = "linux-vc4-mesa-gles2-triangle-clear"

ORDERED_MARKERS = (
    "VC4_LINUX_MESA_GLES2_SUPERVISOR_START",
    "VC4_LINUX_MESA_GLES2_START",
    "VC4_LINUX_MESA_GLES2_EGL_DISPLAY_OK",
    "VC4_LINUX_MESA_GLES2_EGL_INITIALIZE_OK",
    "VC4_LINUX_MESA_GLES2_EGL_CONFIG_OK",
    "VC4_LINUX_MESA_GLES2_EGL_SURFACE_OK",
    "VC4_LINUX_MESA_GLES2_EGL_CONTEXT_OK",
    "VC4_LINUX_MESA_GLES2_EGL_MAKE_CURRENT_OK",
    "VC4_LINUX_MESA_GLES2_RENDERER_VC4_OK",
    "VC4_LINUX_MESA_GLES2_SHADER_COMPILE_OK stage=vertex",
    "VC4_LINUX_MESA_GLES2_SHADER_COMPILE_OK stage=fragment",
    "VC4_LINUX_MESA_GLES2_PROGRAM_LINK_OK",
    "VC4_LINUX_MESA_GLES2_DRAW_START",
    "VC4_LINUX_MESA_GLES2_DRAW_OK",
    "VC4_LINUX_MESA_GLES2_FINISH_START",
    "VC4_LINUX_MESA_GLES2_FINISH_OK",
    "VC4_LINUX_MESA_GLES2_READPIXELS_START",
    "VC4_LINUX_MESA_GLES2_READPIXELS_OK",
    "VC4_LINUX_MESA_GLES2_PIXELS_OK",
    "VC4_LINUX_MESA_GLES2_OK",
    "VC4_LINUX_MESA_GLES2_SUPERVISOR_OK",
)

PACKET_NAMES = {
    0x10: "branch",
    0x11: "branch-to-sub-list",
    0x20: "gl-indexed-primitive",
    0x21: "gl-array-primitive",
    0x30: "compressed-primitive",
    0x31: "clipped-compressed-primitive",
}

FAILURE_RE = re.compile(
    r"VC4_LINUX_MESA_GLES2_FAILED stage=(\S+) "
    r"egl=0x([0-9a-fA-F]+) gl=0x([0-9a-fA-F]+) errno=(\d+)"
)
UNSUPPORTED_PACKET_RE = re.compile(
    r"bcm2835-v3d: packet 0x([0-9a-fA-F]+) "
    r"requires binning/QPU execution at 0x([0-9a-fA-F]+)"
)
CHILD_EXIT_RE = re.compile(
    r"VC4_LINUX_MESA_GLES2_CHILD_EXIT status=(\d+)"
)
CHILD_SIGNAL_RE = re.compile(
    r"VC4_LINUX_MESA_GLES2_CHILD_SIGNAL signal=(\d+)"
)
GL_INFO_RE = re.compile(
    r"VC4_LINUX_MESA_GLES2_GL_INFO renderer=(.*?) "
    r"vendor=(.*?) version=(.*)"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True, type=Path)
    parser.add_argument("--qemu-stderr", required=True, type=Path)
    parser.add_argument("--probe-result", required=True, type=Path)
    parser.add_argument("--return-code", required=True, type=Path)
    parser.add_argument("--json", required=True, type=Path)
    parser.add_argument("--markdown", required=True, type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--run-id")
    parser.add_argument("--run-attempt")
    parser.add_argument("--outcome", action="append", default=[])
    parser.add_argument("--require-harness", action="store_true")
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text(errors="replace") if path.is_file() else ""


def parse_outcomes(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        key, outcome = value.split("=", 1)
        if not key or not outcome:
            raise SystemExit(f"invalid --outcome value: {value!r}")
        result[key] = outcome
    return result


def parse_return_code(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        return int(path.read_text().strip())
    except ValueError:
        return None


def parse_probe_result(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(errors="replace"))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def marker_positions(serial: str) -> dict[str, int]:
    return {name: serial.find(name) for name in ORDERED_MARKERS}


def present_marker_order_valid(positions: dict[str, int]) -> bool:
    present = [value for value in positions.values() if value >= 0]
    return present == sorted(present) and len(present) == len(set(present))


def last_marker(positions: dict[str, int]) -> str | None:
    present = [
        (position, name)
        for name, position in positions.items()
        if position >= 0
    ]
    return max(present)[1] if present else None


def next_missing_marker(positions: dict[str, int]) -> str | None:
    for name in ORDERED_MARKERS:
        if positions[name] < 0:
            return name
    return None


def compact_marker(name: str | None) -> str:
    if name is None:
        return "unknown"
    return (
        name.removeprefix("VC4_LINUX_MESA_GLES2_")
        .lower()
        .replace(" stage=", "-")
        .replace("_", "-")
    )


def classify(evidence: dict[str, Any]) -> str:
    failed_steps = [
        name
        for name in (
            "source",
            "dependencies",
            "mesa_root",
            "modules",
            "initramfs",
            "dtb",
            "build",
            "regressions",
            "runtime",
        )
        if evidence["outcomes"].get(name, "skipped") != "success"
    ]
    if failed_steps:
        return "workflow-" + "-".join(failed_steps) + "-failed"
    if not evidence["module_closure_ok"]:
        return "vc4-mesa-module-closure-regression"
    if not evidence["submit_clear_preserved"]:
        return "vc4-mesa-submit-clear-regression"
    if evidence["exec_failed"]:
        return "vc4-mesa-gles2-exec-failed"
    if not evidence["started"]:
        return "vc4-mesa-gles2-not-reached"

    failure = evidence["failure"]
    if failure is not None:
        if failure["stage"] == "renderer-not-vc4":
            return "vc4-mesa-gles2-wrong-renderer"
        return "vc4-mesa-gles2-" + failure["stage"]

    packet = evidence["unsupported_packet"]
    if packet is not None:
        return (
            "vc4-v3d-unsupported-"
            + packet["name"]
            + f"-0x{packet['opcode']:02x}"
        )
    if evidence["timed_out"]:
        return (
            "vc4-mesa-gles2-"
            + compact_marker(evidence["last_marker"])
            + "-timeout"
        )
    if evidence["child_exit"] is not None:
        return f"vc4-mesa-gles2-child-exit-{evidence['child_exit']}"
    if evidence["child_signal"] is not None:
        return f"vc4-mesa-gles2-child-signal-{evidence['child_signal']}"
    if not evidence["marker_order_valid"]:
        return "vc4-mesa-gles2-marker-order-invalid"
    if evidence["complete"]:
        return CLEAR
    return (
        "vc4-mesa-gles2-missing-"
        + compact_marker(evidence["next_missing_marker"])
    )


def main() -> int:
    args = parse_args()
    serial = read_text(args.serial)
    qemu_stderr = read_text(args.qemu_stderr)
    probe_result = parse_probe_result(args.probe_result)
    outcomes = parse_outcomes(args.outcome)
    positions = marker_positions(serial)

    failure_match = FAILURE_RE.search(serial)
    failure = None
    if failure_match is not None:
        failure = {
            "stage": failure_match.group(1),
            "egl_error": int(failure_match.group(2), 16),
            "gl_error": int(failure_match.group(3), 16),
            "errno": int(failure_match.group(4)),
        }

    packet_match = UNSUPPORTED_PACKET_RE.search(qemu_stderr)
    unsupported_packet = None
    if packet_match is not None:
        opcode = int(packet_match.group(1), 16)
        unsupported_packet = {
            "opcode": opcode,
            "name": PACKET_NAMES.get(opcode, "packet"),
            "address": int(packet_match.group(2), 16),
        }

    child_exit_match = CHILD_EXIT_RE.search(serial)
    child_signal_match = CHILD_SIGNAL_RE.search(serial)
    gl_info_match = GL_INFO_RE.search(serial)

    evidence: dict[str, Any] = {
        "outcomes": outcomes,
        "module_closure_ok":
            "VC4_LINUX_MODULE_CLOSURE_OK" in serial,
        "submit_clear_preserved":
            "VC4_LINUX_DRM_SUBMIT_OK" in serial,
        "started": "VC4_LINUX_MESA_GLES2_START" in serial,
        "exec_failed":
            "VC4_LINUX_MESA_GLES2_EXEC_FAILED" in serial,
        "timed_out": (
            "VC4_LINUX_MESA_GLES2_TIMEOUT" in serial
            or "VC4_LINUX_MESA_GLES2_SUPERVISOR_TIMEOUT" in serial
        ),
        "failure": failure,
        "unsupported_packet": unsupported_packet,
        "child_exit": (
            int(child_exit_match.group(1))
            if child_exit_match is not None else None
        ),
        "child_signal": (
            int(child_signal_match.group(1))
            if child_signal_match is not None else None
        ),
        "renderer": (
            gl_info_match.group(1)
            if gl_info_match is not None else None
        ),
        "vendor": (
            gl_info_match.group(2)
            if gl_info_match is not None else None
        ),
        "gl_version": (
            gl_info_match.group(3)
            if gl_info_match is not None else None
        ),
        "positions": positions,
        "last_marker": last_marker(positions),
        "next_missing_marker": next_missing_marker(positions),
        "marker_order_valid": present_marker_order_valid(positions),
        "complete": all(position >= 0 for position in positions.values()),
        "probe_return_code": parse_return_code(args.return_code),
        "probe_passed": probe_result.get("passed") is True,
    }
    evidence["classification"] = classify(evidence)
    evidence["passed"] = evidence["classification"] == CLEAR
    evidence["hardware_frontier_reached"] = (
        evidence["started"]
        and "VC4_LINUX_MESA_GLES2_RENDERER_VC4_OK" in serial
        and (
            evidence["passed"]
            or evidence["unsupported_packet"] is not None
            or evidence["timed_out"]
            or evidence["failure"] is not None
        )
    )
    evidence["harness_valid"] = (
        not evidence["exec_failed"]
        and evidence["module_closure_ok"]
        and evidence["submit_clear_preserved"]
        and evidence["started"]
        and evidence["marker_order_valid"]
        and (
            evidence["passed"]
            or evidence["failure"] is not None
            or evidence["unsupported_packet"] is not None
            or evidence["timed_out"]
            or evidence["child_exit"] is not None
            or evidence["child_signal"] is not None
        )
    )

    record = {
        "schema_version": 1,
        "source_sha": args.source_sha,
        "run_id": args.run_id,
        "run_attempt": args.run_attempt,
        **evidence,
        "serial_tail": serial.splitlines()[-700:],
        "qemu_diagnostics_tail": qemu_stderr.splitlines()[-700:],
        "probe_result_summary": {
            key: probe_result.get(key)
            for key in (
                "elapsed_seconds",
                "init_seen",
                "framebuffer_marker_seen",
                "passed",
                "qemu_returncode_before_stop",
            )
        },
    }
    args.json.write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    packet = record["unsupported_packet"]
    failure = record["failure"]
    lines = [
        "# VC4 Linux Mesa GLES2 frontier",
        "",
        f"Validation passed: **{'true' if record['passed'] else 'false'}**",
        "",
        f"Harness valid: **{'true' if record['harness_valid'] else 'false'}**",
        "",
        f"Frontier: **`{record['classification']}`**",
        "",
        f"- Module closure preserved: `{record['module_closure_ok']}`",
        f"- Handwritten DRM submit preserved: `{record['submit_clear_preserved']}`",
        f"- Mesa process started: `{record['started']}`",
        f"- VC4 hardware frontier reached: `{record['hardware_frontier_reached']}`",
        f"- Last stage: `{record['last_marker']}`",
        f"- Next missing stage: `{record['next_missing_marker']}`",
        f"- Renderer: `{record['renderer']}`",
        f"- GL version: `{record['gl_version']}`",
        f"- Timed out: `{record['timed_out']}`",
        f"- Child exit: `{record['child_exit']}`",
        f"- Child signal: `{record['child_signal']}`",
        f"- Probe return code: `{record['probe_return_code']}`",
    ]
    if packet is not None:
        lines.extend((
            "",
            "## First unsupported V3D packet",
            "",
            f"- Opcode: `0x{packet['opcode']:02x}`",
            f"- Name: `{packet['name']}`",
            f"- Command-list address: `0x{packet['address']:08x}`",
        ))
    if failure is not None:
        lines.extend((
            "",
            "## Mesa probe failure",
            "",
            f"- Stage: `{failure['stage']}`",
            f"- EGL error: `0x{failure['egl_error']:04x}`",
            f"- GL error: `0x{failure['gl_error']:04x}`",
            f"- errno: `{failure['errno']}`",
        ))
    lines.extend((
        "",
        "This gate runs a pinned Mesa VC4 Gallium driver inside the "
        "AArch64 guest. It requires a hardware VC4 renderer, compiles "
        "real GLES2 shaders, queues a full-surface triangle, waits for "
        "GPU completion, and verifies readback pixels. A non-clear "
        "classification is therefore the next concrete V3D/QPU "
        "contract rather than a synthetic packet guess.",
    ))
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(json.dumps(record, indent=2, sort_keys=True))
    if args.require_harness and not record["harness_valid"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
