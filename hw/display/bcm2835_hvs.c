/*
 * Raspberry Pi BCM2835 Hardware Video Scaler control model
 *
 * The VC4 renderer and plane-list construction remain in the guest driver.
 * This device models the hardware-visible HVS register/DLIST window and the
 * display-list handoff needed by the CRTC vblank path.  Linux writes a pending
 * list pointer through DISPLISTn and completes a flip only after DISPLACTn
 * reports that pointer.  The real block latches it at display start; mirroring
 * it immediately is equivalent by the next pixel-valve vblank and avoids
 * inventing a software renderer.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/bcm2835_hvs.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define SCALER_DISPCTRL              0x0000
#define SCALER_DISPSTAT              0x0004
#define SCALER_DISPLIST0             0x0020
#define SCALER_DISPLACT0             0x0030
#define SCALER_DISPCTRL0             0x0040
#define SCALER_DISPSTAT0             0x0048

#define SCALER_CHANNEL_COUNT         3
#define SCALER_LIST_STRIDE           0x4
#define SCALER_CHANNEL_STRIDE        0x10

#define SCALER_DISPCTRLX_ENABLE      BIT(31)
#define SCALER_DISPCTRLX_RESET       BIT(30)

#define SCALER_DISPSTATX_MODE_SHIFT  30
#define SCALER_DISPSTATX_MODE_RUN    UINT32_C(2)
#define SCALER_DISPSTATX_FULL        BIT(29)
#define SCALER_DISPSTATX_EMPTY       BIT(28)

#define SCALER_IRQ_ENABLE_MASK       UINT32_C(0x1f)

static unsigned bcm2835_hvs_index(hwaddr offset)
{
    return offset / sizeof(uint32_t);
}

static int bcm2835_hvs_linear_channel(hwaddr offset, hwaddr first)
{
    hwaddr delta;

    if (offset < first) {
        return -1;
    }
    delta = offset - first;
    if (delta % SCALER_LIST_STRIDE != 0 ||
        delta / SCALER_LIST_STRIDE >= SCALER_CHANNEL_COUNT) {
        return -1;
    }
    return delta / SCALER_LIST_STRIDE;
}

static int bcm2835_hvs_strided_channel(hwaddr offset, hwaddr first)
{
    hwaddr delta;

    if (offset < first) {
        return -1;
    }
    delta = offset - first;
    if (delta % SCALER_CHANNEL_STRIDE != 0 ||
        delta / SCALER_CHANNEL_STRIDE >= SCALER_CHANNEL_COUNT) {
        return -1;
    }
    return delta / SCALER_CHANNEL_STRIDE;
}

static hwaddr bcm2835_hvs_list_offset(unsigned channel)
{
    return SCALER_DISPLIST0 + channel * SCALER_LIST_STRIDE;
}

static hwaddr bcm2835_hvs_active_offset(unsigned channel)
{
    return SCALER_DISPLACT0 + channel * SCALER_LIST_STRIDE;
}

static hwaddr bcm2835_hvs_control_offset(unsigned channel)
{
    return SCALER_DISPCTRL0 + channel * SCALER_CHANNEL_STRIDE;
}

static hwaddr bcm2835_hvs_status_offset(unsigned channel)
{
    return SCALER_DISPSTAT0 + channel * SCALER_CHANNEL_STRIDE;
}

static void bcm2835_hvs_update_irq(BCM2835HVSState *s)
{
    uint32_t pending = s->regs[bcm2835_hvs_index(SCALER_DISPSTAT)];
    uint32_t enabled = s->regs[bcm2835_hvs_index(SCALER_DISPCTRL)];

    qemu_set_irq(s->irq, (pending & enabled & SCALER_IRQ_ENABLE_MASK) != 0);
}

static void bcm2835_hvs_set_channel_control(BCM2835HVSState *s,
                                             unsigned channel,
                                             uint32_t value)
{
    hwaddr control = bcm2835_hvs_control_offset(channel);
    hwaddr status = bcm2835_hvs_status_offset(channel);
    uint32_t state;

    if (value & SCALER_DISPCTRLX_RESET) {
        /* RESET is a self-clearing command. */
        s->regs[bcm2835_hvs_index(control)] = 0;
        state = SCALER_DISPSTATX_EMPTY;
    } else if (value & SCALER_DISPCTRLX_ENABLE) {
        s->regs[bcm2835_hvs_index(control)] = value;
        state = (SCALER_DISPSTATX_MODE_RUN <<
                 SCALER_DISPSTATX_MODE_SHIFT) |
                SCALER_DISPSTATX_FULL;
        s->regs[bcm2835_hvs_index(
            bcm2835_hvs_active_offset(channel))] =
            s->regs[bcm2835_hvs_index(
                bcm2835_hvs_list_offset(channel))];
    } else {
        s->regs[bcm2835_hvs_index(control)] = value;
        state = SCALER_DISPSTATX_EMPTY;
    }

    s->regs[bcm2835_hvs_index(status)] = state;
}

