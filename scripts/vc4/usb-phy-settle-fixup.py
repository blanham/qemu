#!/usr/bin/env python3
"""Materialize the BCM2835 USB PHY calibration-settle indication."""

from __future__ import annotations

from pathlib import Path

DWC2_HEADER = Path("hw/usb/hcd-dwc2.h")
DWC2_SOURCE = Path("hw/usb/hcd-dwc2.c")

HEADER_FIELD = "    bool bcm2835_phy_settle_pending;\n"

CONSTANTS_ANCHOR = "#define BCM2835_MDIO_READ        0x60020000\n"
CONSTANTS_BLOCK = r'''#define BCM2835_PHY_DIVISOR_REG   0x17
#define BCM2835_PHY_STATUS_REG    0x1b
#define BCM2835_PHY_SETTLE        (1u << 7)
'''

OLD_COMMAND_CASES = r'''    case BCM2835_MDIO_WRITE:
        s->bcm2835_phy[reg] = value & 0xffff;
        s->bcm2835_mdio_csr =
            (s->bcm2835_mdio_csr & BCM2835_GMDIO_CTRL_MASK) |
            s->bcm2835_phy[reg];
        break;
    case BCM2835_MDIO_READ:
        s->bcm2835_mdio_csr =
            (s->bcm2835_mdio_csr & BCM2835_GMDIO_CTRL_MASK) |
            s->bcm2835_phy[reg];
        break;
'''

NEW_COMMAND_CASES = r'''    case BCM2835_MDIO_WRITE:
        s->bcm2835_phy[reg] = value & 0xffff;
        if (reg == BCM2835_PHY_DIVISOR_REG) {
            /* Writing the PHY divisor starts a short settle sequence. */
            s->bcm2835_phy_settle_pending = true;
        }
        s->bcm2835_mdio_csr =
            (s->bcm2835_mdio_csr & BCM2835_GMDIO_CTRL_MASK) |
            s->bcm2835_phy[reg];
        break;
    case BCM2835_MDIO_READ: {
        uint16_t phy = s->bcm2835_phy[reg];

        /*
         * Firmware generations disagree on which edge of bit 7 denotes
         * completion: the pinned stock image waits for it to become set,
         * while the open firmware waits for it to clear.  Model the observed
         * settle pulse: the first status read reports the edge and consumes
         * it; a subsequent read observes the settled level.
         */
        if (reg == BCM2835_PHY_STATUS_REG) {
            phy &= ~BCM2835_PHY_SETTLE;
            if (s->bcm2835_phy_settle_pending) {
                phy |= BCM2835_PHY_SETTLE;
                s->bcm2835_phy_settle_pending = false;
            }
        }
        s->bcm2835_mdio_csr =
            (s->bcm2835_mdio_csr & BCM2835_GMDIO_CTRL_MASK) | phy;
        break;
    }
'''

RESET_ANCHOR = r'''    memset(s->bcm2835_phy, 0, sizeof(s->bcm2835_phy));
'''
RESET_BLOCK = RESET_ANCHOR + r'''    s->bcm2835_phy_settle_pending = false;
'''

OLD_VMSTATE_VERSION = r'''const VMStateDescription vmstate_dwc2_state = {
    .name = "dwc2",
    .version_id = 2,
'''
NEW_VMSTATE_VERSION = r'''const VMStateDescription vmstate_dwc2_state = {
    .name = "dwc2",
    .version_id = 3,
'''

VMSTATE_ANCHOR = r'''        VMSTATE_UINT16_ARRAY_V(bcm2835_phy, DWC2State,
                               DWC2_BCM2835_PHY_REGS, 2),
'''
VMSTATE_BLOCK = (
    VMSTATE_ANCHOR
    + r'''        VMSTATE_BOOL_V(bcm2835_phy_settle_pending, DWC2State, 3),
'''
)


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    """Replace one exact anchor, or accept one already-materialized form."""
    new_count = text.count(new)
    if new_count == 1:
        return text, False
    if new_count > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {new_count}"
        )

    old_count = text.count(old)
    if old_count != 1:
        raise RuntimeError(f"expected one {label}, found {old_count}")
    return text.replace(old, new), True


def insert_after(text: str, anchor: str, block: str,
                 marker: str, label: str) -> tuple[str, bool]:
    marker_count = text.count(marker)
    if marker_count == 1:
        return text, False
    if marker_count > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {marker_count}"
        )
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(
            f"expected one {label} anchor, found {anchor_count}"
        )
    return text.replace(anchor, anchor + block), True


def write_if_changed(path: Path, original: str, updated: str) -> bool:
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def update_header() -> bool:
    original = DWC2_HEADER.read_text(encoding="utf-8")
    text, _ = insert_after(
        original,
        "    uint16_t bcm2835_phy[DWC2_BCM2835_PHY_REGS];\n",
        HEADER_FIELD,
        HEADER_FIELD,
        "BCM2835 PHY settle state",
    )
    return write_if_changed(DWC2_HEADER, original, text)


def update_source() -> bool:
    original = DWC2_SOURCE.read_text(encoding="utf-8")
    text = original

    text, _ = insert_after(
        text,
        CONSTANTS_ANCHOR,
        CONSTANTS_BLOCK,
        CONSTANTS_BLOCK,
        "BCM2835 PHY settle constants",
    )
    text, _ = replace_once(
        text,
        OLD_COMMAND_CASES,
        NEW_COMMAND_CASES,
        "BCM2835 MDIO settle command handling",
    )
    text, _ = replace_once(
        text,
        RESET_ANCHOR,
        RESET_BLOCK,
        "BCM2835 PHY settle reset",
    )
    text, _ = replace_once(
        text,
        OLD_VMSTATE_VERSION,
        NEW_VMSTATE_VERSION,
        "DWC2 VMState version 3",
    )
    text, _ = replace_once(
        text,
        VMSTATE_ANCHOR,
        VMSTATE_BLOCK,
        "BCM2835 PHY settle VMState field",
    )

    return write_if_changed(DWC2_SOURCE, original, text)


def main() -> int:
    changed = update_header() | update_source()
    if changed:
        print("Materialized BCM2835 USB PHY settle indication.")
    else:
        print("BCM2835 USB PHY settle indication is materialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
