#!/usr/bin/env python3
"""Materialize BCM2835 USB power and DWC2 PHY sideband registers."""

from __future__ import annotations

from pathlib import Path

POWERMGT_HEADER = Path("include/hw/misc/bcm2835_powermgt.h")
POWERMGT_SOURCE = Path("hw/misc/bcm2835_powermgt.c")
DWC2_HEADER = Path("hw/usb/hcd-dwc2.h")
DWC2_SOURCE = Path("hw/usb/hcd-dwc2.c")

POWERMGT_CONSTANTS = r'''#define R_WDOG 0x24

/*
 * PM_IMAGE and PM_PROC are firmware-visible power-domain registers.
'''
POWERMGT_CONSTANTS_NEW = r'''#define R_WDOG 0x24

/* PM_USB is a one-bit controller-enable latch at 0x7e10005c. */
#define R_USB 0x5c
#define V_USB_CTRLEN (1u << 0)

/*
 * PM_IMAGE and PM_PROC are firmware-visible power-domain registers.
'''

POWERMGT_READ = r'''    case R_WDOG:
        res = s->wdog;
        break;
    case R_IMAGE:
'''
POWERMGT_READ_NEW = r'''    case R_WDOG:
        res = s->wdog;
        break;
    case R_USB:
        res = s->usb;
        break;
    case R_IMAGE:
'''

POWERMGT_WRITE = r'''    case R_WDOG:
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_powermgt_write: WDOG\n");
        s->wdog = value;
        break;
    case R_IMAGE:
'''
POWERMGT_WRITE_NEW = r'''    case R_WDOG:
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_powermgt_write: WDOG\n");
        s->wdog = value;
        break;
    case R_USB:
        s->usb = value & V_USB_CTRLEN;
        break;
    case R_IMAGE:
'''

POWERMGT_VMSTATE_VERSION = r'''    .version_id = 3,
'''
POWERMGT_VMSTATE_VERSION_NEW = r'''    .version_id = 4,
'''

POWERMGT_VMSTATE = r'''        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),
        VMSTATE_UINT32_V(proc, BCM2835PowerMgtState, 2),
'''
POWERMGT_VMSTATE_NEW = r'''        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),
        VMSTATE_UINT32_V(usb, BCM2835PowerMgtState, 4),
        VMSTATE_UINT32_V(proc, BCM2835PowerMgtState, 2),
'''

POWERMGT_RESET = r'''    s->wdog = 0x00000000;
    s->image = V_IMAGE_RESET;
'''
POWERMGT_RESET_NEW = r'''    s->wdog = 0x00000000;
    s->usb = 0x00000000;
    s->image = V_IMAGE_RESET;
'''

POWERMGT_HEADER_FIELD = r'''    uint32_t wdog;
    uint32_t image;
'''
POWERMGT_HEADER_FIELD_NEW = r'''    uint32_t wdog;
    uint32_t usb;
    uint32_t image;
'''

DWC2_HEADER_COUNT = r'''#define DWC2_NB_CHAN        8       /* Number of host channels */
#define DWC2_MAX_XFER_SIZE  65536   /* Max transfer size expected in HCTSIZ */
'''
DWC2_HEADER_COUNT_NEW = r'''#define DWC2_NB_CHAN        8       /* Number of host channels */
#define DWC2_MAX_XFER_SIZE  65536   /* Max transfer size expected in HCTSIZ */
#define DWC2_BCM2835_PHY_REGS 32    /* MDIO-visible PHY register count */
'''

DWC2_HEADER_STATE = r'''    };

    union {
#define DWC2_FSZREG_SIZE    0x04
'''
DWC2_HEADER_STATE_NEW = r'''    };

    /*
     * BCM2835-specific DWC2 PHY sideband registers.  MDIO transactions
     * complete synchronously, matching the other boot-time handshakes in
     * the Raspberry Pi peripheral model.
     */
    uint32_t bcm2835_mdio_csr;
    uint32_t bcm2835_mdio_gen;
    uint32_t bcm2835_vbusdrv;
    uint16_t bcm2835_phy[DWC2_BCM2835_PHY_REGS];

    union {
#define DWC2_FSZREG_SIZE    0x04
'''

DWC2_CONSTANTS = r'''#define USB_FRMINTVL    12000

/* nifty macros from Arnon's EHCI version  */
'''
DWC2_CONSTANTS_NEW = r'''#define USB_FRMINTVL    12000

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

/* nifty macros from Arnon's EHCI version  */
'''

DWC2_HELPER_ANCHOR = r'''static uint64_t dwc2_glbreg_read(void *ptr, hwaddr addr, int index,
                                 unsigned size)
'''
DWC2_HELPER_BLOCK = r'''static void dwc2_bcm2835_mdio_command(DWC2State *s,
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

static uint64_t dwc2_glbreg_read(void *ptr, hwaddr addr, int index,
                                 unsigned size)
'''

DWC2_READ_BODY = r'''{
    DWC2State *s = ptr;
    uint32_t val;

    if (addr > GINTSTS2) {
'''
DWC2_READ_BODY_NEW = r'''{
    DWC2State *s = ptr;
    uint32_t val;

    switch (addr) {
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

    if (addr > GINTSTS2) {
'''

