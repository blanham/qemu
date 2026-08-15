#!/usr/bin/env python3
"""Materialize BCM2835 USB power and DWC2 PHY sideband registers."""

from __future__ import annotations

from pathlib import Path

POWERMGT_HEADER = Path("include/hw/misc/bcm2835_powermgt.h")
POWERMGT_SOURCE = Path("hw/misc/bcm2835_powermgt.c")
DWC2_HEADER = Path("hw/usb/hcd-dwc2.h")
DWC2_SOURCE = Path("hw/usb/hcd-dwc2.c")

PM_CONSTANTS = r'''
/* PM_USB is a one-bit controller-enable latch at 0x7e10005c. */
#define R_USB 0x5c
#define V_USB_CTRLEN (1u << 0)
'''

PM_READ_CASE = r'''    case R_USB:
        res = s->usb;
        break;
'''

PM_WRITE_CASE = r'''    case R_USB:
        s->usb = value & V_USB_CTRLEN;
        break;
'''

DWC2_CONSTANTS = r'''
/*
 * Broadcom's DWC2 integration exposes an MDIO control block in the otherwise
 * unused global-register space.  The open VideoCore firmware uses these
 * registers to initialize the USB PHY before touching the standard host core.
 */
#define BCM2835_GMDIOCSR         0x080
#define BCM2835_GMDIOGEN         0x084
#define BCM2835_GVBUSDRV         0x088
#define BCM2835_GMDIO_RSVD       0x08c
#define BCM2835_GMDIO_BUSY       (1u << 31)
#define BCM2835_GMDIO_CTRL_MASK  0x000f0000
#define BCM2835_GVBUSDRV_MASK    0x000fffff
#define BCM2835_MDIO_CMD_MASK    0xf0030000
#define BCM2835_MDIO_WRITE       0x50020000
#define BCM2835_MDIO_READ        0x60020000
'''

DWC2_STATE = r'''    /*
     * BCM2835-specific DWC2 PHY sideband registers.  MDIO transactions
     * complete synchronously, matching the other boot-time handshakes in
     * the Raspberry Pi peripheral model.
     */
    uint32_t bcm2835_mdio_csr;
    uint32_t bcm2835_mdio_gen;
    uint32_t bcm2835_vbusdrv;
    uint16_t bcm2835_phy[DWC2_BCM2835_PHY_REGS];

'''

DWC2_HELPER = r'''static void dwc2_bcm2835_mdio_command(DWC2State *s,
                                          uint32_t value)
{
    unsigned reg = (value >> 18) & 0x1f;

    s->bcm2835_mdio_gen = value;

    switch (value & BCM2835_MDIO_CMD_MASK) {
    case BCM2835_MDIO_WRITE:
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
    default:
        /* 0xffffffff is the required preamble and zero is the errata dummy. */
        break;
    }

    /* The transaction completes before the next guest-visible load. */
    s->bcm2835_mdio_csr &= ~BCM2835_GMDIO_BUSY;
}

'''

DWC2_READ_SWITCH = r'''    switch (addr) {
    case BCM2835_GMDIOCSR:
        return s->bcm2835_mdio_csr;
    case BCM2835_GMDIOGEN:
        return s->bcm2835_mdio_gen;
    case BCM2835_GVBUSDRV:
        return s->bcm2835_vbusdrv;
    case BCM2835_GMDIO_RSVD:
        return 0;
    default:
        break;
    }

'''

DWC2_WRITE_SWITCH = r'''    switch (addr) {
    case BCM2835_GMDIOCSR:
        s->bcm2835_mdio_csr = val & ~BCM2835_GMDIO_BUSY;
        return;
    case BCM2835_GMDIOGEN:
        dwc2_bcm2835_mdio_command(s, val);
        return;
    case BCM2835_GVBUSDRV:
        s->bcm2835_vbusdrv = val & BCM2835_GVBUSDRV_MASK;
        return;
    case BCM2835_GMDIO_RSVD:
        return;
    default:
        break;
    }

'''

