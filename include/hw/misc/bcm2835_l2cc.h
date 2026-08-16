/*
 * BCM2835 VideoCore L2 cache-control block
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_MISC_BCM2835_L2CC_H
#define HW_MISC_BCM2835_L2CC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_L2CC "bcm2835-l2cc"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835L2CCState, BCM2835_L2CC)

#define BCM2835_L2CC_MMIO_SIZE 0x1000

struct BCM2835L2CCState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    uint32_t control;
    uint32_t flush_start;
    uint32_t flush_end;
    uint32_t alias_exception;
    uint32_t alias_exception_id;
    uint32_t alias_exception_addr;
    uint32_t rd_hits;
    uint32_t rd_misses;
    uint32_t wr_hits;
    uint32_t wr_misses;
    uint32_t wr_backs;
    uint32_t in_flight;
    uint32_t stalls;
    uint32_t tag_stalls;
    uint32_t sd_stalls;
};

#endif /* HW_MISC_BCM2835_L2CC_H */
