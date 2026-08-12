#!/usr/bin/env python3
"""Materialize the BCM2835 PM_IMAGE power-domain handshake."""

from __future__ import annotations

from pathlib import Path

SOURCE = Path("hw/misc/bcm2835_powermgt.c")
HEADER = Path("include/hw/misc/bcm2835_powermgt.h")

OLD_CONSTANTS = r'''/*
 * PM_PROC is the firmware-visible processor power-domain register.  The bit
 * layout comes from Broadcom's generated cpr_powman.h retained by the open
 * VideoCore firmware projects.
 */
#define R_PROC 0x110
#define V_PROC_CFG_MASK  0x007f0000
#define V_PROC_ENAB      (1u << 12)
#define V_PROC_ARMRSTN   (1u << 6)
#define V_PROC_ISFUNC    (1u << 5)
#define V_PROC_MRDONE    (1u << 4)
#define V_PROC_MEMREP    (1u << 3)
#define V_PROC_ISPOW     (1u << 2)
#define V_PROC_POWOK     (1u << 1)
#define V_PROC_POWUP     (1u << 0)

#define V_PROC_WRITABLE (V_PROC_CFG_MASK | V_PROC_ENAB | V_PROC_ARMRSTN | \
                         V_PROC_ISFUNC | V_PROC_MEMREP | V_PROC_ISPOW | \
                         V_PROC_POWUP)
#define V_PROC_READY (V_PROC_POWUP | V_PROC_POWOK | V_PROC_ISPOW | \
                      V_PROC_MEMREP | V_PROC_MRDONE | V_PROC_ISFUNC | \
                      V_PROC_ARMRSTN)

static void bcm2835_powermgt_update_proc(BCM2835PowerMgtState *s,
                                         uint32_t requested)
{
    bool arm_powered;

    requested &= V_PROC_WRITABLE;

    /*
     * The analogue power controller completes these handshakes
     * asynchronously on hardware.  They complete immediately in this model,
     * while preserving the firmware-visible polling protocol.
     */
    if (requested & V_PROC_POWUP) {
        requested |= V_PROC_POWOK;
    }
    if ((requested & (V_PROC_POWUP | V_PROC_ISPOW | V_PROC_MEMREP)) ==
        (V_PROC_POWUP | V_PROC_ISPOW | V_PROC_MEMREP)) {
        requested |= V_PROC_MRDONE;
    }

    s->proc = requested;
    arm_powered = (s->proc & V_PROC_READY) == V_PROC_READY;
    if (arm_powered != s->arm_powered) {
        s->arm_powered = arm_powered;
        qemu_set_irq(s->arm_power_on, arm_powered);
    }
}
'''

NEW_CONSTANTS = r'''/*
 * PM_IMAGE and PM_PROC are firmware-visible power-domain registers.  Their
 * common handshake layout comes from Broadcom's generated cpr_powman.h
 * retained by the open VideoCore firmware projects.
 */
#define V_DOMAIN_CFG_MASK 0x007f0000
#define V_DOMAIN_ENAB     (1u << 12)
#define V_DOMAIN_ISFUNC   (1u << 5)
#define V_DOMAIN_MRDONE   (1u << 4)
#define V_DOMAIN_MEMREP   (1u << 3)
#define V_DOMAIN_ISPOW    (1u << 2)
#define V_DOMAIN_POWOK    (1u << 1)
#define V_DOMAIN_POWUP    (1u << 0)

#define R_IMAGE 0x108
#define V_IMAGE_RSTN_MASK ((1u << 8) | (1u << 7) | (1u << 6))
#define V_IMAGE_WRITABLE (V_DOMAIN_CFG_MASK | V_DOMAIN_ENAB | \
                          V_IMAGE_RSTN_MASK | V_DOMAIN_ISFUNC | \
                          V_DOMAIN_MEMREP | V_DOMAIN_ISPOW | \
                          V_DOMAIN_POWUP)
#define V_IMAGE_RESET V_DOMAIN_ENAB

#define R_PROC 0x110
#define V_PROC_ARMRSTN (1u << 6)
#define V_PROC_WRITABLE (V_DOMAIN_CFG_MASK | V_DOMAIN_ENAB | \
                         V_PROC_ARMRSTN | V_DOMAIN_ISFUNC | \
                         V_DOMAIN_MEMREP | V_DOMAIN_ISPOW | \
                         V_DOMAIN_POWUP)
#define V_PROC_READY (V_DOMAIN_POWUP | V_DOMAIN_POWOK | V_DOMAIN_ISPOW | \
                      V_DOMAIN_MEMREP | V_DOMAIN_MRDONE | \
                      V_DOMAIN_ISFUNC | V_PROC_ARMRSTN)

static uint32_t bcm2835_powermgt_complete_domain(uint32_t requested,
                                                 uint32_t writable)
{
    requested &= writable;

    /*
     * The analogue power controller completes these handshakes
     * asynchronously on hardware.  They complete immediately in this model,
     * while preserving the firmware-visible polling protocol.
     */
    if (requested & V_DOMAIN_POWUP) {
        requested |= V_DOMAIN_POWOK;
    }
    if ((requested & (V_DOMAIN_POWUP | V_DOMAIN_ISPOW |
                      V_DOMAIN_MEMREP)) ==
        (V_DOMAIN_POWUP | V_DOMAIN_ISPOW | V_DOMAIN_MEMREP)) {
        requested |= V_DOMAIN_MRDONE;
    }

    return requested;
}

static void bcm2835_powermgt_update_image(BCM2835PowerMgtState *s,
                                          uint32_t requested)
{
    s->image = bcm2835_powermgt_complete_domain(requested,
                                                V_IMAGE_WRITABLE);
}

static void bcm2835_powermgt_update_proc(BCM2835PowerMgtState *s,
                                         uint32_t requested)
{
    bool arm_powered;

    s->proc = bcm2835_powermgt_complete_domain(requested,
                                               V_PROC_WRITABLE);
    arm_powered = (s->proc & V_PROC_READY) == V_PROC_READY;
    if (arm_powered != s->arm_powered) {
        s->arm_powered = arm_powered;
        qemu_set_irq(s->arm_power_on, arm_powered);
    }
}
'''

