/*
 * Raspberry Pi BCM2835 HDMI controller
 *
 * This slice models the visible HDMI core and HD register windows needed by
 * the native VC4 KMS driver.  Most registers are retained latches.  FIFO
 * recentering is synchronous from the guest's point of view, so asserting
 * RECENTER immediately raises RECENTER_DONE.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/bcm2835_hdmi.h"
#include "hw/irq.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define HDMI_FIFO_CTL                0x05c
#define HDMI_FIFO_CTL_RECENTER       BIT(6)
#define HDMI_FIFO_CTL_RECENTER_DONE  BIT(14)

static unsigned bcm2835_hdmi_index(hwaddr offset)
{
    return offset / sizeof(uint32_t);
}

static bool bcm2835_hdmi_valid_access(hwaddr offset, unsigned size,
                                      hwaddr limit)
{
    return size == 4 && !(offset & 3) && offset < limit;
}

static uint64_t bcm2835_hdmi_core_read(void *opaque, hwaddr offset,
                                       unsigned size)
{
    BCM2835HDMIState *s = opaque;

    if (!bcm2835_hdmi_valid_access(offset, size,
                                   BCM2835_HDMI_CORE_MMIO_SIZE)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": invalid core read at 0x%"
                      HWADDR_PRIx " (size %u)\n", offset, size);
        return 0;
    }

    return s->core_regs[bcm2835_hdmi_index(offset)];
}

static void bcm2835_hdmi_core_write(void *opaque, hwaddr offset,
                                    uint64_t value, unsigned size)
{
    BCM2835HDMIState *s = opaque;
    uint32_t val = value;

    if (!bcm2835_hdmi_valid_access(offset, size,
                                   BCM2835_HDMI_CORE_MMIO_SIZE)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": invalid core write at 0x%"
                      HWADDR_PRIx " (size %u)\n", offset, size);
        return;
    }

    if (offset == HDMI_FIFO_CTL) {
        /* DONE is hardware-owned and follows the RECENTER command. */
        val &= ~HDMI_FIFO_CTL_RECENTER_DONE;
        if (val & HDMI_FIFO_CTL_RECENTER) {
            val |= HDMI_FIFO_CTL_RECENTER_DONE;
        }
    }

    s->core_regs[bcm2835_hdmi_index(offset)] = val;
}

static uint64_t bcm2835_hdmi_hd_read(void *opaque, hwaddr offset,
                                     unsigned size)
{
    BCM2835HDMIState *s = opaque;

    if (!bcm2835_hdmi_valid_access(offset, size,
                                   BCM2835_HDMI_HD_MMIO_SIZE)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": invalid HD read at 0x%"
                      HWADDR_PRIx " (size %u)\n", offset, size);
        return 0;
    }

    return s->hd_regs[bcm2835_hdmi_index(offset)];
}

static void bcm2835_hdmi_hd_write(void *opaque, hwaddr offset,
                                  uint64_t value, unsigned size)
{
    BCM2835HDMIState *s = opaque;

    if (!bcm2835_hdmi_valid_access(offset, size,
                                   BCM2835_HDMI_HD_MMIO_SIZE)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": invalid HD write at 0x%"
                      HWADDR_PRIx " (size %u)\n", offset, size);
        return;
    }

    s->hd_regs[bcm2835_hdmi_index(offset)] = (uint32_t)value;
}

static const MemoryRegionOps bcm2835_hdmi_core_ops = {
    .read = bcm2835_hdmi_core_read,
    .write = bcm2835_hdmi_core_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static const MemoryRegionOps bcm2835_hdmi_hd_ops = {
    .read = bcm2835_hdmi_hd_read,
    .write = bcm2835_hdmi_hd_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static void bcm2835_hdmi_reset(DeviceState *dev)
{
    BCM2835HDMIState *s = BCM2835_HDMI(dev);
    unsigned index;

    memset(s->core_regs, 0, sizeof(s->core_regs));
    memset(s->hd_regs, 0, sizeof(s->hd_regs));
    for (index = 0; index < BCM2835_HDMI_IRQ_COUNT; index++) {
        qemu_set_irq(s->irq[index], 0);
    }
}

static int bcm2835_hdmi_post_load(void *opaque, int version_id)
{
    BCM2835HDMIState *s = opaque;
    unsigned index;

    (void)version_id;
    for (index = 0; index < BCM2835_HDMI_IRQ_COUNT; index++) {
        qemu_set_irq(s->irq[index], 0);
    }
    return 0;
}

static const VMStateDescription vmstate_bcm2835_hdmi = {
    .name = TYPE_BCM2835_HDMI,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2835_hdmi_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(core_regs, BCM2835HDMIState,
                             BCM2835_HDMI_CORE_REG_WORDS),
        VMSTATE_UINT32_ARRAY(hd_regs, BCM2835HDMIState,
                             BCM2835_HDMI_HD_REG_WORDS),
        VMSTATE_END_OF_LIST()
    },
};

static void bcm2835_hdmi_init(Object *obj)
{
    BCM2835HDMIState *s = BCM2835_HDMI(obj);
    unsigned index;

    memory_region_init_io(&s->core_iomem, obj, &bcm2835_hdmi_core_ops, s,
                          TYPE_BCM2835_HDMI "-core",
                          BCM2835_HDMI_CORE_MMIO_SIZE);
    memory_region_init_io(&s->hd_iomem, obj, &bcm2835_hdmi_hd_ops, s,
                          TYPE_BCM2835_HDMI "-hd",
                          BCM2835_HDMI_HD_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->core_iomem);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->hd_iomem);
    for (index = 0; index < BCM2835_HDMI_IRQ_COUNT; index++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[index]);
    }
}

static void bcm2835_hdmi_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, bcm2835_hdmi_reset);
    dc->vmsd = &vmstate_bcm2835_hdmi;
    dc->desc = "BCM2835 HDMI controller";
}

static const TypeInfo bcm2835_hdmi_info = {
    .name = TYPE_BCM2835_HDMI,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835HDMIState),
    .instance_init = bcm2835_hdmi_init,
    .class_init = bcm2835_hdmi_class_init,
};

static void bcm2835_hdmi_register_types(void)
{
    type_register_static(&bcm2835_hdmi_info);
}

type_init(bcm2835_hdmi_register_types)