static uint64_t bcm2835_hvs_read(void *opaque, hwaddr offset, unsigned size)
{
    BCM2835HVSState *s = opaque;

    if (size != 4 || (offset & 3) || offset >= BCM2835_HVS_MMIO_SIZE) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HVS ": invalid read at 0x%"
                      HWADDR_PRIx " (size %u)\n", offset, size);
        return 0;
    }

    return s->regs[bcm2835_hvs_index(offset)];
}

static void bcm2835_hvs_write(void *opaque, hwaddr offset,
                              uint64_t value, unsigned size)
{
    BCM2835HVSState *s = opaque;
    uint32_t val = value;
    int channel;

    if (size != 4 || (offset & 3) || offset >= BCM2835_HVS_MMIO_SIZE) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_HVS ": invalid write at 0x%"
                      HWADDR_PRIx " (size %u)\n", offset, size);
        return;
    }

    if (offset == SCALER_DISPSTAT) {
        /* Global interrupt status is write-one-to-clear. */
        s->regs[bcm2835_hvs_index(offset)] &= ~val;
        bcm2835_hvs_update_irq(s);
        return;
    }

    channel = bcm2835_hvs_linear_channel(offset, SCALER_DISPLIST0);
    if (channel >= 0) {
        s->regs[bcm2835_hvs_index(offset)] = val;
        s->regs[bcm2835_hvs_index(
            bcm2835_hvs_active_offset(channel))] = val;
        return;
    }

    channel = bcm2835_hvs_linear_channel(offset, SCALER_DISPLACT0);
    if (channel >= 0) {
        /* DISPLACTn is hardware-owned. */
        return;
    }

    channel = bcm2835_hvs_strided_channel(offset, SCALER_DISPCTRL0);
    if (channel >= 0) {
        bcm2835_hvs_set_channel_control(s, channel, val);
        return;
    }

    channel = bcm2835_hvs_strided_channel(offset, SCALER_DISPSTAT0);
    if (channel >= 0) {
        /* Per-channel status is hardware-owned. */
        return;
    }

    s->regs[bcm2835_hvs_index(offset)] = val;
    if (offset == SCALER_DISPCTRL) {
        bcm2835_hvs_update_irq(s);
    }
}

static const MemoryRegionOps bcm2835_hvs_ops = {
    .read = bcm2835_hvs_read,
    .write = bcm2835_hvs_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
        .unaligned = false,
    },
};

static void bcm2835_hvs_reset(DeviceState *dev)
{
    BCM2835HVSState *s = BCM2835_HVS(dev);
    unsigned channel;

    memset(s->regs, 0, sizeof(s->regs));
    for (channel = 0; channel < SCALER_CHANNEL_COUNT; channel++) {
        bcm2835_hvs_set_channel_control(s, channel, 0);
    }
    bcm2835_hvs_update_irq(s);
}

static int bcm2835_hvs_post_load(void *opaque, int version_id)
{
    BCM2835HVSState *s = opaque;

    (void)version_id;
    bcm2835_hvs_update_irq(s);
    return 0;
}

static const VMStateDescription vmstate_bcm2835_hvs = {
    .name = TYPE_BCM2835_HVS,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2835_hvs_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2835HVSState,
                             BCM2835_HVS_REG_WORDS),
        VMSTATE_END_OF_LIST()
    },
};

static void bcm2835_hvs_init(Object *obj)
{
    BCM2835HVSState *s = BCM2835_HVS(obj);

    memory_region_init_io(&s->iomem, obj, &bcm2835_hvs_ops, s,
                          TYPE_BCM2835_HVS, BCM2835_HVS_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
}

static void bcm2835_hvs_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, bcm2835_hvs_reset);
    dc->vmsd = &vmstate_bcm2835_hvs;
    dc->desc = "BCM2835 Hardware Video Scaler control block";
}

static const TypeInfo bcm2835_hvs_info = {
    .name = TYPE_BCM2835_HVS,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835HVSState),
    .instance_init = bcm2835_hvs_init,
    .class_init = bcm2835_hvs_class_init,
};

static void bcm2835_hvs_register_types(void)
{
    type_register_static(&bcm2835_hvs_info);
}

type_init(bcm2835_hvs_register_types)
