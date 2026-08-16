/*
 * BCM2835 VideoCore L1 cache-control block
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_MISC_BCM2835_L1CC_H
#define HW_MISC_BCM2835_L1CC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_L1CC "bcm2835-l1cc"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835L1CCState, BCM2835_L1CC)

#define BCM2835_L1CC_MMIO_SIZE 0x1000

struct BCM2835L1CCState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    uint32_t ic_control[2];
    uint32_t ic_priority[2];
    uint32_t ic_flush_start[2];
    uint32_t ic_flush_end[2];
    uint32_t d_control;
    uint32_t d_flush_start;
    uint32_t d_flush_end;
    uint32_t d_priority;
};

#endif /* HW_MISC_BCM2835_L1CC_H */
