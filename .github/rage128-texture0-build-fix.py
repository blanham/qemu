#!/usr/bin/env python3

from pathlib import Path

source = Path("hw/display/ati_3d.c")
old = """} ATI3DShadeState;

static uint32_t *ati_3d_reg_ptr(ATIVGAState *s, hwaddr addr)
"""
new = """} ATI3DShadeState;

static uint8_t ati_3d_clamp_channel(float value);

static uint32_t *ati_3d_reg_ptr(ATIVGAState *s, hwaddr addr)
"""
text = source.read_text(encoding="utf-8")

if new in text:
    raise SystemExit(0)
if text.count(old) != 1:
    raise SystemExit(
        "hw/display/ati_3d.c: shade-state declaration anchor is not unique"
    )
source.write_text(text.replace(old, new, 1), encoding="utf-8")
