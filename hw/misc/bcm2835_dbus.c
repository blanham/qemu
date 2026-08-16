/*
 * BCM2835 DBUS firmware control register
 *
 * The VideoCore firmware writes the password-protected register at
 * offset 0x100 while bringing the USB block online.  Model the observed
 * interface as a latched low 24-bit control value.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/misc/bcm2835_dbus.h"
#include "migration/vmstate.h"
#include "qemu/log.h"

#define DBUS_CONTROL           0x100
#define DBUS_PASSWORD_MASK     0xff000000u
#define DBUS_PASSWORD          0x5a000000u
#define DBUS_CONTROL_MASK      0x00ffffffu

static uint64_t bcm2835_dbus_read(void *opaque, hwaddr addr,
                                  unsigned size)
{
    BCM2835DbusState *s = BCM2835_DBUS(opaque);

    switch (addr) {
    case DBUS_CONTROL:
        return s->control;
    default:
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_DBUS
                      ": unimplemented read at 0x%04" HWADDR_PRIx
                      "\n", addr);
        return 0;
    }
}

static void bcm2835_dbus_write(void *opaque, hwaddr addr,
                               uint64_t value, unsigned size)
{
    BCM2835DbusState *s = BCM2835_DBUS(opaque);
    uint32_t v = (uint32_t)value;

    switch (addr) {
    case DBUS_CONTROL:
        if ((v & DBUS_PASSWORD_MASK) != DBUS_PASSWORD) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_DBUS
                          ": rejected control write without password"
                          " (value 0x%08x)\n", v);
            return;
        }
        s->control = v & DBUS_CONTROL_MASK;
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_DBUS
                      ": unimplemented write at 0x%04" HWADDR_PRIx
                      " (value 0x%08x)\n", addr, v);
        break;
    }
}

static const MemoryRegionOps bcm2835_dbus_ops = {
    .read = bcm2835_dbus_read,
    .write = bcm2835_dbus_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_dbus_reset(DeviceState *dev)
{
    BCM2835DbusState *s = BCM2835_DBUS(dev);

    s->control = 0;
}

static void bcm2835_dbus_realize(DeviceState *dev, Error **errp)
{
    BCM2835DbusState *s = BCM2835_DBUS(dev);

    memory_region_init_io(&s->iomem, OBJECT(s), &bcm2835_dbus_ops, s,
                          TYPE_BCM2835_DBUS,
                          BCM2835_DBUS_WINDOW_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static const VMStateDescription bcm2835_dbus_vmstate = {
    .name = TYPE_BCM2835_DBUS,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, BCM2835DbusState),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_dbus_class_init(ObjectClass *klass,
                                    const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_dbus_realize;
    device_class_set_legacy_reset(dc, bcm2835_dbus_reset);
    dc->vmsd = &bcm2835_dbus_vmstate;
}

static const TypeInfo bcm2835_dbus_info = {
    .name = TYPE_BCM2835_DBUS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835DbusState),
    .class_init = bcm2835_dbus_class_init,
};

static void bcm2835_dbus_register_types(void)
{
    type_register_static(&bcm2835_dbus_info);
}

type_init(bcm2835_dbus_register_types)
