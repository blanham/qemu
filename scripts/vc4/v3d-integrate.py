#!/usr/bin/env python3
"""Materialize the initial BCM2835 V3D device into the Raspberry Pi SoC.

The standalone device and regression are ordinary source files.  This helper
performs the small, exact integration edits under a full dual-frontend build
and is intentionally idempotent so CI can rerun it safely.
"""

from __future__ import annotations

from pathlib import Path


V3D_SOURCE = Path("hw/display/bcm2835_v3d.c")
DISPLAY_MESON = Path("hw/display/meson.build")
PERIPHERALS_HEADER = Path("include/hw/arm/bcm2835_peripherals.h")
PERIPHERALS_SOURCE = Path("hw/arm/bcm2835_peripherals.c")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


def update_v3d_source() -> None:
    text = V3D_SOURCE.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '#include "qemu/osdep.h"\n',
        '#include "qemu/osdep.h"\n#include "qemu/units.h"\n',
        "V3D unit definitions",
    )
    V3D_SOURCE.write_text(text, encoding="utf-8")


def update_meson() -> None:
    text = DISPLAY_MESON.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "system_ss.add(when: 'CONFIG_RASPI', "
        "if_true: files('bcm2835_fb.c'))\n",
        "system_ss.add(when: 'CONFIG_RASPI', "
        "if_true: files('bcm2835_fb.c', 'bcm2835_v3d.c'))\n",
        "Raspberry Pi display source list",
    )
    DISPLAY_MESON.write_text(text, encoding="utf-8")


def update_header() -> None:
    text = PERIPHERALS_HEADER.read_text(encoding="utf-8")
    text = replace_once(
        text,
        '#include "hw/display/bcm2835_fb.h"\n',
        '#include "hw/display/bcm2835_fb.h"\n'
        '#include "hw/display/bcm2835_v3d.h"\n',
        "V3D header include",
    )
    text = replace_once(
        text,
        "    UnimplementedDeviceState v3d;\n",
        "    BCM2835V3DState v3d;\n",
        "V3D child state",
    )
    PERIPHERALS_HEADER.write_text(text, encoding="utf-8")


def update_peripherals() -> None:
    text = PERIPHERALS_SOURCE.read_text(encoding="utf-8")

    init_anchor = (
        "    object_property_add_const_link(OBJECT(&s->fb), \"dma-mr\",\n"
        "                                   OBJECT(&s->gpu_bus_mr));\n\n"
        "    /* OTP */\n"
    )
    init_block = (
        "    object_property_add_const_link(OBJECT(&s->fb), \"dma-mr\",\n"
        "                                   OBJECT(&s->gpu_bus_mr));\n\n"
        "    /* VideoCore IV 3D accelerator */\n"
        "    object_initialize_child(obj, \"v3d\", &s->v3d,\n"
        "                            TYPE_BCM2835_V3D);\n"
        "    object_property_add_const_link(OBJECT(&s->v3d), \"dma-mr\",\n"
        "                                   OBJECT(&s->gpu_bus_mr));\n\n"
        "    /* OTP */\n"
    )
    text = replace_once(
        text,
        init_anchor,
        init_block,
        "V3D child initialization",
    )

    realize_anchor = (
        "    create_unimp(s, &s->txp, \"bcm2835-txp\", "
        "TXP_OFFSET, 0x1000);\n"
    )
    realize_block = (
        "    /* VideoCore IV 3D accelerator */\n"
        "    if (!sysbus_realize(SYS_BUS_DEVICE(&s->v3d), errp)) {\n"
        "        return;\n"
        "    }\n"
        "    memory_region_add_subregion(\n"
        "        &s->peri_mr, V3D_OFFSET,\n"
        "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->v3d), 0));\n"
        "    sysbus_connect_irq(\n"
        "        SYS_BUS_DEVICE(&s->v3d), 0,\n"
        "        qdev_get_gpio_in_named(DEVICE(&s->ic),\n"
        "                               BCM2835_IC_GPU_IRQ,\n"
        "                               INTERRUPT_3D));\n\n"
        "    create_unimp(s, &s->txp, \"bcm2835-txp\", "
        "TXP_OFFSET, 0x1000);\n"
    )
    text = replace_once(
        text,
        realize_anchor,
        realize_block,
        "V3D realization",
    )
    text = replace_once(
        text,
        "    create_unimp(s, &s->v3d, \"bcm2835-v3d\", "
        "V3D_OFFSET, 0x1000);\n",
        "",
        "obsolete V3D placeholder",
    )

    required = (
        'object_initialize_child(obj, "v3d"',
        "TYPE_BCM2835_V3D",
        "&s->peri_mr, V3D_OFFSET",
        "INTERRUPT_3D",
    )
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise RuntimeError(f"V3D integration validation failed: {missing}")
    if 'create_unimp(s, &s->v3d' in text:
        raise RuntimeError("obsolete unimplemented V3D placeholder remains")

    PERIPHERALS_SOURCE.write_text(text, encoding="utf-8")


def main() -> int:
    update_v3d_source()
    update_meson()
    update_header()
    update_peripherals()
    print("BCM2835 V3D integration materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
