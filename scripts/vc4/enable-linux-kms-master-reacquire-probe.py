#!/usr/bin/env python3
"""Enable the independent-drm_file VC4 modeset/page-flip witness."""

from __future__ import annotations

import argparse
from pathlib import Path


MARKER_CONTRACT = (
    "static const char vc4_master_reacquire_marker_contract[] "
    "__attribute__((used)) =\n"
    '    "VC4_LINUX_KMS_MODESET_SETCRTC_OK\\0"\n'
    '    "VC4_LINUX_KMS_MODESET_OK\\0"\n'
    '    "VC4_LINUX_KMS_PAGEFLIP_OK\\0"\n'
    '    "VC4_LINUX_KMS_PAGEFLIP_SUPERVISOR_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_START\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_DROPPED\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_INHERITED_CLOSED\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_OPEN_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_SET_MASTER_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_SELECTION_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_BASELINE_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_DUMB_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_MAP_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_FB_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_START\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_SETCRTC_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_MODESET_CURRENT_FB_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_INDEPENDENT_MODESET_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_ACTIVE_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_IOCTL_START\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_QUEUED\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_EVENT_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_CURRENT_FB_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_VISUAL_READY\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_CHILD_DROPPED\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_ORIGINAL_RESTORED\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_SUPERVISOR_OK\\0"\n'
    '    "VC4_LINUX_KMS_MASTER_REACQUIRE_TIMEOUT\\0";\n\n'
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
        "Enabled independent-drm_file VC4 modeset/page-flip witness "
        f"in {args.source}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
