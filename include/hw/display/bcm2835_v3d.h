/*
 * BCM2835 VideoCore IV V3D accelerator
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_BCM2835_V3D_H
#define HW_DISPLAY_BCM2835_V3D_H

#include "system/memory.h"
#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_V3D "bcm2835-v3d"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835V3DState, BCM2835_V3D)

#define BCM2835_V3D_MMIO_SIZE 0x1000
#define BCM2835_V3D_REG_WORDS (BCM2835_V3D_MMIO_SIZE / sizeof(uint32_t))

struct BCM2835V3DState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    MemoryRegion *dma_mr;
    AddressSpace dma_as;
    qemu_irq irq;

    /*
     * Most V3D registers are simple latches.  Registers with side effects
     * are interpreted by the MMIO callbacks while retaining their visible
     * state here for migration and debug-register reads.
     */
    uint32_t regs[BCM2835_V3D_REG_WORDS];
};

#endif /* HW_DISPLAY_BCM2835_V3D_H */
