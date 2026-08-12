#!/usr/bin/env python3
"""Materialize the BCM2835 SDRAM controller and PHY boot contract."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one {label} anchor, found {count}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def remove_once(path: Path, old: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one {label} removal, found {count}"
        )
    path.write_text(text.replace(old, ""), encoding="utf-8")


def main() -> int:
    peripherals_h = Path("include/hw/arm/bcm2835_peripherals.h")
    peripherals_c = Path("hw/arm/bcm2835_peripherals.c")
    platform_h = Path("include/hw/raspi/raspi_platform.h")
    meson = Path("hw/misc/meson.build")

    replace_once(
        peripherals_h,
        '#include "hw/misc/bcm2835_powermgt.h"\n',
        '#include "hw/misc/bcm2835_powermgt.h"\n'
        '#include "hw/misc/bcm2835_sdramc.h"\n',
        "SDRAMC include",
    )
    replace_once(
        peripherals_h,
        "    UnimplementedDeviceState sdramc;\n",
        "    BCM2835SdramcState sdramc;\n",
        "SDRAMC state",
    )

    replace_once(
        peripherals_c,
        '    /* CPRMAN clock manager */\n'
        '    object_initialize_child(obj, "cprman", &s->cprman, '
        'TYPE_BCM2835_CPRMAN);\n',
        '    /* CPRMAN clock manager */\n'
        '    object_initialize_child(obj, "cprman", &s->cprman, '
        'TYPE_BCM2835_CPRMAN);\n\n'
        '    /* SDRAM controller plus address/data PHY status windows */\n'
        '    object_initialize_child(obj, "sdramc", &s->sdramc,\n'
        '                            TYPE_BCM2835_SDRAMC);\n',
        "SDRAMC child initialization",
    )

    replace_once(
        peripherals_c,
        '    memory_region_add_subregion(&s->peri_mr, CPRMAN_OFFSET,\n'
        '                sysbus_mmio_get_region('
        'SYS_BUS_DEVICE(&s->cprman), 0));\n'
        '    qdev_connect_clock_in(DEVICE(&s->uart0), "clk",\n'
        '                          qdev_get_clock_out('
        'DEVICE(&s->cprman), "uart-out"));\n\n'
        '    memory_region_add_subregion(&s->peri_mr, '
        'ARMCTRL_IC_OFFSET,\n',
        '    memory_region_add_subregion(&s->peri_mr, CPRMAN_OFFSET,\n'
        '                sysbus_mmio_get_region('
        'SYS_BUS_DEVICE(&s->cprman), 0));\n'
        '    qdev_connect_clock_in(DEVICE(&s->uart0), "clk",\n'
        '                          qdev_get_clock_out('
        'DEVICE(&s->cprman), "uart-out"));\n\n'
        '    /* SDRAM controller, address PHY, and data PHY. */\n'
        '    if (!sysbus_realize(SYS_BUS_DEVICE(&s->sdramc), errp)) {\n'
        '        return;\n'
        '    }\n'
        '    memory_region_add_subregion(\n'
        '        &s->peri_mr, SDRAMC_OFFSET,\n'
        '        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdramc), 0));\n'
        '    memory_region_add_subregion(\n'
        '        &s->peri_mr, SDRAMC_APHY_OFFSET,\n'
        '        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdramc), 1));\n'
        '    memory_region_add_subregion(\n'
        '        &s->peri_mr, SDRAMC_DPHY_OFFSET,\n'
        '        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->sdramc), 2));\n\n'
        '    memory_region_add_subregion(&s->peri_mr, '
        'ARMCTRL_IC_OFFSET,\n',
        "SDRAMC realization",
    )

    remove_once(
        peripherals_c,
        '    create_unimp(s, &s->sdramc, "bcm2835-sdramc", '
        'SDRAMC_OFFSET, 0x100);\n',
        "old SDRAMC stub",
    )

    replace_once(
        platform_h,
        "#define SDRAMC_OFFSET           0xe00000\n",
        "#define SDRAMC_OFFSET           0xe00000\n"
        "#define SDRAMC_APHY_OFFSET      0xe06000\n"
        "#define SDRAMC_DPHY_OFFSET      0xe07000\n",
        "SDRAMC PHY offsets",
    )

    replace_once(
        meson,
        "  'bcm2835_powermgt.c',\n",
        "  'bcm2835_powermgt.c',\n"
        "  'bcm2835_sdramc.c',\n",
        "SDRAMC source",
    )

    print("Materialized BCM2835 SDRAM controller/PHY boot contract.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
