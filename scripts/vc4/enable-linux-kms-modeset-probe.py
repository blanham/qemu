#!/usr/bin/env python3
"""Enable the inherited-master VC4 native-modeset witness in a source tree."""

from __future__ import annotations

import argparse
from pathlib import Path


def patch_source(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    include_line = '#include "linux-kms-modeset-probe.inc.c"\n\n'
    include_anchor = "static int probe_kms_topology(VC4DRMNode *card)\n"
    if include_line in source:
        raise SystemExit(f"{path}: modeset include is already enabled")
    if source.count(include_anchor) != 1:
        raise SystemExit(
            f"{path}: expected one topology-function anchor, "
            f"found {source.count(include_anchor)}"
        )
    source = source.replace(
        include_anchor,
        include_line + include_anchor,
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
        "        if (kms_result == 0) {\n"
        "            (void)vc4_kms_modeset_supervise(card.fd);\n"
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
    print(f"Enabled inherited-master VC4 modeset witness in {args.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
