/*
 * BCM2835 DBUS firmware control register
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */
#ifndef HW_MISC_BCM2835_DBUS_H
#define HW_MISC_BCM2835_DBUS_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define TYPE_BCM2835_DBUS "bcm2835-dbus"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835DbusState, BCM2835_DBUS)

/* HDMI core begins at DBUS_OFFSET + 0x2000. */
#define BCM2835_DBUS_WINDOW_SIZE 0x2000

struct BCM2835DbusState {
    SysBusDevice parent_obj;

    MemoryRegion iomem;
    uint32_t control;
};

#endif /* HW_MISC_BCM2835_DBUS_H */
