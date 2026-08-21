#!/usr/bin/env python3
"""Offline regression test for the VC4 module dependency resolver."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_resolver():
    path = Path(__file__).with_name("fetch-linux-vc4-modules.py")
    spec = importlib.util.spec_from_file_location("vc4_module_resolver", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load resolver: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    resolver = load_resolver()
    sample = """
kernel/drivers/gpu/drm/drm.ko.xz:
kernel/drivers/gpu/drm/drm_kms_helper.ko.xz: kernel/drivers/gpu/drm/drm.ko.xz
kernel/drivers/gpu/drm/drm_display_helper.ko.xz: kernel/drivers/gpu/drm/drm_kms_helper.ko.xz
kernel/drivers/gpu/drm/vc4/vc4.ko.xz: kernel/drivers/gpu/drm/drm.ko.xz kernel/drivers/gpu/drm/drm_display_helper.ko.xz
"""
    dependencies = resolver.parse_modules_dep(sample)
    target = resolver.find_target(dependencies, "vc4")
    order = resolver.dependency_order(dependencies, target)
    expected = [
        "kernel/drivers/gpu/drm/drm.ko.xz",
        "kernel/drivers/gpu/drm/drm_kms_helper.ko.xz",
        "kernel/drivers/gpu/drm/drm_display_helper.ko.xz",
        "kernel/drivers/gpu/drm/vc4/vc4.ko.xz",
    ]
    if order != expected:
        raise RuntimeError(f"unexpected dependency order: {order!r}")
    if resolver.module_stem(target) != "vc4.ko":
        raise RuntimeError(f"unexpected module stem: {resolver.module_stem(target)!r}")

    cycle = resolver.parse_modules_dep("a.ko: b.ko\nb.ko: a.ko\n")
    try:
        resolver.dependency_order(cycle, "a.ko")
    except RuntimeError as exc:
        if "cycle" not in str(exc):
            raise
    else:
        raise RuntimeError("dependency cycle was not rejected")

    print("VC4 module resolver smoke test passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