DWC2_RESET = r'''    s->bcm2835_mdio_csr = 0;
    s->bcm2835_mdio_gen = 0;
    s->bcm2835_vbusdrv = 0;
    memset(s->bcm2835_phy, 0, sizeof(s->bcm2835_phy));

'''

DWC2_VMSTATE_FIELDS = r'''        VMSTATE_UINT32_V(bcm2835_mdio_csr, DWC2State, 2),
        VMSTATE_UINT32_V(bcm2835_mdio_gen, DWC2State, 2),
        VMSTATE_UINT32_V(bcm2835_vbusdrv, DWC2State, 2),
        VMSTATE_UINT16_ARRAY_V(bcm2835_phy, DWC2State,
                               DWC2_BCM2835_PHY_REGS, 2),
'''


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
    if text.count(marker) == 1:
        return text, False
    if text.count(marker) > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {text.count(marker)}"
        )
    if text.count(anchor) != 1:
        raise RuntimeError(
            f"expected one {label} anchor, found {text.count(anchor)}"
        )
    return text.replace(anchor, anchor + block), True


def insert_before(text: str, anchor: str, block: str,
                  marker: str, label: str) -> tuple[str, bool]:
    if text.count(marker) == 1:
        return text, False
    if text.count(marker) > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {text.count(marker)}"
        )
    if text.count(anchor) != 1:
        raise RuntimeError(
            f"expected one {label} anchor, found {text.count(anchor)}"
        )
    return text.replace(anchor, block + anchor), True


def write_if_changed(path: Path, original: str, updated: str) -> bool:
    if updated == original:
        return False
    path.write_text(updated, encoding="utf-8")
    return True


def update_powermgt_header() -> bool:
    original = POWERMGT_HEADER.read_text(encoding="utf-8")
    text, _ = insert_after(
        original,
        "    uint32_t wdog;\n",
        "    uint32_t usb;\n",
        "    uint32_t usb;\n",
        "PM_USB state field",
    )
    return write_if_changed(POWERMGT_HEADER, original, text)


def update_powermgt_source() -> bool:
    original = POWERMGT_SOURCE.read_text(encoding="utf-8")
    text = original

    text, _ = insert_after(
        text,
        "#define R_WDOG 0x24\n",
        PM_CONSTANTS,
        "#define R_USB 0x5c\n",
        "PM_USB constants",
    )
    text, _ = insert_before(
        text,
        "    case R_IMAGE:\n        res = s->image;\n",
        PM_READ_CASE,
        "    case R_USB:\n        res = s->usb;\n",
        "PM_USB read case",
    )
    text, _ = insert_before(
        text,
        "    case R_IMAGE:\n        bcm2835_powermgt_update_image(s, value);\n",
        PM_WRITE_CASE,
        "    case R_USB:\n        s->usb = value & V_USB_CTRLEN;\n",
        "PM_USB write case",
    )
    text, _ = replace_once(
        text,
        "    .version_id = 3,\n    .minimum_version_id = 1,\n",
        "    .version_id = 4,\n    .minimum_version_id = 1,\n",
        "power-manager VMState version",
    )
    text, _ = insert_after(
        text,
        "        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),\n",
        "        VMSTATE_UINT32_V(usb, BCM2835PowerMgtState, 4),\n",
        "        VMSTATE_UINT32_V(usb, BCM2835PowerMgtState, 4),\n",
        "PM_USB VMState field",
    )
    text, _ = insert_after(
        text,
        "    s->wdog = 0x00000000;\n",
        "    s->usb = 0x00000000;\n",
        "    s->usb = 0x00000000;\n",
        "PM_USB reset",
    )

    return write_if_changed(POWERMGT_SOURCE, original, text)


