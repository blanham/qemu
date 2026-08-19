#!/usr/bin/env python3
"""Instrument and classify the VC4 stock-firmware ARM handoff transition.

Instrumentation is temporary: callers build and run it, preserve the report,
then reset all C sources.  The resulting trace distinguishes a pre-release VPU
stall from a missing ARM-control side effect, an overwritten CPU run state, and
an ARM entry-state failure.
"""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import re
from typing import Any

MARKER_PREFIX = "VC4_TRANSITION"

HALTED_RE = re.compile(
    r"^(?P<indent>\s*)(?P<expr>.+?)->halted\s*=\s*"
    r"(?P<value>true|false|0|1)\s*;(?P<tail>.*)$"
)
STOPPED_RE = re.compile(
    r"^(?P<indent>\s*)(?P<expr>.+?)->stopped\s*=\s*"
    r"(?P<value>true|false|0|1)\s*;(?P<tail>.*)$"
)
CALL_RE = re.compile(
    r"^(?P<indent>\s*)(?P<name>cpu_reset|qemu_cpu_kick|cpu_resume)"
    r"\((?P<expr>[^;]+)\)\s*;(?P<tail>.*)$"
)
SET_PC_RE = re.compile(
    r"^(?P<indent>\s*)cpu_set_pc\((?P<expr>[^,]+),\s*"
    r"(?P<value>[^;]+)\)\s*;(?P<tail>.*)$"
)
FUNCTION_RE = re.compile(
    r"(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\([^;]*$"
)
TRACE_RE = re.compile(
    r"VC4_TRANSITION\s+(?P<kind>[A-Z_]+)\s+"
    r"(?P<path>[^: ]+):(?P<line>\d+)\s+"
    r"(?P<body>.*)$"
)


def relevant(path: Path, text: str) -> bool:
    if path.suffix != ".c":
        return False
    lower = text.lower()
    return any(term in lower for term in (
        "raspi3b-vc4-hetero",
        "bcm283",
        "vc4",
        "arm_control",
        "armctrl",
        "pm_proc",
        "interrupt_multicoresync",
    ))


def add_header(text: str) -> str:
    header = '#include "qemu/log.h"\n'
    if header in text:
        return text
    includes = list(re.finditer(r'^#include [^\n]+\n', text, re.M))
    if not includes:
        raise RuntimeError("could not locate include block")
    position = includes[-1].end()
    return text[:position] + header + text[position:]


def c_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def marker(indent: str, kind: str, rel: str, line: int,
           body_format: str, arguments: str = "") -> str:
    text = (
        f"{MARKER_PREFIX} {kind} {rel}:{line} {body_format}\\n"
    )
    suffix = f", {arguments}" if arguments else ""
    return (
        f'{indent}qemu_log_mask(LOG_GUEST_ERROR, "{c_string(text)}"'
        f'{suffix});\n'
    )


