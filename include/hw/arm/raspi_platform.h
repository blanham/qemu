/*
 * bcm2708 aka bcm2835/2836 aka Raspberry Pi/Pi2 SoC platform defines
 *
 * These definitions are derived from those in Raspbian Linux at
 * arch/arm/mach-{bcm2708,bcm2709}/include/mach/platform.h
 * where they carry the following notice:
 *
 * Copyright (C) 2010 Broadcom
 *
 * This program is free software; you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation; either version 2 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program. If not, see <https://www.gnu.org/licenses/>.
 *
 * Various undocumented addresses and names come from Herman Hermitage's VC4
 * documentation:
 * https://github.com/hermanhermitage/videocoreiv/wiki/MMIO-Register-map
 */

#ifndef HW_ARM_RASPI_PLATFORM_H
#define HW_ARM_RASPI_PLATFORM_H

#include "hw/core/boards.h"
#include "hw/arm/boot.h"

/* Registered machine type (matches RPi Foundation bootloader and U-Boot) */
#define MACH_TYPE_BCM2708   3138

#define TYPE_RASPI_BASE_MACHINE MACHINE_TYPE_NAME("raspi-base")
OBJECT_DECLARE_TYPE(RaspiBaseMachineState, RaspiBaseMachineClass,
                    RASPI_BASE_MACHINE)

struct RaspiBaseMachineState {
    /*< private >*/
    MachineState parent_obj;
    /*< public >*/
    struct arm_boot_info binfo;
};

struct RaspiBaseMachineClass {
    /*< private >*/
    MachineClass parent_obj;
    /*< public >*/
    uint32_t board_rev;
};

/* Common functions for raspberry pi machines */
const char *board_soc_type(uint32_t board_rev);
void raspi_machine_init(MachineState *machine);

typedef struct BCM283XBaseState BCM283XBaseState;
void raspi_base_machine_init(MachineState *machine,
                             BCM283XBaseState *soc);

void raspi_machine_class_common_init(MachineClass *mc,
                                     uint32_t board_rev);
uint64_t board_ram_size(uint32_t board_rev);

#include "hw/raspi/raspi_platform.h"

#endif /* HW_ARM_RASPI_PLATFORM_H */
