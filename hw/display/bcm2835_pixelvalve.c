/*
 * Raspberry Pi BCM2835 pixel valve
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/bcm2835_pixelvalve.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define PV_CONTROL             0x00
#define PV_CONTROL_FIFO_CLR    BIT(1)
#define PV_CONTROL_EN          BIT(0)
#define PV_V_CONTROL           0x04
#define PV_VCONTROL_VIDEN      BIT(0)
#define PV_INTEN               0x24
#define PV_INTSTAT             0x28
#define PV_INT_VFP_START       BIT(7)

#define BCM2835_PIXELVALVE_FRAME_PERIOD_NS UINT64_C(16666667)

static bool bcm2835_pixelvalve_active(BCM2835PixelValveState *s)
{
    return (s->regs[PV_CONTROL / 4] & PV_CONTROL_EN) &&
           (s->regs[PV_V_CONTROL / 4] & PV_VCONTROL_VIDEN);
}

static void bcm2835_pixelvalve_update_irq(BCM2835PixelValveState *s)
{
    qemu_set_irq(s->irq,
                 (s->regs[PV_INTEN / 4] &
                  s->regs[PV_INTSTAT / 4]) != 0);
}

static void bcm2835_pixelvalve_schedule_frame(BCM2835PixelValveState *s)
{
    int64_t now = qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL);

    timer_mod(s->frame_timer,
              now + BCM2835_PIXELVALVE_FRAME_PERIOD_NS);
}

static void bcm2835_pixelvalve_update_timer(BCM2835PixelValveState *s)
{
    if (bcm2835_pixelvalve_active(s)) {
        if (!timer_pending(s->frame_timer)) {
            bcm2835_pixelvalve_schedule_frame(s);
        }
    } else {
        timer_del(s->frame_timer);
    }
}

static void bcm2835_pixelvalve_frame(void *opaque)
{
    BCM2835PixelValveState *s = opaque;

    if (!bcm2835_pixelvalve_active(s)) {
        return;
    }

    s->regs[PV_INTSTAT / 4] |= PV_INT_VFP_START;
    bcm2835_pixelvalve_update_irq(s);
    bcm2835_pixelvalve_schedule_frame(s);
}

static uint64_t bcm2835_pixelvalve_read(void *opaque, hwaddr offset,
                                        unsigned size)
{
    BCM2835PixelValveState *s = opaque;

    if (size != 4 || (offset & 3) ||
        offset >= BCM2835_PIXELVALVE_MMIO_SIZE) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: invalid read at 0x%" HWADDR_PRIx
                      " (size %u)\n",
                      TYPE_BCM2835_PIXELVALVE, offset, size);
        return 0;
    }

    return s->regs[offset / 4];
}

static void bcm2835_pixelvalve_write(void *opaque, hwaddr offset,
                                     uint64_t value, unsigned size)
{
    BCM2835PixelValveState *s = opaque;
    uint32_t val = value;

    if (size != 4 || (offset & 3) ||
        offset >= BCM2835_PIXELVALVE_MMIO_SIZE) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: invalid write at 0x%" HWADDR_PRIx
                      " (size %u)\n",
                      TYPE_BCM2835_PIXELVALVE, offset, size);
        return;
    }

    switch (offset) {
    case PV_CONTROL:
        /* FIFO_CLR is a pulse in hardware and reads back as clear. */
        s->regs[offset / 4] = val & ~PV_CONTROL_FIFO_CLR;
        bcm2835_pixelvalve_update_timer(s);
        break;
    case PV_V_CONTROL:
        s->regs[offset / 4] = val;
        bcm2835_pixelvalve_update_timer(s);
        break;
    case PV_INTEN:
        s->regs[offset / 4] = val;
        bcm2835_pixelvalve_update_irq(s);
        break;
    case PV_INTSTAT:
        /* Interrupt status is write-one-to-clear. */
        s->regs[offset / 4] &= ~val;
        bcm2835_pixelvalve_update_irq(s);
        break;
    default:
        s->regs[offset / 4] = val;
        break;
    }
}

static const MemoryRegionOps bcm2835_pixelvalve_ops = {
    .read = bcm2835_pixelvalve_read,
    .write = bcm2835_pixelvalve_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static void bcm2835_pixelvalve_reset(DeviceState *dev)
{
    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);

    memset(s->regs, 0, sizeof(s->regs));
    timer_del(s->frame_timer);
    bcm2835_pixelvalve_update_irq(s);
}

static int bcm2835_pixelvalve_post_load(void *opaque, int version_id)
{
    BCM2835PixelValveState *s = opaque;

    (void)version_id;
    bcm2835_pixelvalve_update_irq(s);
    bcm2835_pixelvalve_update_timer(s);
    return 0;
}

static const VMStateDescription vmstate_bcm2835_pixelvalve = {
    .name = TYPE_BCM2835_PIXELVALVE,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2835_pixelvalve_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2835PixelValveState,
                             BCM2835_PIXELVALVE_REG_COUNT),
        VMSTATE_TIMER_PTR(frame_timer, BCM2835PixelValveState),
        VMSTATE_END_OF_LIST()
    },
};

static void bcm2835_pixelvalve_realize(DeviceState *dev, Error **errp)
{
    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);

    (void)errp;
    s->frame_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                  bcm2835_pixelvalve_frame, s);
    memory_region_init_io(&s->iomem, OBJECT(dev),
                          &bcm2835_pixelvalve_ops, s,
                          TYPE_BCM2835_PIXELVALVE,
                          BCM2835_PIXELVALVE_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(dev), &s->irq);
}

static void bcm2835_pixelvalve_unrealize(DeviceState *dev)
{
    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);

    timer_del(s->frame_timer);
    timer_free(s->frame_timer);
    s->frame_timer = NULL;
}

static void bcm2835_pixelvalve_class_init(ObjectClass *klass,
                                           const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_pixelvalve_realize;
    dc->unrealize = bcm2835_pixelvalve_unrealize;
    dc->vmsd = &vmstate_bcm2835_pixelvalve;
    dc->desc = "BCM2835 pixel valve";
    device_class_set_legacy_reset(dc, bcm2835_pixelvalve_reset);
}

static const TypeInfo bcm2835_pixelvalve_info = {
    .name = TYPE_BCM2835_PIXELVALVE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835PixelValveState),
    .class_init = bcm2835_pixelvalve_class_init,
};

static void bcm2835_pixelvalve_register_types(void)
{
    type_register_static(&bcm2835_pixelvalve_info);
}

type_init(bcm2835_pixelvalve_register_types)