READ_ANCHOR = r'''    case R_PROC:
        res = s->proc;
        break;
'''
READ_REPLACEMENT = r'''    case R_IMAGE:
        res = s->image;
        break;
    case R_PROC:
        res = s->proc;
        break;
'''

WRITE_ANCHOR = r'''    case R_PROC:
        bcm2835_powermgt_update_proc(s, value);
        break;
'''
WRITE_REPLACEMENT = r'''    case R_IMAGE:
        bcm2835_powermgt_update_image(s, value);
        break;
    case R_PROC:
        bcm2835_powermgt_update_proc(s, value);
        break;
'''

VMSTATE_VERSION = r'''    .version_id = 2,
'''
VMSTATE_VERSION_NEW = r'''    .version_id = 3,
'''
VMSTATE_ANCHOR = r'''        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),
        VMSTATE_UINT32_V(proc, BCM2835PowerMgtState, 2),
'''
VMSTATE_REPLACEMENT = r'''        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),
        VMSTATE_UINT32_V(proc, BCM2835PowerMgtState, 2),
        VMSTATE_UINT32_V(image, BCM2835PowerMgtState, 3),
'''

RESET_ANCHOR = r'''    s->wdog = 0x00000000;
    s->proc = 0;
'''
RESET_REPLACEMENT = r'''    s->wdog = 0x00000000;
    s->image = V_IMAGE_RESET;
    s->proc = 0;
'''

HEADER_ANCHOR = r'''    uint32_t wdog;
    uint32_t proc;
'''
HEADER_REPLACEMENT = r'''    uint32_t wdog;
    uint32_t image;
    uint32_t proc;
'''


def replace_once(text: str, old: str, new: str, label: str) -> tuple[str, bool]:
    count = text.count(old)
    if count == 1:
        return text.replace(old, new), True
    if count == 0 and new in text:
        return text, False
    raise RuntimeError(f"expected one {label}, found {count}")


def main() -> int:
    source = SOURCE.read_text(encoding="utf-8")
    header = HEADER.read_text(encoding="utf-8")
    source_changed = False
    header_changed = False

    for old, new, label in (
        (OLD_CONSTANTS, NEW_CONSTANTS, "power-domain block"),
        (READ_ANCHOR, READ_REPLACEMENT, "PM_IMAGE read case"),
        (WRITE_ANCHOR, WRITE_REPLACEMENT, "PM_IMAGE write case"),
        (VMSTATE_VERSION, VMSTATE_VERSION_NEW, "VMState version"),
        (VMSTATE_ANCHOR, VMSTATE_REPLACEMENT, "PM_IMAGE VMState field"),
        (RESET_ANCHOR, RESET_REPLACEMENT, "PM_IMAGE reset"),
    ):
        source, changed = replace_once(source, old, new, label)
        source_changed |= changed

    header, header_changed = replace_once(
        header,
        HEADER_ANCHOR,
        HEADER_REPLACEMENT,
        "PM_IMAGE state field",
    )

    if source_changed:
        SOURCE.write_text(source, encoding="utf-8")
    if header_changed:
        HEADER.write_text(header, encoding="utf-8")

    if source_changed or header_changed:
        print("Materialized BCM2835 PM_IMAGE synchronous power handshake.")
    else:
        print("BCM2835 PM_IMAGE power handshake is already materialized.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
