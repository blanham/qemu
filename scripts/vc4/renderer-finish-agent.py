#!/usr/bin/env python3
"""Bounded VC4 renderer completion agent.

The agent may change only QEMU VC4 implementation and focused unit tests.  It
uses an unmodified, hash-pinned Mesa userspace as the acceptance workload and
publishes to the integration branch only after exact pixel verification.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request


ALLOWED_PATHS = {
    "hw/display/bcm2835_v3d.c",
    "hw/display/vc4_qpu.c",
    "hw/display/vc4_qpu_exec.c",
    "hw/display/vc4_tiling.c",
    "hw/display/vc4_v3d_frontier.c",
    "hw/display/vc4_v3d_pipeline.c",
    "include/hw/display/bcm2835_v3d.h",
    "include/hw/display/vc4_qpu.h",
    "include/hw/display/vc4_qpu_exec.h",
    "include/hw/display/vc4_tiling.h",
    "include/hw/display/vc4_v3d_frontier.h",
    "include/hw/display/vc4_v3d_pipeline.h",
    "tests/unit/test-vc4-qpu.c",
    "tests/unit/test-vc4-qpu-contract.c",
    "tests/unit/test-vc4-tiling.c",
    "tests/unit/test-vc4-v3d-pipeline.c",
}

CONTEXT_PATHS = [
    "hw/display/vc4_qpu_exec.c",
    "include/hw/display/vc4_qpu_exec.h",
    "hw/display/vc4_v3d_pipeline.c",
    "include/hw/display/vc4_v3d_pipeline.h",
    "hw/display/bcm2835_v3d.c",
    "include/hw/display/bcm2835_v3d.h",
    "hw/display/vc4_tiling.c",
    "include/hw/display/vc4_tiling.h",
    "tests/unit/test-vc4-qpu.c",
    "tests/unit/test-vc4-v3d-pipeline.c",
]

ORDERED_MARKERS = [
    "VC4_LINUX_MESA_GLES2_START",
    "VC4_LINUX_MESA_GLES2_EGL_DISPLAY_OK",
    "VC4_LINUX_MESA_GLES2_EGL_INITIALIZE_OK",
    "VC4_LINUX_MESA_GLES2_RENDERER_VC4_OK",
    "VC4_LINUX_MESA_GLES2_PROGRAM_LINK_OK",
    "VC4_LINUX_MESA_GLES2_DRAW_START",
    "VC4_LINUX_MESA_GLES2_DRAW_OK",
    "VC4_LINUX_MESA_GLES2_FINISH_START",
    "VC4_LINUX_MESA_GLES2_FINISH_OK",
    "VC4_LINUX_MESA_GLES2_READPIXELS_START",
    "VC4_LINUX_MESA_GLES2_READPIXELS_OK",
    "VC4_LINUX_MESA_GLES2_PIXELS_OK",
    "VC4_LINUX_MESA_GLES2_OK",
]

FORBIDDEN_ADDED_PATTERNS = [
    re.compile(r"0x100049e0203e303e", re.I),
    re.compile(r"shader[_ -]?hash", re.I),
    re.compile(r"mesa[_ -]?(?:workaround|special|hack|fingerprint)", re.I),
    re.compile(r"host[_ -]?(?:draw|render)[_ -]?bypass", re.I),
    re.compile(r"software[_ -]?raster", re.I),
    re.compile(r"force[_ -]?(?:success|complete|pixel)", re.I),
]


def run(
    command: list[str],
    *,
    cwd: Path,
    log: Path | None = None,
    check: bool = True,
    timeout: int | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", shlex.join(command), flush=True)
    result = subprocess.run(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
    )
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(result.stdout, encoding="utf-8", errors="replace")
    sys.stdout.write(result.stdout)
    sys.stdout.flush()
    if check and result.returncode != 0:
        raise RuntimeError(
            f"command failed with status {result.returncode}: "
            f"{shlex.join(command)}"
        )
    return result


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = run(["git", *arguments], cwd=repo, check=check)
    return result.stdout.strip()


def read_text(path: Path, limit: int = 60000) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n\n/* ... middle truncated ... */\n\n" + text[-half:]


def combined_runtime_text(probe_dir: Path) -> str:
    chunks: list[str] = []
    for name in (
        "probe.stdout",
        "serial.log",
        "qemu.stderr",
        "result.json",
    ):
        path = probe_dir / name
        if path.is_file():
            chunks.append(f"\n===== {name} =====\n")
            chunks.append(read_text(path, 120000))
    return "".join(chunks)


def classify_runtime(probe_dir: Path, returncode: int) -> dict[str, object]:
    text = combined_runtime_text(probe_dir)
    lower = text.lower()
    markers = {marker: marker in text for marker in ORDERED_MARKERS}
    score = sum(index + 1 for index, marker in enumerate(ORDERED_MARKERS)
                if markers[marker])
    pixel_ok_count = text.count("VC4_LINUX_MESA_GLES2_PIXEL_OK")
    score += min(pixel_ok_count, 9) * 4
    software_fallback = any(
        token in lower for token in (
            "llvmpipe", "softpipe", "swrast", "software rasterizer"
        )
    )
    timeout_seen = "VC4_LINUX_MESA_GLES2_TIMEOUT" in text
    exact = (
        markers["VC4_LINUX_MESA_GLES2_READPIXELS_OK"]
        and markers["VC4_LINUX_MESA_GLES2_PIXELS_OK"]
        and markers["VC4_LINUX_MESA_GLES2_OK"]
        and pixel_ok_count >= 3
        and not software_fallback
        and not timeout_seen
        and returncode == 0
    )
    frontier_lines = [
        line for line in text.splitlines()
        if any(token in line.lower() for token in (
            "frontier", "unsupported", "timeout", "fault", "qpu ",
            "tmu", "varying", "pixel_mismatch", "resetting v3d"
        ))
    ]
    return {
        "returncode": returncode,
        "score": score,
        "exact_pixels": exact,
        "pixel_ok_count": pixel_ok_count,
        "software_fallback": software_fallback,
        "timeout_seen": timeout_seen,
        "markers": markers,
        "frontier_tail": frontier_lines[-240:],
        "runtime_tail": text.splitlines()[-300:],
    }


def run_mesa(
    repo: Path,
    evidence: Path,
    label: str,
    qemu: Path,
    kernel: Path,
    dtb: Path,
    initrd: Path,
) -> dict[str, object]:
    probe_dir = evidence / label
    if probe_dir.exists():
        subprocess.run(["rm", "-rf", str(probe_dir)], check=True)
    probe_dir.mkdir(parents=True)
    command = [
        "python3", "scripts/vc4/raspi3-linux-probe.py",
        "--mode", "linux-direct",
        "--qemu", str(qemu),
        "--kernel", str(kernel),
        "--dtb", str(dtb),
        "--initrd", str(initrd),
        "--out-dir", str(probe_dir),
        "--seconds", "150",
        "--sample-interval", "5",
    ]
    result = run(
        command,
        cwd=repo,
        log=probe_dir / "probe.stdout",
        check=False,
        timeout=190,
    )
    (probe_dir / "return-code").write_text(f"{result.returncode}\n")
    status = classify_runtime(probe_dir, result.returncode)
    status["label"] = label
    status["source_sha"] = git(repo, "rev-parse", "HEAD")
    (probe_dir / "classification.json").write_text(
        json.dumps(status, indent=2, sort_keys=True) + "\n"
    )
    return status


def build_and_test(repo: Path, evidence: Path, label: str) -> None:
    log_dir = evidence / label
    log_dir.mkdir(parents=True, exist_ok=True)
    run(
        [
            "ninja", "-C", "build",
            "qemu-system-aarch64", "qemu-system-vc4",
            "tests/unit/test-vc4-qpu",
            "tests/unit/test-vc4-qpu-contract",
            "tests/unit/test-vc4-tiling",
            "tests/unit/test-vc4-v3d-pipeline",
        ],
        cwd=repo,
        log=log_dir / "build.log",
        timeout=900,
    )
    for binary, log_name in (
        ("build/tests/unit/test-vc4-qpu", "qpu.tap"),
        ("build/tests/unit/test-vc4-qpu-contract", "qpu-contract.tap"),
        ("build/tests/unit/test-vc4-tiling", "tiling.tap"),
        ("build/tests/unit/test-vc4-v3d-pipeline", "pipeline.tap"),
    ):
        run(
            [binary, "--tap", "-k"],
            cwd=repo,
            log=log_dir / log_name,
            timeout=180,
        )
    run(
        [
            "python3", "scripts/vc4/v3d-smoke.py",
            "--qemu", "build/qemu-system-aarch64",
        ],
        cwd=repo,
        log=log_dir / "v3d-smoke.log",
        timeout=180,
    )


def model_catalog(token: str) -> list[str]:
    request = urllib.request.Request(
        "https://models.github.ai/catalog/models",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "vc4-renderer-finish-agent",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=45) as response:
            data = json.load(response)
    except Exception:
        return []
    models: list[str] = []
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                identifier = item.get("id") or item.get("name")
                if isinstance(identifier, str):
                    models.append(identifier)
    return models


def choose_model(token: str) -> str:
    requested = os.environ.get("VC4_FINISH_MODEL", "").strip()
    if requested:
        return requested
    available = set(model_catalog(token))
    priorities = [
        "openai/gpt-5.1-codex-max",
        "openai/gpt-5.1-codex",
        "openai/gpt-5",
        "openai/gpt-4.1",
        "anthropic/claude-sonnet-4.5",
        "anthropic/claude-sonnet-4",
    ]
    for model in priorities:
        if not available or model in available:
            return model
    if available:
        coding = sorted(
            model for model in available
            if any(token in model.lower() for token in ("codex", "gpt", "claude"))
        )
        if coding:
            return coding[-1]
    return "openai/gpt-4.1"


def call_model(token: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are repairing an architecture-level QEMU VideoCore IV "
                    "emulator. Return one unified git diff and nothing else. "
                    "Never modify Mesa, firmware, the acceptance probe, workflow "
                    "files, or weaken tests. Do not special-case a shader, command "
                    "stream, guest address, or test fingerprint. Implement the "
                    "general documented hardware behavior exposed by the evidence."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 16000,
    }
    body = json.dumps(payload).encode()
    endpoints = [
        "https://models.github.ai/inference/chat/completions",
        "https://models.inference.ai.azure.com/chat/completions",
    ]
    errors: list[str] = []
    for endpoint in endpoints:
        request = urllib.request.Request(
            endpoint,
            data=body,
            method="POST",
            headers={
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "vc4-renderer-finish-agent",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=240) as response:
                data = json.load(response)
            return str(data["choices"][0]["message"]["content"])
        except Exception as exc:
            errors.append(f"{endpoint}: {type(exc).__name__}: {exc}")
    raise RuntimeError("model inference failed: " + " | ".join(errors))


def extract_diff(response: str) -> str:
    fenced = re.search(r"```(?:diff)?\s*(diff --git .*?)```", response, re.S)
    if fenced:
        response = fenced.group(1)
    start = response.find("diff --git ")
    if start < 0:
        raise RuntimeError("model response did not contain a unified git diff")
    return response[start:].strip() + "\n"


def changed_paths(patch: str) -> set[str]:
    paths = set()
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+?)$", patch, re.M):
        left, right = match.groups()
        if left != right:
            raise RuntimeError(f"renames are not allowed: {left} -> {right}")
        paths.add(left)
    if not paths:
        raise RuntimeError("patch contained no file changes")
    return paths


def validate_patch(patch: str) -> set[str]:
    paths = changed_paths(patch)
    unexpected = sorted(paths - ALLOWED_PATHS)
    if unexpected:
        raise RuntimeError("patch changed forbidden paths: " + ", ".join(unexpected))
    if not any(path.startswith(("hw/", "include/hw/")) for path in paths):
        raise RuntimeError("patch contains tests but no production implementation")
    added = "\n".join(
        line[1:] for line in patch.splitlines()
        if line.startswith("+") and not line.startswith("+++")
    )
    for pattern in FORBIDDEN_ADDED_PATTERNS:
        if pattern.search(added):
            raise RuntimeError(
                f"patch contains forbidden workload-specific pattern: {pattern.pattern}"
            )
    return paths


def source_context(repo: Path) -> str:
    chunks: list[str] = []
    budget = 220000
    for relative in CONTEXT_PATHS:
        path = repo / relative
        if not path.is_file():
            continue
        remaining = budget - sum(len(chunk) for chunk in chunks)
        if remaining <= 2000:
            break
        content = read_text(path, min(50000, remaining - 1000))
        chunks.append(f"\n===== {relative} =====\n{content}\n")
    return "".join(chunks)


def make_prompt(
    repo: Path,
    status: dict[str, object],
    iteration: int,
    rejected: list[str],
) -> str:
    return f"""