def update_dwc2_header() -> bool:
    original = DWC2_HEADER.read_text(encoding="utf-8")
    text = original

    text, _ = insert_after(
        text,
        "#define DWC2_MAX_XFER_SIZE  65536   /* Max transfer size expected in HCTSIZ */\n",
        "#define DWC2_BCM2835_PHY_REGS 32    /* MDIO-visible PHY register count */\n",
        "#define DWC2_BCM2835_PHY_REGS 32",
        "BCM2835 PHY register count",
    )
    text, _ = insert_before(
        text,
        "    union {\n#define DWC2_FSZREG_SIZE    0x04\n",
        DWC2_STATE,
        "    uint32_t bcm2835_mdio_csr;\n",
        "BCM2835 DWC2 state",
    )

    return write_if_changed(DWC2_HEADER, original, text)


def update_dwc2_source() -> bool:
    original = DWC2_SOURCE.read_text(encoding="utf-8")
    text = original

    text, _ = insert_after(
        text,
        "#define USB_FRMINTVL    12000\n",
        DWC2_CONSTANTS,
        "#define BCM2835_GMDIOCSR",
        "BCM2835 DWC2 constants",
    )
    text, _ = insert_before(
        text,
        "static uint64_t dwc2_glbreg_read(void *ptr, hwaddr addr, int index,\n"
        "                                 unsigned size)\n",
        DWC2_HELPER,
        "static void dwc2_bcm2835_mdio_command",
        "BCM2835 MDIO helper",
    )
    text, _ = insert_before(
        text,
        "    if (addr > GINTSTS2) {\n"
        "        qemu_log_mask(LOG_GUEST_ERROR, \"%s: Bad offset 0x%\"HWADDR_PRIx\"\\n\",\n"
        "                      __func__, addr);\n"
        "        return 0;\n"
        "    }\n",
        DWC2_READ_SWITCH,
        "    case BCM2835_GMDIOCSR:\n        return s->bcm2835_mdio_csr;\n",
        "BCM2835 sideband reads",
    )
    text, _ = insert_before(
        text,
        "    if (addr > GINTSTS2) {\n"
        "        qemu_log_mask(LOG_GUEST_ERROR, \"%s: Bad offset 0x%\"HWADDR_PRIx\"\\n\",\n"
        "                      __func__, addr);\n"
        "        return;\n"
        "    }\n",
        DWC2_WRITE_SWITCH,
        "    case BCM2835_GMDIOGEN:\n        dwc2_bcm2835_mdio_command(s, val);\n",
        "BCM2835 sideband writes",
    )
    text, _ = insert_after(
        text,
        "    s->gintsts2 = 0;\n",
        "\n" + DWC2_RESET,
        "    s->bcm2835_mdio_csr = 0;\n",
        "BCM2835 sideband reset",
    )
    text, _ = replace_once(
        text,
        "const VMStateDescription vmstate_dwc2_state = {\n"
        "    .name = \"dwc2\",\n"
        "    .version_id = 1,\n",
        "const VMStateDescription vmstate_dwc2_state = {\n"
        "    .name = \"dwc2\",\n"
        "    .version_id = 2,\n",
        "DWC2 VMState version",
    )
    text, _ = insert_after(
        text,
        "        VMSTATE_UINT32_ARRAY(glbreg, DWC2State,\n"
        "                             DWC2_GLBREG_SIZE / sizeof(uint32_t)),\n",
        DWC2_VMSTATE_FIELDS,
        "        VMSTATE_UINT32_V(bcm2835_mdio_csr, DWC2State, 2),\n",
        "BCM2835 sideband VMState fields",
    )

    return write_if_changed(DWC2_SOURCE, original, text)


def main() -> int:
    changed = (
        update_powermgt_header()
        | update_powermgt_source()
        | update_dwc2_header()
        | update_dwc2_source()
    )

    if changed:
        print("Materialized BCM2835 USB power and DWC2 PHY sideband support.")
    else:
        print(
            "BCM2835 USB power and DWC2 PHY sideband support is materialized."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
