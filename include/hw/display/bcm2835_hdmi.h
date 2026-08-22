/*
 * BCM2835 HDMI controller
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_BCM2835_HDMI_H
#define HW_DISPLAY_BCM2835_HDMI_H

#include "hw/core/sysbus.h"
#include "system/memory.h"
#include "qom/object.h"

#define TYPE_BCM2835_HDMI "bcm2835-hdmi"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835HDMIState, BCM2835_HDMI)

#define BCM2835_HDMI_CORE_MMIO_SIZE 0x600
#define BCM2835_HDMI_HD_MMIO_SIZE   0x100
#define BCM2835_HDMI_CORE_REG_WORDS \
    (BCM2835_HDMI_CORE_MMIO_SIZE / sizeof(uint32_t))
#define BCM2835_HDMI_HD_REG_WORDS \
    (BCM2835_HDMI_HD_MMIO_SIZE / sizeof(uint32_t))
#define BCM2835_HDMI_IRQ_COUNT 2

struct BCM2835HDMIState {
    SysBusDevice parent_obj;

    MemoryRegion core_iomem;
    MemoryRegion hd_iomem;
    qemu_irq irq[BCM2835_HDMI_IRQ_COUNT];

    uint32_t core_regs[BCM2835_HDMI_CORE_REG_WORDS];
    uint32_t hd_regs[BCM2835_HDMI_HD_REG_WORDS];
};

#endif /* HW_DISPLAY_BCM2835_HDMI_H */