def materialize(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    manifest: list[dict[str, Any]] = []
    changed: list[str] = []
    directories = [root / "hw" / "arm", root / "hw" / "misc"]

    for path in sorted(
        p for directory in directories if directory.is_dir()
        for p in directory.glob("*.c")
    ):
        original = path.read_text(encoding="utf-8")
        if not relevant(path, original):
            continue
        rel = str(path.relative_to(root))
        lines = original.splitlines(keepends=True)
        output: list[str] = []
        function: str | None = None
        pending_signature = ""
        file_manifest: list[dict[str, Any]] = []

        for line_number, line in enumerate(lines, 1):
            stripped = line.rstrip("\n")
            pending_signature += " " + stripped.strip()
            if ";" in stripped:
                pending_signature = ""
            match_function = FUNCTION_RE.search(pending_signature)
            if match_function and "{" in stripped:
                function = match_function.group("name")
                pending_signature = ""
            elif "{" in stripped and "(" in pending_signature:
                before = pending_signature.split("(", 1)[0].split()
                if before:
                    function = before[-1].lstrip("*")
                pending_signature = ""

            output.append(line)

            halted = HALTED_RE.match(stripped)
            if halted:
                expr = halted.group("expr").strip()
                value = halted.group("value")
                output.append(marker(
                    halted.group("indent"), "HALTED", rel, line_number,
                    f"function={function} value={value} "
                    "halted=%d stopped=%d cpu=%p",
                    f"({expr})->halted, ({expr})->stopped, (void *)({expr})",
                ))
                file_manifest.append({
                    "kind": "HALTED",
                    "line": line_number,
                    "function": function,
                    "expression": expr,
                    "value": value,
                })
                continue

            stopped = STOPPED_RE.match(stripped)
            if stopped:
                expr = stopped.group("expr").strip()
                value = stopped.group("value")
                output.append(marker(
                    stopped.group("indent"), "STOPPED", rel, line_number,
                    f"function={function} value={value} "
                    "halted=%d stopped=%d cpu=%p",
                    f"({expr})->halted, ({expr})->stopped, (void *)({expr})",
                ))
                file_manifest.append({
                    "kind": "STOPPED",
                    "line": line_number,
                    "function": function,
                    "expression": expr,
                    "value": value,
                })
                continue

            call = CALL_RE.match(stripped)
            if call:
                expr = call.group("expr").strip()
                name = call.group("name")
                output.append(marker(
                    call.group("indent"), name.upper(), rel, line_number,
                    f"function={function} cpu=%p",
                    f"(void *)({expr})",
                ))
                file_manifest.append({
                    "kind": name.upper(),
                    "line": line_number,
                    "function": function,
                    "expression": expr,
                })
                continue

            set_pc = SET_PC_RE.match(stripped)
            if set_pc:
                expr = set_pc.group("expr").strip()
                value = set_pc.group("value").strip()
                output.append(marker(
                    set_pc.group("indent"), "SET_PC", rel, line_number,
                    f"function={function} value=0x%" " PRIx64 " " cpu=%p",
                    f"(uint64_t)({value}), (void *)({expr})",
                ))
                file_manifest.append({
                    "kind": "SET_PC",
                    "line": line_number,
                    "function": function,
                    "expression": expr,
                    "value": value,
                })

        if not file_manifest:
            continue
        updated = add_header("".join(output))
        path.write_text(updated, encoding="utf-8")
        changed.append(rel)
        manifest.extend({"path": rel, **item} for item in file_manifest)

    result = {
        "schema_version": 1,
        "changed_files": changed,
        "markers": manifest,
    }
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if manifest else 3


def parse_register_pc(register_text: str) -> str | None:
    patterns = [
        re.compile(r"\bPC=([0-9a-fA-F]+)"),
        re.compile(r"\bpc\s+0x([0-9a-fA-F]+)", re.I),
        re.compile(r"\bPC\s+([0-9a-fA-F]+)"),
    ]
    for pattern in patterns:
        match = pattern.search(register_text)
        if match:
            return "0x" + match.group(1).lower()
    return None


def classify(args: argparse.Namespace) -> int:
    probe = json.loads(args.probe.read_text(encoding="utf-8"))
    stderr = args.stderr.read_text(encoding="utf-8", errors="replace")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = []
    for sequence, line in enumerate(stderr.splitlines()):
        match = TRACE_RE.search(line)
        if not match:
            continue
        events.append({
            "sequence": sequence,
            "kind": match.group("kind"),
            "path": match.group("path"),
            "line": int(match.group("line")),
            "body": match.group("body"),
            "text": line,
        })

    counts = Counter(event["kind"] for event in events)
    snapshot = probe.get("cpu_snapshot") or {}
    cpus = snapshot.get("cpus") or []
    cpu_summary = []
    arm_cpus = []
    for cpu in cpus:
        record = {
            "cpu_index": cpu.get("cpu_index"),
            "qom_type": cpu.get("qom_type"),
            "halted": cpu.get("halted"),
            "pc": parse_register_pc(str(cpu.get("registers", ""))),
            "registers": cpu.get("registers"),
        }
        cpu_summary.append(record)
        if "arm" in str(record["qom_type"]).lower() \
                or "aarch64" in str(record["qom_type"]).lower():
            arm_cpus.append(record)

    kernel_before = probe.get("kernel_word_before_boot")
    kernel_after = probe.get("kernel_word_after_boot")
    kernel_loaded = (
        kernel_before is not None
        and kernel_after is not None
        and kernel_before != kernel_after
        and kernel_after not in {"0x0000000000000000", "0xffffffffffffffff"}
    )
    any_arm_runnable = any(cpu.get("halted") is False for cpu in arm_cpus)
    unhalt_events = counts.get("HALTED", 0) + counts.get("CPU_RESUME", 0)
    reset_events = counts.get("CPU_RESET", 0)
    set_pc_events = counts.get("SET_PC", 0)

    if probe.get("signature_seen"):
        diagnosis = "handoff-reached"
    elif not kernel_loaded and unhalt_events == 0 and reset_events == 0:
        diagnosis = "pre-arm-release-vpu-stall"
    elif kernel_loaded and unhalt_events == 0 and reset_events == 0:
        diagnosis = "missing-arm-release-side-effect"
    elif not any_arm_runnable and (unhalt_events or reset_events):
        diagnosis = "arm-release-state-overwritten"
    elif any_arm_runnable and kernel_loaded and set_pc_events == 0:
        diagnosis = "missing-arm-entry-pc-transfer"
    elif any_arm_runnable and kernel_loaded:
        diagnosis = "arm-entry-state-or-exception"
    elif unhalt_events or reset_events:
        diagnosis = "release-before-payload-load"
    else:
        diagnosis = "insufficient-transition-evidence"

    result = {
        "schema_version": 1,
        "diagnosis": diagnosis,
        "signature_seen": bool(probe.get("signature_seen")),
        "kernel_loaded": kernel_loaded,
        "kernel_word_before_boot": kernel_before,
        "kernel_word_after_boot": kernel_after,
        "event_counts": dict(counts),
        "events": events[-3000:],
        "instrumentation": manifest,
        "cpu_summary": cpu_summary,
        "arm_cpus": arm_cpus,
        "probe": probe,
    }
    args.out.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    next_steps = {
        "handoff-reached": (
            "Promote the clean handoff result; do not retain instrumentation."
        ),
        "pre-arm-release-vpu-stall": (
            "Return to the final VPU PC and scalar/MMIO frontier.  ARM "
            "scheduler changes are downstream of the observed stall."
        ),
        "missing-arm-release-side-effect": (
            "Implement the documented ARM-control or power-register release "
            "side effect at the exact write site shown in the trace."
        ),
        "arm-release-state-overwritten": (
            "Move the power-on transition to a run-on-CPU callback and set "
            "reset, halted, stopped, and kick state atomically."
        ),
        "missing-arm-entry-pc-transfer": (
            "Transfer the firmware-programmed ARM entry address into the "
            "released CPU PC before it becomes runnable."
        ),
        "arm-entry-state-or-exception": (
            "Inspect the recorded ARM registers and first exception; the "
            "release occurred and the payload was loaded."
        ),
        "release-before-payload-load": (
            "Correct ordering between payload DMA/load completion and ARM "
            "release; do not add a longer delay."
        ),
        "insufficient-transition-evidence": (
            "Instrument the concrete ARM-control MMIO device named in the "
            "stock frontier before selecting another behavioral candidate."
        ),
    }
    lines = [
        "# VC4 ARM transition trace",
        "",
        f"Diagnosis: **`{diagnosis}`**",
        f"Stock payload signature: **{result['signature_seen']}**",
        f"Kernel image changed at load address: **{kernel_loaded}**",
        "",
        "## Transition counts",
        "",
    ]
    if counts:
        lines.extend(
            f"- `{kind}`: {count}" for kind, count in counts.most_common()
        )
    else:
        lines.append("- No instrumented CPU transition executed.")
    lines.extend(["", "## ARM CPU snapshot", ""])
    for cpu in arm_cpus:
        lines.append(
            f"- CPU {cpu.get('cpu_index')}: halted={cpu.get('halted')}, "
            f"PC={cpu.get('pc')}, type={cpu.get('qom_type')}"
        )
    lines.extend([
        "",
        "## Required next step",
        "",
        next_steps[diagnosis],
        "",
        "## Last transition events",
        "",
    ])
    for event in events[-200:]:
        lines.append(
            f"- `{event['path']}:{event['line']}` "
            f"`{event['kind']}` — {event['body']}"
        )
    args.markdown.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    commands = root.add_subparsers(dest="command", required=True)

    materialize_parser = commands.add_parser("materialize")
    materialize_parser.add_argument("--root", type=Path, default=Path("."))
    materialize_parser.add_argument("--out", required=True, type=Path)
    materialize_parser.set_defaults(func=materialize)

    classify_parser = commands.add_parser("classify")
    classify_parser.add_argument("--probe", required=True, type=Path)
    classify_parser.add_argument("--stderr", required=True, type=Path)
    classify_parser.add_argument("--manifest", required=True, type=Path)
    classify_parser.add_argument("--out", required=True, type=Path)
    classify_parser.add_argument("--markdown", required=True, type=Path)
    classify_parser.set_defaults(func=classify)
    return root


def main() -> int:
    args = parser().parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
