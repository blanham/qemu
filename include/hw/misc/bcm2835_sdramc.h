/*
 * BCM2835 SDRAM controller and PHY initialization model
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_MISC_BCM2835_SDRAMC_H
#define HW_MISC_BCM2835_SDRAMC_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_SDRAMC "bcm2835-sdramc"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835SdramcState, BCM2835_SDRAMC)

#define BCM2835_SDRAMC_WINDOW_SIZE 0x100
#define BCM2835_SDRAMC_REG_COUNT \
    (BCM2835_SDRAMC_WINDOW_SIZE / sizeof(uint32_t))

struct BCM2835SdramcState {
    SysBusDevice parent_obj;

    MemoryRegion ctrl_iomem;
    MemoryRegion aphy_iomem;
    MemoryRegion dphy_iomem;

    uint32_t ctrl_regs[BCM2835_SDRAMC_REG_COUNT];
    uint32_t aphy_regs[BCM2835_SDRAMC_REG_COUNT];
    uint32_t dphy_regs[BCM2835_SDRAMC_REG_COUNT];
    uint8_t mode_regs[256];
};

#endif /* HW_MISC_BCM2835_SDRAMC_H */
