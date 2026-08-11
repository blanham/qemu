#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if old not in text:
        raise SystemExit(f"expected text not found in {path}: {old!r}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace(
    "hw/vc4/bcm2835_vc4_intc.c",
    '#include "hw/vc4/bcm2835_vc4_intc.h"\n',
    '#include "hw/vc4/bcm2835_vc4_intc.h"\n#include "hw/core/irq.h"\n',
)

replace(
    "hw/vc4/raspi3_vpu.c",
    "sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->vpu_intc[i]), 0), 0);",
    "sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->vpu_intc[i]), 0), 1);",
)
