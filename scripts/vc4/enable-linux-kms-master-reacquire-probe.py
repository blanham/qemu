#!/usr/bin/env python3
"""Enable explicit VC4 DRM-master handoff and atomic-primary witness."""

from __future__ import annotations

import argparse
from pathlib import Path


MARKERS = (
    "VC4_LINUX_KMS_MODESET_SETCRTC_OK",
    "VC4_LINUX_KMS_MODESET_OK",
    "VC4_LINUX_KMS_PAGEFLIP_OK",
    "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_BUSY_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_READY",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_DUMB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_MAP_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CAPS_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_PRIMARY_PLANE_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CRTC_ACTIVE_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_DUMB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_MAP_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_TEST_ONLY_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_IOCTL_START",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_QUEUED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_EVENT_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_CURRENT_FB_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_VISUAL_READY",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ATOMIC_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_HANDOFF_ORDER_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK",
    "VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT",
)

MARKER_CONTRACT = (
    "static const char vc4_master_reacquire_marker_contract[] "
    "__attribute__((used)) =\n"
    + "".join(f'    "{marker}\\0"\n' for marker in MARKERS[:-1])
    + f'    "{MARKERS[-1]}\\0";\n\n'
)


def patch_source(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    include_line = '#include "linux-kms-master-reacquire-probe.inc.c"\n\n'
    include_anchor = "static int probe_kms_topology(VC4DRMNode *card)\n"
    conflicting_includes = (
        '#include "linux-kms-modeset-probe.inc.c"',
        '#include "linux-kms-pageflip-probe.inc.c"',
        '#include "linux-kms-master-reacquire-probe.inc.c"',
    )
    for conflicting in conflicting_includes:
        if conflicting in source:
            raise SystemExit(
                f"{path}: conflicting KMS witness include is already enabled: "
                f"{conflicting}"
            )
    if source.count(include_anchor) != 1:
        raise SystemExit(
            f"{path}: expected one topology-function anchor, "
            f"found {source.count(include_anchor)}"
        )
    source = source.replace(
        include_anchor,
        include_line + MARKER_CONTRACT + include_anchor,
        1,
    )

    call_anchor = (
        "    if (card.fd >= 0 && card.vc4) {\n"
        "        kms_result = probe_kms_topology(&card);\n"
        "    } else {\n"
    )
    call_replacement = (
        "    if (card.fd >= 0 && card.vc4) {\n"
        "        kms_result = probe_kms_topology(&card);\n"
        "        if (kms_result == 0 &&\n"
        "            vc4_kms_modeset_supervise(card.fd) == 0 &&\n"
        "            vc4_kms_pageflip_supervise(card.fd) == 0) {\n"
        "            (void)vc4_kms_master_reacquire_supervise(card.fd);\n"
        "        }\n"
        "    } else {\n"
    )
    if source.count(call_anchor) != 1:
        raise SystemExit(
            f"{path}: expected one topology-call anchor, "
            f"found {source.count(call_anchor)}"
        )
    source = source.replace(call_anchor, call_replacement, 1)
    path.write_text(source, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "source",
        nargs="?",
        type=Path,
        default=Path("tests/vc4/linux-v3d-modular-init.c"),
    )
    args = parser.parse_args()
    patch_source(args.source)
    print(
        "Enabled explicit VC4 DRM-master handoff, independent modeset, "
        f"legacy page flip, and atomic primary-plane witness in {args.source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
