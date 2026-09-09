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
#define BCM2835_V3D_MAX_TRIANGLES 64
#define BCM2835_V3D_TRIANGLE_COORDS (BCM2835_V3D_MAX_TRIANGLES * 3)

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

    /* Typed CT0 output consumed by the synchronous CT1 tile renderer. */
    uint32_t triangle_count;
    int32_t triangle_x[BCM2835_V3D_TRIANGLE_COORDS];
    int32_t triangle_y[BCM2835_V3D_TRIANGLE_COORDS];
    uint32_t triangle_color[BCM2835_V3D_MAX_TRIANGLES];

    /* Diagnostic-only suppression for repeated kernel timeout retries. */
    uint32_t last_frontier_pc;
    uint32_t last_frontier_shader_record;
};

#endif /* HW_DISPLAY_BCM2835_V3D_H */
