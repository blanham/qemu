#!/usr/bin/env python3
"""Enable the pinned Mesa VC4 GLES2 probe in a copied Linux init source."""

from __future__ import annotations

import argparse
from pathlib import Path


INCLUDE_LINE = '#include "linux-mesa-gles2-supervisor.inc.c"\n\n'
INCLUDE_ANCHOR = "static int probe_kms_topology(VC4DRMNode *card)\n"
CALL_ANCHOR = """            if (submit_result == 0) {
                mark_submit_success();
            }
"""
CALL_REPLACEMENT = """            if (submit_result == 0) {
                mark_submit_success();
                (void)vc4_linux_mesa_gles2_supervise();
            }
"""


def patch_source(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    if "linux-mesa-gles2-supervisor.inc.c" in source:
        raise SystemExit(f"{path}: Mesa GLES2 supervisor is already enabled")
    if source.count(INCLUDE_ANCHOR) != 1:
        raise SystemExit(
            f"{path}: expected one include anchor, "
            f"found {source.count(INCLUDE_ANCHOR)}"
        )
    if source.count(CALL_ANCHOR) != 1:
        raise SystemExit(
            f"{path}: expected one submit-success anchor, "
            f"found {source.count(CALL_ANCHOR)}"
        )

    source = source.replace(
        INCLUDE_ANCHOR, INCLUDE_LINE + INCLUDE_ANCHOR, 1
    )
    source = source.replace(CALL_ANCHOR, CALL_REPLACEMENT, 1)
    path.write_text(source, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    args = parser.parse_args()
    patch_source(args.source)
    print(f"Enabled Linux Mesa VC4 GLES2 probe in {args.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
