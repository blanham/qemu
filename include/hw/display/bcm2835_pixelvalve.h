/*
 * BCM2835 pixel valve
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_BCM2835_PIXELVALVE_H
#define HW_DISPLAY_BCM2835_PIXELVALVE_H

#include "hw/core/sysbus.h"
#include "qemu/timer.h"
#include "system/memory.h"
#include "qom/object.h"

#define TYPE_BCM2835_PIXELVALVE "bcm2835-pixelvalve"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835PixelValveState, BCM2835_PIXELVALVE)

#define BCM2835_PIXELVALVE_MMIO_SIZE 0x100
#define BCM2835_PIXELVALVE_REG_WORDS \
    (BCM2835_PIXELVALVE_MMIO_SIZE / sizeof(uint32_t))

struct BCM2835PixelValveState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    qemu_irq irq;
    QEMUTimer *vblank_timer;

    uint32_t regs[BCM2835_PIXELVALVE_REG_WORDS];
};

#endif /* HW_DISPLAY_BCM2835_PIXELVALVE_H */
