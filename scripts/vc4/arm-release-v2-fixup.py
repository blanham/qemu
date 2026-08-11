#!/usr/bin/env python3
"""Small include and API hardening pass for the ARM-release fixture."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
p = ROOT / "hw/arm/vc4_arm_release_smoke.c"
text = p.read_text(encoding="utf-8")

needle = '#include "qemu/error-report.h"\n'
addition = needle + '#include "qemu/log.h"\n'
if '#include "qemu/log.h"' not in text:
    if needle not in text:
        raise SystemExit("could not locate qemu/error-report.h include")
    text = text.replace(needle, addition, 1)

p.write_text(text, encoding="utf-8")