Goal: make the pinned, unmodified Mesa VC4 GLES2 acceptance program complete
its glReadPixels resolve and verify all expected pixels under QEMU.

This is iteration {iteration}.  The current measured runtime classification is:
{json.dumps(status, indent=2, sort_keys=True)}

Previously rejected proposal summaries:
{json.dumps(rejected[-8:], indent=2)}

Architectural constraints:
- Mesa 24.0.2 and its generated command lists/QPU programs are untouched.
- No software renderer, host-side draw path, or fabricated completion.
- No shader words, guest addresses, hashes, test names, or command fingerprints
  in production dispatch.
- Unsupported behavior must fail closed; bounds and memory checks stay intact.
- Implement the smallest general VC4 behavior that advances this exact measured
  frontier: varying interpolation/FIFO, QPU semantics, TMU0 setup/load, tiled
  RGBA8888 sampling, or fragment output as actually required by the evidence.
- Add focused architecture-level tests for every new semantic.
- Return only one unified git diff.  Allowed paths are:
{chr(10).join(sorted(ALLOWED_PATHS))}

Current source and focused tests:
{source_context(repo)}
""".strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--kernel", type=Path, required=True)
    parser.add_argument("--dtb", type=Path, required=True)
    parser.add_argument("--initrd", type=Path, required=True)
    parser.add_argument("--max-iterations", type=int, default=4)
    parser.add_argument("--target-branch", required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    evidence = args.evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=True)
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        raise SystemExit("GITHUB_TOKEN is required for bounded model inference")
    model = choose_model(token)
    (evidence / "MODEL").write_text(model + "\n")

    git(repo, "config", "user.name", "VC4 Finish Agent")
    git(repo, "config", "user.email", "vc4-finish@users.noreply.github.com")
    start_head = git(repo, "rev-parse", "HEAD")
    best_commit = start_head
    rejected: list[str] = []

    build_and_test(repo, evidence, "baseline-regressions")
    best_status = run_mesa(
        repo,
        evidence,
        "mesa-baseline",
        repo / "build/qemu-system-aarch64",
        args.kernel,
        args.dtb,
        args.initrd,
    )
    history = [best_status]
    success = bool(best_status["exact_pixels"])

    for iteration in range(1, args.max_iterations + 1):
        if success:
            break
        prompt = make_prompt(repo, best_status, iteration, rejected)
        iteration_dir = evidence / f"proposal-{iteration}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        (iteration_dir / "prompt.txt").write_text(prompt)
        try:
            response = call_model(token, model, prompt)
            (iteration_dir / "response.txt").write_text(response)
            patch = extract_diff(response)
            paths = validate_patch(patch)
            patch_path = iteration_dir / "proposal.patch"
            patch_path.write_text(patch)
            run(["git", "apply", "--check", str(patch_path)], cwd=repo)
            run(["git", "apply", str(patch_path)], cwd=repo)
            run(["git", "diff", "--check"], cwd=repo)
            build_and_test(repo, evidence, f"proposal-{iteration}-regressions")
            candidate_status = run_mesa(
                repo,
                evidence,
                f"mesa-proposal-{iteration}",
                repo / "build/qemu-system-aarch64",
                args.kernel,
                args.dtb,
                args.initrd,
            )
            history.append(candidate_status)
            if (
                candidate_status["exact_pixels"]
                or int(candidate_status["score"]) > int(best_status["score"])
            ):
                git(repo, "add", "--", *sorted(paths))
                git(
                    repo,
                    "commit",
                    "-m",
                    f"hw/display: advance VC4 untouched-Mesa readback tranche {iteration}",
                )
                best_commit = git(repo, "rev-parse", "HEAD")
                best_status = candidate_status
                success = bool(candidate_status["exact_pixels"])
            else:
                rejected.append(
                    f"iteration {iteration}: no measured advance; "
                    f"score {candidate_status['score']} <= {best_status['score']}"
                )
                git(repo, "reset", "--hard", best_commit)
        except Exception as exc:
            rejected.append(f"iteration {iteration}: {type(exc).__name__}: {exc}")
            (iteration_dir / "failure.txt").write_text(rejected[-1] + "\n")
            git(repo, "reset", "--hard", best_commit)

    result = {
        "start_head": start_head,
        "best_commit": best_commit,
        "model": model,
        "success": success,
        "best_status": best_status,
        "history": history,
        "rejected": rejected,
        "unmodified_mesa": True,
        "software_renderer_forbidden": True,
        "host_draw_bypass_forbidden": True,
    }
    status_path = evidence / "VC4_RENDERER_FINISH_AGENT_STATUS.json"
    status_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")

    if success:
        print("VC4_RENDERER_FINISH_EXACT_PIXELS", flush=True)
        return 0
    print("VC4_RENDERER_FINISH_FRONTIER_REMAINS", flush=True)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
