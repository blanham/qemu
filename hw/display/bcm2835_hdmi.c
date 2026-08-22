/*
 * BCM2835 HDMI controller
 *
 * This slice models the visible HDMI and HD register windows needed by the
 * native VC4 KMS driver.  Most registers are latches.  FIFO recentering is a
 * synchronous hardware operation from the guest's point of view, so asserting
 * RECENTER immediately raises RECENTER_DONE.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/bcm2835_hdmi.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define HDMI_FIFO_CTL                0x05c
#define HDMI_FIFO_CTL_RECENTER       BIT(6)
#define HDMI_FIFO_CTL_RECENTER_DONE  BIT(14)

#define REG_INDEX(offset) ((offset) >> 2)

static uint64_t bcm2835_hdmi_core_read(void *opaque, hwaddr addr,
                                       unsigned size)
{
    BCM2835HDMIState *s = opaque;
    unsigned index = REG_INDEX(addr);

    if (index >= BCM2835_HDMI_CORE_REG_WORDS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": bad core read at 0x%"
                      HWADDR_PRIx "\n", addr);
        return 0;
    }

    return s->core_regs[index];
}

static void bcm2835_hdmi_core_write(void *opaque, hwaddr addr,
                                    uint64_t value, unsigned size)
{
    BCM2835HDMIState *s = opaque;
    unsigned index = REG_INDEX(addr);
    uint32_t v = value;

    if (index >= BCM2835_HDMI_CORE_REG_WORDS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": bad core write at 0x%"
                      HWADDR_PRIx "\n", addr);
        return;
    }

    if (addr == HDMI_FIFO_CTL) {
        /*
         * DONE is hardware-owned.  Clearing RECENTER clears the completion;
         * asserting RECENTER completes immediately in this timing model.
         */
        v &= ~HDMI_FIFO_CTL_RECENTER_DONE;
        if (v & HDMI_FIFO_CTL_RECENTER) {
            v |= HDMI_FIFO_CTL_RECENTER_DONE;
        }
    }

    s->core_regs[index] = v;
}

static uint64_t bcm2835_hdmi_hd_read(void *opaque, hwaddr addr,
                                     unsigned size)
{
    BCM2835HDMIState *s = opaque;
    unsigned index = REG_INDEX(addr);

    if (index >= BCM2835_HDMI_HD_REG_WORDS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": bad HD read at 0x%"
                      HWADDR_PRIx "\n", addr);
        return 0;
    }

    return s->hd_regs[index];
}

static void bcm2835_hdmi_hd_write(void *opaque, hwaddr addr,
                                  uint64_t value, unsigned size)
{
    BCM2835HDMIState *s = opaque;
    unsigned index = REG_INDEX(addr);

    if (index >= BCM2835_HDMI_HD_REG_WORDS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HDMI ": bad HD write at 0x%"
                      HWADDR_PRIx "\n", addr);
        return;
    }

    s->hd_regs[index] = value;
}

static const MemoryRegionOps bcm2835_hdmi_core_ops = {
    .read = bcm2835_hdmi_core_read,
    .write = bcm2835_hdmi_core_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static const MemoryRegionOps bcm2835_hdmi_hd_ops = {
    .read = bcm2835_hdmi_hd_read,
    .write = bcm2835_hdmi_hd_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_hdmi_reset(DeviceState *dev)
{
    BCM2835HDMIState *s = BCM2835_HDMI(dev);
    unsigned i;

    memset(s->core_regs, 0, sizeof(s->core_regs));
    memset(s->hd_regs, 0, sizeof(s->hd_regs));
    for (i = 0; i < BCM2835_HDMI_IRQ_COUNT; i++) {
        qemu_set_irq(s->irq[i], 0);
    }
}

static int bcm2835_hdmi_post_load(void *opaque, int version_id)
{
    BCM2835HDMIState *s = opaque;
    unsigned i;

    for (i = 0; i < BCM2835_HDMI_IRQ_COUNT; i++) {
        qemu_set_irq(s->irq[i], 0);
    }
    return 0;
}

static const VMStateDescription bcm2835_hdmi_vmstate = {
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
    }
};

static void bcm2835_hdmi_init(Object *obj)
{
    BCM2835HDMIState *s = BCM2835_HDMI(obj);
    unsigned i;

    memory_region_init_io(&s->core_iomem, obj, &bcm2835_hdmi_core_ops, s,
                          TYPE_BCM2835_HDMI "-core",
                          BCM2835_HDMI_CORE_MMIO_SIZE);
    memory_region_init_io(&s->hd_iomem, obj, &bcm2835_hdmi_hd_ops, s,
                          TYPE_BCM2835_HDMI "-hd",
                          BCM2835_HDMI_HD_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->core_iomem);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->hd_iomem);
    for (i = 0; i < BCM2835_HDMI_IRQ_COUNT; i++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[i]);
    }
}

static void bcm2835_hdmi_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, bcm2835_hdmi_reset);
    dc->vmsd = &bcm2835_hdmi_vmstate;
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
