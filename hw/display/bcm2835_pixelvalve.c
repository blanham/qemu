/*
 * BCM2835 pixel valve
 *
 * This slice provides the register and interrupt contract used by native VC4
 * KMS.  An enabled pixel valve produces a periodic VFP-start interrupt; Linux
 * uses that event for vblank accounting and atomic page-flip completion.
 * Timing is fixed at 60 Hz until the H/V timing registers are promoted into a
 * complete scanout model.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/bcm2835_pixelvalve.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define PV_CONTROL       0x00
#define PV_V_CONTROL     0x0c
#define PV_INTEN         0x24
#define PV_INTSTAT       0x28
#define PV_STAT          0x2c

#define PV_INT_VFP_END       BIT(0)
#define PV_INT_VFP_START     BIT(1)
#define PV_INT_MASK          (PV_INT_VFP_END | PV_INT_VFP_START)
#define PV_VCONTROL_VIDEN    BIT(0)
#define PV_STAT_VIDEN        BIT(0)

#define BCM2835_PIXELVALVE_FRAME_NS INT64_C(16666667)
#define REG_INDEX(offset) ((offset) >> 2)

static void bcm2835_pixelvalve_update_irq(BCM2835PixelValveState *s)
{
    uint32_t pending = s->regs[REG_INDEX(PV_INTSTAT)];
    uint32_t enabled = s->regs[REG_INDEX(PV_INTEN)];

    qemu_set_irq(s->irq, (pending & enabled & PV_INT_MASK) != 0);
}

static void bcm2835_pixelvalve_schedule_vblank(BCM2835PixelValveState *s)
{
    timer_mod(s->vblank_timer,
              qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) +
              BCM2835_PIXELVALVE_FRAME_NS);
}

static void bcm2835_pixelvalve_set_video(BCM2835PixelValveState *s,
                                         bool enabled)
{
    if (enabled) {
        s->regs[REG_INDEX(PV_STAT)] |= PV_STAT_VIDEN;
        bcm2835_pixelvalve_schedule_vblank(s);
    } else {
        timer_del(s->vblank_timer);
        s->regs[REG_INDEX(PV_STAT)] &= ~PV_STAT_VIDEN;
        s->regs[REG_INDEX(PV_INTSTAT)] &= ~PV_INT_MASK;
        bcm2835_pixelvalve_update_irq(s);
    }
}

static void bcm2835_pixelvalve_vblank(void *opaque)
{
    BCM2835PixelValveState *s = opaque;

    if (!(s->regs[REG_INDEX(PV_V_CONTROL)] & PV_VCONTROL_VIDEN)) {
        return;
    }

    s->regs[REG_INDEX(PV_INTSTAT)] |= PV_INT_VFP_START;
    bcm2835_pixelvalve_update_irq(s);
    bcm2835_pixelvalve_schedule_vblank(s);
}

static uint64_t bcm2835_pixelvalve_read(void *opaque, hwaddr addr,
                                        unsigned size)
{
    BCM2835PixelValveState *s = opaque;
    unsigned index = REG_INDEX(addr);

    if (index >= BCM2835_PIXELVALVE_REG_WORDS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_PIXELVALVE ": bad read at 0x%"
                      HWADDR_PRIx "\n", addr);
        return 0;
    }

    return s->regs[index];
}

static void bcm2835_pixelvalve_write(void *opaque, hwaddr addr,
                                     uint64_t value, unsigned size)
{
    BCM2835PixelValveState *s = opaque;
    unsigned index = REG_INDEX(addr);
    uint32_t v = value;

    if (index >= BCM2835_PIXELVALVE_REG_WORDS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_PIXELVALVE ": bad write at 0x%"
                      HWADDR_PRIx "\n", addr);
        return;
    }

    switch (addr) {
    case PV_INTSTAT:
        /* Interrupt status is write-one-to-clear. */
        s->regs[index] &= ~(v & PV_INT_MASK);
        bcm2835_pixelvalve_update_irq(s);
        return;
    case PV_INTEN:
        s->regs[index] = v;
        bcm2835_pixelvalve_update_irq(s);
        return;
    case PV_STAT:
        /* Video-active status is hardware-owned. */
        return;
    case PV_V_CONTROL:
        s->regs[index] = v;
        bcm2835_pixelvalve_set_video(
            s, (v & PV_VCONTROL_VIDEN) != 0);
        return;
    default:
        s->regs[index] = v;
        return;
    }
}

static const MemoryRegionOps bcm2835_pixelvalve_ops = {
    .read = bcm2835_pixelvalve_read,
    .write = bcm2835_pixelvalve_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_pixelvalve_reset(DeviceState *dev)
{
    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(dev);

    timer_del(s->vblank_timer);
    memset(s->regs, 0, sizeof(s->regs));
    bcm2835_pixelvalve_update_irq(s);
}

static int bcm2835_pixelvalve_post_load(void *opaque, int version_id)
{
    BCM2835PixelValveState *s = opaque;
    bool enabled =
        (s->regs[REG_INDEX(PV_V_CONTROL)] & PV_VCONTROL_VIDEN) != 0;

    timer_del(s->vblank_timer);
    if (enabled) {
        s->regs[REG_INDEX(PV_STAT)] |= PV_STAT_VIDEN;
        bcm2835_pixelvalve_schedule_vblank(s);
    } else {
        s->regs[REG_INDEX(PV_STAT)] &= ~PV_STAT_VIDEN;
    }
    bcm2835_pixelvalve_update_irq(s);
    return 0;
}

static const VMStateDescription bcm2835_pixelvalve_vmstate = {
    .name = TYPE_BCM2835_PIXELVALVE,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2835_pixelvalve_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2835PixelValveState,
                             BCM2835_PIXELVALVE_REG_WORDS),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_pixelvalve_init(Object *obj)
{
    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(obj);

    memory_region_init_io(&s->iomem, obj, &bcm2835_pixelvalve_ops, s,
                          TYPE_BCM2835_PIXELVALVE,
                          BCM2835_PIXELVALVE_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
    s->vblank_timer = timer_new_ns(QEMU_CLOCK_VIRTUAL,
                                   bcm2835_pixelvalve_vblank, s);
}

static void bcm2835_pixelvalve_finalize(Object *obj)
{
    BCM2835PixelValveState *s = BCM2835_PIXELVALVE(obj);

    timer_free(s->vblank_timer);
}

static void bcm2835_pixelvalve_class_init(ObjectClass *klass,
                                          const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, bcm2835_pixelvalve_reset);
    dc->vmsd = &bcm2835_pixelvalve_vmstate;
}

static const TypeInfo bcm2835_pixelvalve_info = {
    .name = TYPE_BCM2835_PIXELVALVE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835PixelValveState),
    .instance_init = bcm2835_pixelvalve_init,
    .instance_finalize = bcm2835_pixelvalve_finalize,
    .class_init = bcm2835_pixelvalve_class_init,
};

static void bcm2835_pixelvalve_register_types(void)
{
    type_register_static(&bcm2835_pixelvalve_info);
}

type_init(bcm2835_pixelvalve_register_types)
