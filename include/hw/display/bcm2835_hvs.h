/*
 * Raspberry Pi BCM2835 Hardware Video Scaler
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_BCM2835_HVS_H
#define HW_DISPLAY_BCM2835_HVS_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_HVS "bcm2835-hvs"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835HVSState, BCM2835_HVS)

#define BCM2835_HVS_MMIO_SIZE 0x6000
#define BCM2835_HVS_REG_WORDS \
    (BCM2835_HVS_MMIO_SIZE / sizeof(uint32_t))

struct BCM2835HVSState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    qemu_irq irq;
    uint32_t regs[BCM2835_HVS_REG_WORDS];

    struct BCM2835FBState *fb;
};

#endif /* HW_DISPLAY_BCM2835_HVS_H */
