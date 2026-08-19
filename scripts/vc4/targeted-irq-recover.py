#!/usr/bin/env python3
"""Evaluate bounded VC4 interrupt-delivery repairs against stock firmware.

The evaluator starts from a clean, already-configured tree and compares three
small candidates against the reproducible 0x544 low-PC frontier:

* an unconditional raw-GPU mirror into the VPU interrupt controller;
* latching the VC4 external-interrupt status across a source deassertion;
* a diagnostic-only direct VPU-controller output bypass.

Only the first two are eligible for retention.  A production candidate must
build both frontends, pass the processor-control and multicore-sync smokes,
and improve the stock-firmware frontier by a material margin.  The selected
candidate is rebuilt and revalidated from the exact retained tree before this
script leaves source changes in place.  Otherwise all source files are
restored byte-for-byte.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
from pathlib import Path
import re
import subprocess
from typing import Any


SOURCE_FILES = (
    Path("hw/intc/bcm2835_ic.c"),
    Path("hw/vc4/bcm2835_vc4_intc.c"),
    Path("target/vc4/cpu.c"),
)


def run(
    command: list[str],
    *,
    check: bool = True,
    stdout: Any = None,
    stderr: Any = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        text=True,
        stdout=stdout,
        stderr=stderr,
    )


def function_span(
    text: str,
    name: str,
) -> tuple[int, int, str] | None:
    match = re.search(
        rf"\b{re.escape(name)}\s*\((.*?)\)\s*\{{",
        text,
        re.DOTALL,
    )
    if match is None:
        return None
    brace = text.find("{", match.start())
    depth = 0
    for index in range(brace, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return match.start(), index + 1, match.group(1)
    return None


def split_call_arguments(call: str) -> list[str]:
    arguments: list[str] = []
    current: list[str] = []
    depth = 0
    for character in call:
        if character == "," and depth == 0:
            arguments.append("".join(current).strip())
            current = []
            continue
        current.append(character)
        if character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
    arguments.append("".join(current).strip())
    return arguments


def gpio_input_handler(text: str, needle: str) -> str | None:
    for match in re.finditer(
        r"qdev_init_gpio_in(?:_named)?\s*\((.*?)\);",
        text,
        re.DOTALL,
    ):
        call = match.group(1)
        if needle and needle.lower() not in call.lower():
            continue
        arguments = split_call_arguments(call)
        if len(arguments) >= 2 and re.fullmatch(r"\w+", arguments[1]):
            return arguments[1]
    return None


def parameter_names(parameters: str) -> list[str]:
    names: list[str] = []
    for parameter in parameters.split(","):
        match = re.findall(
            r"([A-Za-z_]\w*)\s*(?:\[[^]]*\])?\s*$",
            parameter.strip(),
        )
        names.append(match[-1] if match else "")
    return names


def patch_arm_raw_mirror() -> tuple[bool, str]:
    path = Path("hw/intc/bcm2835_ic.c")
    text = path.read_text(encoding="utf-8")
    handler = gpio_input_handler(text, "gpu")
    if handler is None:
        return False, "GPU input handler was not found"
    span = function_span(text, handler)
    if span is None:
        return False, f"function body for {handler} was not found"
    start, end, parameters = span
    names = parameter_names(parameters)
    if len(names) < 3:
        return False, f"unexpected parameters for {handler}: {parameters}"
    irq_name, level_name = names[-2], names[-1]
    body = text[start:end]
    state_match = re.search(r"(\w+)\s*=\s*opaque\s*;", body)
    state_name = state_match.group(1) if state_match else "s"
    output_match = re.search(
        r"qdev_init_gpio_out_named\s*\("
        r"[^,]+,\s*([^,]+),\s*[^,]*GPU[^,]*,",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    output = (
        output_match.group(1).strip()
        if output_match is not None
        else f"{state_name}->gpu_irq_out"
    )
    statement = f"qemu_set_irq({output}[{irq_name}], {level_name});"
    if statement in body:
        return False, "raw GPU level is already mirrored directly"

    assignment = body.find("= opaque")
    if assignment >= 0:
        insert_at = body.find(";", assignment) + 1
    else:
        insert_at = body.find("{") + 1
    insertion = (
        "\n    /* Mirror raw GPU state independently of ARM IRQ enables. */\n"
        f"    {statement}"
    )
    patched = body[:insert_at] + insertion + body[insert_at:]
    path.write_text(text[:start] + patched + text[end:], encoding="utf-8")
    return True, statement


def patch_cpu_irq_latch() -> tuple[bool, str]:
    path = Path("target/vc4/cpu.c")
    text = path.read_text(encoding="utf-8")
    handler = gpio_input_handler(text, "") or "vc4_cpu_set_irq"
    span = function_span(text, handler)
    if span is None:
        return False, f"function body for {handler} was not found"
    start, end, _parameters = span
    body = text[start:end]

    pattern = re.compile(
        r"(else\s*\{[^{}]*?)"
        r"(env->interrupt_status\s*&=\s*~[^;]+;)",
        re.DOTALL,
    )
    match = pattern.search(body)
    if match is None:
        return False, "external-interrupt deassertion clear was not found"
    replacement = (
        match.group(1)
        + "/* Preserve the latched external interrupt until guest handling. */"
    )
    patched = body[: match.start()] + replacement + body[match.end() :]
    path.write_text(text[:start] + patched + text[end:], encoding="utf-8")
    return True, match.group(2)


def patch_vpu_direct_output() -> tuple[bool, str]:
    path = Path("hw/vc4/bcm2835_vc4_intc.c")
    text = path.read_text(encoding="utf-8")
    handler = gpio_input_handler(text, "gpu")
    if handler is None:
        return False, "VPU interrupt input handler was not found"
    span = function_span(text, handler)
    if span is None:
        return False, f"function body for {handler} was not found"
    start, end, parameters = span
    names = parameter_names(parameters)
    if len(names) < 3:
        return False, f"unexpected parameters for {handler}: {parameters}"
    level_name = names[-1]
    calls = re.findall(
        r"qemu_set_irq\s*\((.*?),\s*(.*?)\)\s*;",
        text,
        re.DOTALL,
    )
    if not calls:
        return False, "VPU interrupt-controller output was not found"
    output = calls[0][0].strip()
    body = text[start:end]
    insert_at = body.rfind("}")
    insertion = (
        "\n    /* Diagnostic only: expose any raw input level to the CPU. */\n"
        f"    qemu_set_irq({output}, {level_name});\n"
    )
    patched = body[:insert_at] + insertion + body[insert_at:]
    path.write_text(text[:start] + patched + text[end:], encoding="utf-8")
    return True, output


def frontier_score(result: dict[str, Any] | None) -> int:
    if not result:
        return -1_000_000
    if result.get("signature_seen"):
        return 1_000_000
    samples: list[tuple[int, int]] = []
    for item in result.get("pc_histogram", []):
        try:
            samples.append((int(item["pc"], 0), int(item["count"])))
        except (KeyError, TypeError, ValueError):
            continue
    total = sum(count for _pc, count in samples) or 1
    old_loop = sum(count for pc, count in samples if pc == 0x544)
    maximum_pc = max((pc for pc, _count in samples), default=0)
    escaped_weight = int((1.0 - old_loop / total) * 100_000)
    return (
        escaped_weight
        + min(maximum_pc, 0x20_0000) // 8
        + len(samples) * 100
    )


def restore_sources(originals: dict[Path, bytes]) -> None:
    for path, content in originals.items():
        path.write_bytes(content)


def build_binaries(
    build_dir: Path,
    out_dir: Path,
    tag: str,
) -> int:
    with (out_dir / f"{tag}-build.log").open("w", encoding="utf-8") as log:
        process = run(
            [
                "ninja",
                "-C",
                str(build_dir),
                "qemu-system-aarch64",
                "qemu-system-vc4",
            ],
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    return process.returncode


def run_validation(
    qemu_aarch64: Path,
    qemu_vc4: Path,
    image: Path,
    out_dir: Path,
    tag: str,
) -> dict[str, Any]:
    tests: dict[str, int] = {}
    commands = {
        "preg": [
            "python3",
            "scripts/vc4/preg-mutex-smoke.py",
            "--qemu",
            str(qemu_vc4),
        ],
        "msync": [
            "python3",
            "scripts/vc4/msync-smoke.py",
            "--qemu",
            str(qemu_aarch64),
        ],
    }
    for name, command in commands.items():
        with (out_dir / f"{tag}-{name}.log").open(
            "w", encoding="utf-8"
        ) as log:
            tests[name] = run(
                command,
                check=False,
                stdout=log,
                stderr=subprocess.STDOUT,
            ).returncode

    evidence = out_dir / f"{tag}-evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    with (out_dir / f"{tag}-probe.log").open(
        "w", encoding="utf-8"
    ) as log:
        probe_rc = run(
            [
                "python3",
                "scripts/vc4/lowpc-irq-probe.py",
                "--qemu",
                str(qemu_aarch64),
                "--image",
                str(image),
                "--seconds",
                "20",
                "--interval",
                "0.005",
                "--stable-samples",
                "256",
                "--out-dir",
                str(evidence),
            ],
            check=False,
            stdout=log,
            stderr=subprocess.STDOUT,
        ).returncode
    result_path = evidence / "result.json"
    result = (
        json.loads(result_path.read_text(encoding="utf-8"))
        if result_path.is_file()
        else None
    )
    return {
        "tests": tests,
        "probe_rc": probe_rc,
        "result": result,
        "score": frontier_score(result),
    }


def acceptable(
    candidate: dict[str, Any],
    baseline_score: int,
) -> bool:
    return bool(
        candidate.get("build_rc") == 0
        and candidate.get("production_candidate")
        and all(code == 0 for code in candidate.get("tests", {}).values())
        and candidate.get("score", -1_000_000) > baseline_score + 1_000
    )


def write_markdown(path: Path, report: dict[str, Any]) -> None:
    baseline = report.get("baseline", {})
    lines = [
        "# VC4 targeted interrupt repair evaluation",
        "",
        f"Selected: **{report.get('selected')}**",
        f"Selected candidate: `{report.get('selected_variant')}`",
        f"Baseline score: `{baseline.get('score')}`",
        "",
        "## Candidate matrix",
        "",
    ]
    for candidate in report.get("variants", []):
        result = candidate.get("result") or {}
        lines.append(
            f"- `{candidate.get('name')}`: changed="
            f"`{candidate.get('changed')}`, production="
            f"`{candidate.get('production_candidate')}`, build="
            f"`{candidate.get('build_rc')}`, tests="
            f"`{candidate.get('tests')}`, score="
            f"`{candidate.get('score')}`, signature="
            f"`{result.get('signature_seen')}`, stop="
            f"`{result.get('stop_reason')}`"
        )
    final = report.get("final_validation") or {}
    final_result = final.get("result") or {}
    lines.extend(
        [
            "",
            "## Retained-tree validation",
            "",
            f"- Focused tests: `{final.get('tests')}`",
            f"- Score: `{final.get('score')}`",
            f"- ARM payload signature: "
            f"`{final_result.get('signature_seen')}`",
            f"- Stop reason: `{final_result.get('stop_reason')}`",
            f"- Top PCs: "
            f"`{(final_result.get('pc_histogram') or [])[:10]}`",
            "",
            "The direct VPU-controller output bypass is deliberately never "
            "eligible for retention; it exists only to localize a mask or "
            "register-semantics defect.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def evaluate(args: argparse.Namespace) -> int:
    qemu_aarch64 = args.qemu_aarch64.resolve()
    qemu_vc4 = args.qemu_vc4.resolve()
    image = args.image.resolve()
    for path in (qemu_aarch64, qemu_vc4, image):
        if not path.is_file():
            raise SystemExit(f"required file is absent: {path}")
    build_dir = qemu_aarch64.parent
    args.out_dir.mkdir(parents=True, exist_ok=True)
    originals = {path: path.read_bytes() for path in SOURCE_FILES}

    variants: list[
        tuple[str, Callable[[], tuple[bool, str]], bool]
    ] = [
        ("arm-raw-unconditional", patch_arm_raw_mirror, True),
        ("cpu-irq-latch", patch_cpu_irq_latch, True),
        ("vpu-direct-raw-diagnostic", patch_vpu_direct_output, False),
    ]
    report: dict[str, Any] = {"schema_version": 1, "variants": []}
    selected: tuple[str, Callable[[], tuple[bool, str]]] | None = None
    try:
        restore_sources(originals)
        baseline_build = build_binaries(build_dir, args.out_dir, "baseline")
        baseline = (
            run_validation(
                qemu_aarch64,
                qemu_vc4,
                image,
                args.out_dir,
                "baseline",
            )
            if baseline_build == 0
            else {"score": -1_000_000}
        )
        baseline["build_rc"] = baseline_build
        report["baseline"] = baseline
        baseline_score = int(baseline.get("score", -1_000_000))

        for name, patcher, production_candidate in variants:
            restore_sources(originals)
            changed, detail = patcher()
            entry: dict[str, Any] = {
                "name": name,
                "changed": changed,
                "detail": detail,
                "production_candidate": production_candidate,
            }
            if changed:
                entry["build_rc"] = build_binaries(
                    build_dir,
                    args.out_dir,
                    name,
                )
                if entry["build_rc"] == 0:
                    entry.update(
                        run_validation(
                            qemu_aarch64,
                            qemu_vc4,
                            image,
                            args.out_dir,
                            name,
                        )
                    )
            report["variants"].append(entry)

        viable = [
            candidate
            for candidate in report["variants"]
            if acceptable(candidate, baseline_score)
        ]
        viable.sort(key=lambda item: item.get("score", -1_000_000), reverse=True)
        if viable:
            selected_name = str(viable[0]["name"])
            selected_patcher = next(
                patcher
                for name, patcher, _production in variants
                if name == selected_name
            )
            selected = selected_name, selected_patcher

        restore_sources(originals)
        if selected is not None:
            selected_name, selected_patcher = selected
            changed, detail = selected_patcher()
            if not changed:
                raise RuntimeError(
                    f"selected candidate {selected_name} was not reproducible: "
                    f"{detail}"
                )
            final_build = build_binaries(
                build_dir,
                args.out_dir,
                "selected",
            )
            final = (
                run_validation(
                    qemu_aarch64,
                    qemu_vc4,
                    image,
                    args.out_dir,
                    "selected",
                )
                if final_build == 0
                else {"score": -1_000_000}
            )
            final["build_rc"] = final_build
            report["final_validation"] = final
            selected_matrix = viable[0]
            if not (
                final_build == 0
                and all(code == 0 for code in final.get("tests", {}).values())
                and final.get("score", -1_000_000)
                >= selected_matrix.get("score", -1_000_000)
            ):
                restore_sources(originals)
                selected = None

        report["selected"] = selected is not None
        report["selected_variant"] = selected[0] if selected else None
        report_path = args.out_dir / "result.json"
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        write_markdown(args.out_dir / "result.md", report)
        print(
            json.dumps(
                {
                    "selected": report["selected"],
                    "selected_variant": report["selected_variant"],
                    "baseline_score": report["baseline"].get("score"),
                    "variants": [
                        {
                            "name": item.get("name"),
                            "changed": item.get("changed"),
                            "build_rc": item.get("build_rc"),
                            "tests": item.get("tests"),
                            "score": item.get("score"),
                        }
                        for item in report["variants"]
                    ],
                },
                indent=2,
            )
        )
        return 0
    finally:
        if selected is None:
            restore_sources(originals)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--qemu-aarch64", required=True, type=Path)
    parser.add_argument("--qemu-vc4", required=True, type=Path)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    return parser


def main() -> int:
    return evaluate(build_parser().parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
