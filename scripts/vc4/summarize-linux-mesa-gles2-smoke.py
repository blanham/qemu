#!/usr/bin/env python3
"""Exercise the VC4 Mesa GLES2 frontier classifier."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


PREREQUISITES = (
    "VC4_LINUX_MODULE_CLOSURE_OK",
    "VC4_LINUX_DRM_SUBMIT_OK",
)


def load_reporter() -> ModuleType:
    path = Path(__file__).with_name(
        "summarize-linux-mesa-gles2.py"
    )
    spec = importlib.util.spec_from_file_location(
        "vc4_mesa_gles2_summary", path
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def serial_for(module: ModuleType, count: int | None = None,
               extra: str = "") -> str:
    markers = module.ORDERED_MARKERS
    if count is not None:
        markers = markers[:count]
    lines = [*PREREQUISITES, *markers]
    if extra:
        lines.append(extra)
    return "\n".join(lines) + "\n"


def evidence_for(module: ModuleType, serial: str,
                 qemu_stderr: str = "") -> dict[str, object]:
    positions = module.marker_positions(serial)
    packet_match = module.UNSUPPORTED_PACKET_RE.search(qemu_stderr)
    packet = None
    if packet_match:
        opcode = int(packet_match.group(1), 16)
        packet = {
            "opcode": opcode,
            "name": module.PACKET_NAMES.get(opcode, "packet"),
            "address": int(packet_match.group(2), 16),
        }
    failure_match = module.FAILURE_RE.search(serial)
    failure = None
    if failure_match:
        failure = {
            "stage": failure_match.group(1),
            "egl_error": int(failure_match.group(2), 16),
            "gl_error": int(failure_match.group(3), 16),
            "errno": int(failure_match.group(4)),
        }
    return {
        "outcomes": {
            name: "success"
            for name in (
                "source", "dependencies", "mesa_root", "modules",
                "initramfs", "dtb", "build", "regressions", "runtime",
            )
        },
        "module_closure_ok":
            "VC4_LINUX_MODULE_CLOSURE_OK" in serial,
        "submit_clear_preserved":
            "VC4_LINUX_DRM_SUBMIT_OK" in serial,
        "exec_failed":
            "VC4_LINUX_MESA_GLES2_EXEC_FAILED" in serial,
        "started": "VC4_LINUX_MESA_GLES2_START" in serial,
        "failure": failure,
        "unsupported_packet": packet,
        "timed_out": "VC4_LINUX_MESA_GLES2_TIMEOUT" in serial,
        "child_exit": None,
        "child_signal": None,
        "marker_order_valid":
            module.present_marker_order_valid(positions),
        "complete": all(value >= 0 for value in positions.values()),
        "last_marker": module.last_marker(positions),
        "next_missing_marker": module.next_missing_marker(positions),
    }


def expect(module: ModuleType, expected: str, serial: str,
           qemu_stderr: str = "") -> None:
    actual = module.classify(
        evidence_for(module, serial, qemu_stderr)
    )
    if actual != expected:
        raise AssertionError(f"expected {expected!r}, got {actual!r}")


def main() -> int:
    module = load_reporter()
    expect(module, module.CLEAR, serial_for(module))
    expect(
        module,
        "vc4-v3d-unsupported-gl-array-primitive-0x21",
        serial_for(module, 14),
        "bcm2835-v3d: packet 0x21 requires binning/QPU execution "
        "at 0x00123456\n",
    )
    expect(
        module,
        "vc4-mesa-gles2-finish-start-timeout",
        serial_for(
            module,
            15,
            "VC4_LINUX_MESA_GLES2_TIMEOUT stage=process-alarm",
        ),
    )
    expect(
        module,
        "vc4-mesa-gles2-egl-initialize",
        serial_for(
            module,
            3,
            "VC4_LINUX_MESA_GLES2_FAILED stage=egl-initialize "
            "egl=0x3001 gl=0x0000 errno=19",
        ),
    )
    expect(
        module,
        "vc4-mesa-gles2-wrong-renderer",
        serial_for(
            module,
            8,
            "VC4_LINUX_MESA_GLES2_FAILED stage=renderer-not-vc4 "
            "egl=0x3000 gl=0x0000 errno=0",
        ),
    )

    reordered = list(module.ORDERED_MARKERS)
    reordered[2], reordered[3] = reordered[3], reordered[2]
    expect(
        module,
        "vc4-mesa-gles2-marker-order-invalid",
        "\n".join((*PREREQUISITES, *reordered)) + "\n",
    )

    print("VC4 Mesa GLES2 frontier classifications: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
