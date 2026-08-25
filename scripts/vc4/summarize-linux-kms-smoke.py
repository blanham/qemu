#!/usr/bin/env python3
"""Exercise the durable Linux KMS frontier classifications."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any


def load_summarizer() -> ModuleType:
    path = Path(__file__).with_name("summarize-linux-kms.py")
    spec = importlib.util.spec_from_file_location("vc4_linux_kms_summary", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def expect(module: ModuleType, expected: str, *,
           result: dict[str, Any],
           outcomes: dict[str, str] | None = None,
           markers: dict[str, bool] | None = None) -> None:
    if outcomes is None:
        outcomes = {
            "dependencies": "success",
            "source": "success",
            "pinned": "success",
            "fixture": "success",
            "dtb": "success",
            "build": "success",
            "regressions": "success",
            "render_regression": "success",
            "kms_runtime": "success",
        }
    if markers is None:
        markers = {marker: True for marker in module.EXPECTED_MARKERS}

    actual = module.classify(
        markers,
        outcomes,
        [],
        [],
        False,
        True,
        [],
        module.visible_scanout(result),
    )
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def visible_result() -> dict[str, Any]:
    return {
        "framebuffer_marker_seen": True,
        "passed": True,
        "screenshot": {
            "available": True,
            "quadrants_match": True,
            "width": 640,
            "height": 480,
            "samples": {
                "red": [255, 0, 0],
                "green": [0, 255, 0],
                "blue": [0, 0, 255],
                "white": [255, 255, 255],
            },
            "matches": {
                "red": True,
                "green": True,
                "blue": True,
                "white": True,
            },
        },
    }


def main() -> int:
    module = load_summarizer()

    expect(module, module.VISIBLE_CLEAR, result=visible_result())

    black = visible_result()
    black["passed"] = False
    black["screenshot"]["quadrants_match"] = False
    black["screenshot"]["matches"] = {
        "red": False,
        "green": False,
        "blue": False,
        "white": False,
    }
    black["screenshot"]["samples"] = {
        "red": [0, 0, 0],
        "green": [0, 0, 0],
        "blue": [0, 0, 0],
        "white": [0, 0, 0],
    }
    expect(module, "vc4-kms-visible-scanout-mismatch", result=black)

    failed_runtime = {
        "dependencies": "success",
        "source": "success",
        "pinned": "success",
        "fixture": "success",
        "dtb": "success",
        "build": "success",
        "regressions": "success",
        "render_regression": "success",
        "kms_runtime": "failure",
    }
    expect(
        module,
        "vc4-kms-visible-scanout-mismatch",
        result=black,
        outcomes=failed_runtime,
    )

    missing_screenshot = visible_result()
    missing_screenshot["passed"] = False
    missing_screenshot["screenshot"] = {"available": False}
    expect(
        module,
        "vc4-kms-screenshot-unavailable",
        result=missing_screenshot,
    )

    missing_framebuffer = visible_result()
    missing_framebuffer["passed"] = False
    missing_framebuffer["framebuffer_marker_seen"] = False
    expect(
        module,
        "vc4-kms-framebuffer-witness-unavailable",
        result=missing_framebuffer,
    )

    build_failed = dict(failed_runtime)
    build_failed["build"] = "failure"
    expect(
        module,
        "workflow-build-failed",
        result=black,
        outcomes=build_failed,
    )

    print("VC4 Linux KMS summary classifications: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
