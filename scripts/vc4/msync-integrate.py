#!/usr/bin/env python3
"""Materialize BCM2835 multicore-sync wiring in the Raspberry Pi SoC.

The device model and state live in normal source files on the feature branch.
This small, idempotent helper limits the CI-generated delta to the two SoC
integration points that must be validated by a full dual-frontend build.
"""

from __future__ import annotations

from pathlib import Path


MESON_PATH = Path("hw/misc/meson.build")
PERIPHERALS_PATH = Path("hw/arm/bcm2835_peripherals.c")


def insert_once(text: str, anchor: str, addition: str, label: str) -> str:
    if addition in text:
        return text
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"{label}: expected one anchor, found {count}"
        )
    return text.replace(anchor, addition + anchor, 1)


def update_meson() -> None:
    text = MESON_PATH.read_text(encoding="utf-8")
    anchor = "  'bcm2835_mphi.c',\n"
    entry = "  'bcm2835_msync.c',\n"
    if entry not in text:
        count = text.count(anchor)
        if count != 1:
            raise RuntimeError(
                f"meson source list: expected one anchor, found {count}"
            )
        text = text.replace(anchor, anchor + entry, 1)
        MESON_PATH.write_text(text, encoding="utf-8")


def update_peripherals() -> None:
    text = PERIPHERALS_PATH.read_text(encoding="utf-8")

    init_anchor = (
        "    /* Interrupt Controller */\n"
        "    object_initialize_child(obj, \"ic\", &s->ic, TYPE_BCM2835_IC);\n"
    )
    init_block = (
        "    /* Multicore synchronization */\n"
        "    object_initialize_child(obj, \"msync\", &s->msync,\n"
        "                            TYPE_BCM2835_MSYNC);\n\n"
    )
    text = insert_once(
        text,
        init_anchor,
        init_block,
        "multicore-sync child initialization",
    )

    realize_anchor = "    /* CPRMAN clock manager */\n"
    realize_block = (
        "    /* Multicore synchronization */\n"
        "    if (!sysbus_realize(SYS_BUS_DEVICE(&s->msync), errp)) {\n"
        "        return;\n"
        "    }\n"
        "    memory_region_add_subregion(\n"
        "        &s->peri_mr, MSYNC_OFFSET,\n"
        "        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->msync), 0));\n"
        "    for (n = 0; n < BCM2835_MSYNC_IRQ_COUNT; n++) {\n"
        "        sysbus_connect_irq(\n"
        "            SYS_BUS_DEVICE(&s->msync), n,\n"
        "            qdev_get_gpio_in_named(DEVICE(&s->ic),\n"
        "                                   BCM2835_IC_GPU_IRQ,\n"
        "                                   INTERRUPT_MULTICORESYNC0 + n));\n"
        "    }\n\n"
    )
    text = insert_once(
        text,
        realize_anchor,
        realize_block,
        "multicore-sync realization",
    )

    required = (
        "object_initialize_child(obj, \"msync\"",
        "&s->peri_mr, MSYNC_OFFSET",
        "INTERRUPT_MULTICORESYNC0 + n",
    )
    missing = [needle for needle in required if needle not in text]
    if missing:
        raise RuntimeError(f"integration validation failed: {missing}")

    PERIPHERALS_PATH.write_text(text, encoding="utf-8")


def main() -> int:
    update_meson()
    update_peripherals()
    print("BCM2835 multicore-sync integration materialized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