DWC2_WRITE_BODY = r'''{
    DWC2State *s = ptr;
    uint64_t orig = val;
    uint32_t *mmio;
    uint32_t old;
    int iflg = 0;

    if (addr > GINTSTS2) {
'''
DWC2_WRITE_BODY_NEW = r'''{
    DWC2State *s = ptr;
    uint64_t orig = val;
    uint32_t *mmio;
    uint32_t old;
    int iflg = 0;

    switch (addr) {
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

    if (addr > GINTSTS2) {
'''

DWC2_RESET = r'''    s->gintmsk2 = 0;
    s->gintsts2 = 0;

    s->hptxfsiz = 500 << FIFOSIZE_DEPTH_SHIFT;
'''
DWC2_RESET_NEW = r'''    s->gintmsk2 = 0;
    s->gintsts2 = 0;

    s->bcm2835_mdio_csr = 0;
    s->bcm2835_mdio_gen = 0;
    s->bcm2835_vbusdrv = 0;
    memset(s->bcm2835_phy, 0, sizeof(s->bcm2835_phy));

    s->hptxfsiz = 500 << FIFOSIZE_DEPTH_SHIFT;
'''

DWC2_VMSTATE_VERSION = r'''    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(glbreg, DWC2State,
'''
DWC2_VMSTATE_VERSION_NEW = r'''    .version_id = 2,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(glbreg, DWC2State,
'''

DWC2_VMSTATE_FIELDS = r'''        VMSTATE_UINT32_ARRAY(glbreg, DWC2State,
                             DWC2_GLBREG_SIZE / sizeof(uint32_t)),
        VMSTATE_UINT32_ARRAY(fszreg, DWC2State,
'''
DWC2_VMSTATE_FIELDS_NEW = r'''        VMSTATE_UINT32_ARRAY(glbreg, DWC2State,
                             DWC2_GLBREG_SIZE / sizeof(uint32_t)),
        VMSTATE_UINT32_V(bcm2835_mdio_csr, DWC2State, 2),
        VMSTATE_UINT32_V(bcm2835_mdio_gen, DWC2State, 2),
        VMSTATE_UINT32_V(bcm2835_vbusdrv, DWC2State, 2),
        VMSTATE_UINT16_ARRAY_V(bcm2835_phy, DWC2State,
                               DWC2_BCM2835_PHY_REGS, 2),
        VMSTATE_UINT32_ARRAY(fszreg, DWC2State,
'''


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    new_count = text.count(new)
    if new_count == 1:
        return text, False
    if new_count > 1:
        raise RuntimeError(
            f"expected one materialized {label}, found {new_count}"
        )

    old_count = text.count(old)
    if old_count == 1:
        return text.replace(old, new), True
    raise RuntimeError(f"expected one {label}, found {old_count}")


def update(path: Path, replacements: tuple[tuple[str, str, str], ...]) -> bool:
    text = path.read_text(encoding="utf-8")
    changed = False

    for old, new, label in replacements:
        text, did_change = replace_once(text, old, new, label)
        changed |= did_change

    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def main() -> int:
    changed = False

    changed |= update(
        POWERMGT_HEADER,
        ((POWERMGT_HEADER_FIELD, POWERMGT_HEADER_FIELD_NEW,
          "PM_USB state field"),),
    )
    changed |= update(
        POWERMGT_SOURCE,
        (
            (POWERMGT_CONSTANTS, POWERMGT_CONSTANTS_NEW,
             "PM_USB constants"),
            (POWERMGT_READ, POWERMGT_READ_NEW, "PM_USB read case"),
            (POWERMGT_WRITE, POWERMGT_WRITE_NEW, "PM_USB write case"),
            (POWERMGT_VMSTATE_VERSION, POWERMGT_VMSTATE_VERSION_NEW,
             "power-manager VMState version"),
            (POWERMGT_VMSTATE, POWERMGT_VMSTATE_NEW,
             "PM_USB VMState field"),
            (POWERMGT_RESET, POWERMGT_RESET_NEW, "PM_USB reset"),
        ),
    )
    changed |= update(
        DWC2_HEADER,
        (
            (DWC2_HEADER_COUNT, DWC2_HEADER_COUNT_NEW,
             "BCM2835 PHY register count"),
            (DWC2_HEADER_STATE, DWC2_HEADER_STATE_NEW,
             "BCM2835 DWC2 state"),
        ),
    )
    changed |= update(
        DWC2_SOURCE,
        (
            (DWC2_CONSTANTS, DWC2_CONSTANTS_NEW,
             "BCM2835 DWC2 constants"),
            (DWC2_HELPER_ANCHOR, DWC2_HELPER_BLOCK,
             "BCM2835 MDIO helper"),
            (DWC2_READ_BODY, DWC2_READ_BODY_NEW,
             "BCM2835 sideband reads"),
            (DWC2_WRITE_BODY, DWC2_WRITE_BODY_NEW,
             "BCM2835 sideband writes"),
            (DWC2_RESET, DWC2_RESET_NEW, "BCM2835 sideband reset"),
            (DWC2_VMSTATE_VERSION, DWC2_VMSTATE_VERSION_NEW,
             "DWC2 VMState version"),
            (DWC2_VMSTATE_FIELDS, DWC2_VMSTATE_FIELDS_NEW,
             "BCM2835 sideband VMState fields"),
        ),
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
