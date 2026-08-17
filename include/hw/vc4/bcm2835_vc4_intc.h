/*
 * Broadcom BCM283x VideoCore IV vectored interrupt controller
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_VC4_BCM2835_VC4_INTC_H
#define HW_VC4_BCM2835_VC4_INTC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_VC4_INTC "bcm2835-vc4-intc"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835VC4IntcState, BCM2835_VC4_INTC)

#define BCM2835_VC4_INTC_GPU_IRQ "gpu-irq"
#define BCM2835_VC4_INTC_NUM_IRQS 64

struct BCM2835VC4IntcState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    qemu_irq irq;

    uint32_t control;
    uint32_t source[2];
    uint32_t force[2];
    uint32_t mask[8];
    uint32_t vaddr;
    uint32_t wakeup;
    uint32_t profile;

    int16_t active_vector;
    int16_t pending_vector;
    int8_t active_priority;
    int8_t pending_priority;
};

bool bcm2835_vc4_intc_acknowledge(BCM2835VC4IntcState *s,
                                  uint32_t *vector,
                                  uint32_t *vector_base);
void bcm2835_vc4_intc_complete(BCM2835VC4IntcState *s);

uint32_t bcm2835_vc4_intc_vector_base(BCM2835VC4IntcState *s);

#endif
