#!/usr/bin/env python3
"""Repair the display-handshake gate's generated-source assertion."""

from __future__ import annotations

from pathlib import Path


GATE = Path(".github/workflows/vc4-display-handshake-gate.yml")
OLD = (
    "          grep -Fq TYPE_BCM2835_HVS "
    "include/hw/arm/bcm2835_peripherals.h\n"
)
NEW = (
    "          test -s hw/display/bcm2835_hdmi.c\n"
    "          test -s hw/display/bcm2835_hvs.c\n"
    "          test -s hw/display/bcm2835_pixelvalve.c\n"
    "          test -s include/hw/display/bcm2835_hvs.h\n"
    "          grep -Fq 'hw/display/bcm2835_hvs.h' "
    "include/hw/arm/bcm2835_peripherals.h\n"
)


def main() -> int:
    text = GATE.read_text()
    if OLD in text:
        if text.count(OLD) != 1:
            raise SystemExit("display gate HVS assertion is not unique")
        text = text.replace(OLD, NEW, 1)
    elif NEW not in text:
        raise SystemExit("display gate HVS assertion changed unexpectedly")

    if OLD in text:
        raise SystemExit("stale display gate HVS assertion remains")
    GATE.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
