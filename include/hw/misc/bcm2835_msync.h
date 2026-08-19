/*
 * BCM2835 multicore synchronization block
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_MISC_BCM2835_MSYNC_H
#define HW_MISC_BCM2835_MSYNC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_MSYNC "bcm2835-msync"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835MSyncState, BCM2835_MSYNC)

#define BCM2835_MSYNC_WINDOW_SIZE 0x1000
#define BCM2835_MSYNC_IRQ_COUNT 4

struct BCM2835MSyncState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    qemu_irq irq[BCM2835_MSYNC_IRQ_COUNT];

    uint32_t semaphores;
    uint32_t irq_requests[2];
    uint32_t intercore_pending;
    uint32_t mailboxes[8];
    uint32_t vpu_semaphores;
};

#endif /* HW_MISC_BCM2835_MSYNC_H */
