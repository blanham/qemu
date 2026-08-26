#!/usr/bin/env python3
"""Apply BCM2835 firmware virtual-GPIO buffer support to an exact tree."""

from __future__ import annotations

import argparse
from pathlib import Path


HEADER = Path("include/hw/misc/bcm2835_property.h")
SOURCE = Path("hw/misc/bcm2835_property.c")
SENTINEL = "gpio_virtbuf"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one exact anchor, found {count}")
    return text.replace(old, new, 1)


def patch_header(root: Path) -> None:
    path = root / HEADER
    text = path.read_text(encoding="utf-8")
    if SENTINEL in text:
        raise RuntimeError(f"{HEADER}: virtual-GPIO support is already applied")
    text = replace_once(
        text,
        """    uint32_t exp_gpio_state;\n    char *command_line;\n""",
        """    uint32_t exp_gpio_state;\n    uint32_t gpio_virtbuf;\n    char *command_line;\n""",
        "property state",
    )
    path.write_text(text, encoding="utf-8")


def patch_source(root: Path) -> None:
    path = root / SOURCE
    text = path.read_text(encoding="utf-8")
    if SENTINEL in text:
        raise RuntimeError(f"{SOURCE}: virtual-GPIO support is already applied")

    text = replace_once(
        text,
        """        }\n        case RPI_FWREQ_FRAMEBUFFER_GET_NUM_DISPLAYS:\n""",
        """        }\n        case RPI_FWREQ_FRAMEBUFFER_GET_GPIOVIRTBUF:\n            stl_le_phys(&s->dma_as, value + 12, s->gpio_virtbuf);\n            resplen = sizeof(s->gpio_virtbuf);\n            break;\n        case RPI_FWREQ_FRAMEBUFFER_SET_GPIOVIRTBUF:\n            s->gpio_virtbuf = ldl_le_phys(&s->dma_as, value + 12);\n            /* The firmware returns zero in the request word on success. */\n            stl_le_phys(&s->dma_as, value + 12, 0);\n            resplen = sizeof(s->gpio_virtbuf);\n            break;\n        case RPI_FWREQ_FRAMEBUFFER_GET_NUM_DISPLAYS:\n""",
        "virtual-GPIO property tags",
    )
    text = replace_once(
        text,
        """static const VMStateDescription vmstate_bcm2835_property = {\n    .name = TYPE_BCM2835_PROPERTY,\n    .version_id = 3,\n""",
        """static const VMStateDescription vmstate_bcm2835_property = {\n    .name = TYPE_BCM2835_PROPERTY,\n    .version_id = 4,\n""",
        "property migration version",
    )
    text = replace_once(
        text,
        """        VMSTATE_UINT32_V(exp_gpio_state, BCM2835PropertyState, 3),\n        VMSTATE_BOOL(pending, BCM2835PropertyState),\n""",
        """        VMSTATE_UINT32_V(exp_gpio_state, BCM2835PropertyState, 3),\n        VMSTATE_UINT32_V(gpio_virtbuf, BCM2835PropertyState, 4),\n        VMSTATE_BOOL(pending, BCM2835PropertyState),\n""",
        "virtual-GPIO migration state",
    )
    text = replace_once(
        text,
        """    s->exp_gpio_state = BCM2835_PROPERTY_EXP_GPIO_RESET_STATE;\n}\n""",
        """    s->exp_gpio_state = BCM2835_PROPERTY_EXP_GPIO_RESET_STATE;\n    s->gpio_virtbuf = 0;\n}\n""",
        "virtual-GPIO reset state",
    )
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    patch_header(root)
    patch_source(root)
    print("Applied BCM2835 firmware virtual-GPIO buffer support")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
