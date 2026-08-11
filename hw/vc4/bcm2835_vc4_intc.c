/*
 * Broadcom BCM283x VideoCore IV vectored interrupt controller
 *
 * The register layout follows the Broadcom-generated intctrl0/intctrl1
 * headers retained by the open VideoCore firmware projects.  Each external
 * source has a three-bit priority in a four-bit mask slot.  Zero masks a
 * source; priorities 1..7 are eligible, subject to IC_C.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/vc4/bcm2835_vc4_intc.h"
#include "hw/core/irq.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "qemu/module.h"

enum {
    VC4_INTC_C          = 0x00,
    VC4_INTC_S          = 0x04,
    VC4_INTC_SRC0       = 0x08,
    VC4_INTC_SRC1       = 0x0c,
    VC4_INTC_MASK0      = 0x10,
    VC4_INTC_MASK7      = 0x2c,
    VC4_INTC_VADDR      = 0x30,
    VC4_INTC_WAKEUP     = 0x34,
    VC4_INTC_PROFILE    = 0x38,
    VC4_INTC_FORCE0     = 0x40,
    VC4_INTC_FORCE1     = 0x44,
    VC4_INTC_FORCE0_SET = 0x48,
    VC4_INTC_FORCE1_SET = 0x4c,
    VC4_INTC_FORCE0_CLR = 0x50,
    VC4_INTC_FORCE1_CLR = 0x54,
};

static unsigned vc4_intc_source_priority(const BCM2835VC4IntcState *s,
                                         unsigned source)
{
    return extract32(s->mask[source >> 3], (source & 7) * 4, 3);
}

static uint32_t vc4_intc_status_field(int vector, int priority)
{
    if (vector < 0) {
        return 0;
    }

    return (vector & 0x7f) | ((priority & 7) << 8);
}

static void vc4_intc_update(BCM2835VC4IntcState *s)
{
    uint64_t pending = ((uint64_t)(s->source[1] | s->force[1]) << 32) |
                       (s->source[0] | s->force[0]);
    int best_source = -1;
    int best_priority = -1;
    unsigned threshold = s->control & 7;
    unsigned source;

    for (source = 0; source < BCM2835_VC4_INTC_NUM_IRQS; source++) {
        unsigned priority;

        if (!(pending & (UINT64_C(1) << source))) {
            continue;
        }

        priority = vc4_intc_source_priority(s, source);
        if (priority == 0 || priority <= threshold) {
            continue;
        }

        if ((int)priority > best_priority) {
            best_source = source;
            best_priority = priority;
        }
    }

    s->pending_vector = best_source < 0 ? -1 : best_source + 64;
    s->pending_priority = best_priority;

    /*
     * The CPU acknowledges the vector through the device API.  Hold the
     * output low while a handler is active; RTI completes it and lets a
     * still-level source retrigger.
     */
    qemu_set_irq(s->irq,
                 s->active_vector < 0 && s->pending_vector >= 0);
}

static void vc4_intc_set_irq(void *opaque, int irq, int level)
{
    BCM2835VC4IntcState *s = opaque;
    unsigned bank = irq >> 5;
    unsigned bit = irq & 31;

    s->source[bank] = deposit32(s->source[bank], bit, 1, level != 0);
    vc4_intc_update(s);
}

bool bcm2835_vc4_intc_acknowledge(BCM2835VC4IntcState *s,
                                  uint32_t *vector,
                                  uint32_t *vector_base)
{
    if (s->active_vector >= 0 || s->pending_vector < 0) {
        return false;
    }

    s->active_vector = s->pending_vector;
    s->active_priority = s->pending_priority;
    *vector = s->active_vector;
    *vector_base = s->vaddr;
    vc4_intc_update(s);
    return true;
}

void bcm2835_vc4_intc_complete(BCM2835VC4IntcState *s)
{
    s->active_vector = -1;
    s->active_priority = -1;
    vc4_intc_update(s);
}

static uint64_t vc4_intc_read(void *opaque, hwaddr offset, unsigned size)
{
    BCM2835VC4IntcState *s = opaque;
    uint32_t current;
    uint32_t next;

    if (offset >= VC4_INTC_MASK0 && offset <= VC4_INTC_MASK7) {
        return s->mask[(offset - VC4_INTC_MASK0) >> 2];
    }

    switch (offset) {
    case VC4_INTC_C:
        return s->control;
    case VC4_INTC_S:
        current = vc4_intc_status_field(
            s->active_vector >= 0 ? s->active_vector : s->pending_vector,
            s->active_vector >= 0 ? s->active_priority :
                                    s->pending_priority);
        next = vc4_intc_status_field(s->pending_vector,
                                     s->pending_priority);
        return current | (next << 16);
    case VC4_INTC_SRC0:
        return s->source[0] | s->force[0];
    case VC4_INTC_SRC1:
        return s->source[1] | s->force[1];
    case VC4_INTC_VADDR:
        return s->vaddr;
    case VC4_INTC_WAKEUP:
        return s->wakeup;
    case VC4_INTC_PROFILE:
        return s->profile;
    case VC4_INTC_FORCE0:
    case VC4_INTC_FORCE0_SET:
    case VC4_INTC_FORCE0_CLR:
        return s->force[0];
    case VC4_INTC_FORCE1:
    case VC4_INTC_FORCE1_SET:
    case VC4_INTC_FORCE1_CLR:
        return s->force[1];
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: bad read offset 0x%" HWADDR_PRIx "\n",
                      TYPE_BCM2835_VC4_INTC, offset);
        return 0;
    }
}

static void vc4_intc_write(void *opaque, hwaddr offset, uint64_t value,
                           unsigned size)
{
    BCM2835VC4IntcState *s = opaque;
    unsigned bank;

    if (offset >= VC4_INTC_MASK0 && offset <= VC4_INTC_MASK7) {
        s->mask[(offset - VC4_INTC_MASK0) >> 2] =
            value & 0x77777777u;
        vc4_intc_update(s);
        return;
    }

    switch (offset) {
    case VC4_INTC_C:
        s->control = value & 0xf;
        break;
    case VC4_INTC_VADDR:
        s->vaddr = value & 0xfffffe00u;
        break;
    case VC4_INTC_WAKEUP:
        s->wakeup = value & 0xfffffffeu;
        break;
    case VC4_INTC_PROFILE:
        s->profile = value & 0xffff;
        break;
    case VC4_INTC_FORCE0:
    case VC4_INTC_FORCE1:
        bank = (offset == VC4_INTC_FORCE1);
        s->force[bank] = value;
        break;
    case VC4_INTC_FORCE0_SET:
    case VC4_INTC_FORCE1_SET:
        bank = (offset == VC4_INTC_FORCE1_SET);
        s->force[bank] |= value;
        break;
    case VC4_INTC_FORCE0_CLR:
    case VC4_INTC_FORCE1_CLR:
        bank = (offset == VC4_INTC_FORCE1_CLR);
        s->force[bank] &= ~value;
        break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: bad write offset 0x%" HWADDR_PRIx
                      " value 0x%" PRIx64 "\n",
                      TYPE_BCM2835_VC4_INTC, offset, value);
        return;
    }

    vc4_intc_update(s);
}

static const MemoryRegionOps vc4_intc_ops = {
    .read = vc4_intc_read,
    .write = vc4_intc_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
};

static void vc4_intc_reset(DeviceState *dev)
{
    BCM2835VC4IntcState *s = BCM2835_VC4_INTC(dev);

    s->control = 0;
    memset(s->source, 0, sizeof(s->source));
    memset(s->force, 0, sizeof(s->force));
    memset(s->mask, 0, sizeof(s->mask));
    s->vaddr = 0;
    s->wakeup = 0x10000000;
    s->profile = 0;
    s->active_vector = -1;
    s->pending_vector = -1;
    s->active_priority = -1;
    s->pending_priority = -1;
    qemu_set_irq(s->irq, 0);
}

static const VMStateDescription vmstate_vc4_intc = {
    .name = TYPE_BCM2835_VC4_INTC,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, BCM2835VC4IntcState),
        VMSTATE_UINT32_ARRAY(source, BCM2835VC4IntcState, 2),
        VMSTATE_UINT32_ARRAY(force, BCM2835VC4IntcState, 2),
        VMSTATE_UINT32_ARRAY(mask, BCM2835VC4IntcState, 8),
        VMSTATE_UINT32(vaddr, BCM2835VC4IntcState),
        VMSTATE_UINT32(wakeup, BCM2835VC4IntcState),
        VMSTATE_UINT32(profile, BCM2835VC4IntcState),
        VMSTATE_INT16(active_vector, BCM2835VC4IntcState),
        VMSTATE_INT16(pending_vector, BCM2835VC4IntcState),
        VMSTATE_INT8(active_priority, BCM2835VC4IntcState),
        VMSTATE_INT8(pending_priority, BCM2835VC4IntcState),
        VMSTATE_END_OF_LIST()
    },
};

static void vc4_intc_init(Object *obj)
{
    BCM2835VC4IntcState *s = BCM2835_VC4_INTC(obj);

    memory_region_init_io(&s->iomem, obj, &vc4_intc_ops, s,
                          TYPE_BCM2835_VC4_INTC, 0x100);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
    qdev_init_gpio_in_named(DEVICE(s), vc4_intc_set_irq,
                            BCM2835_VC4_INTC_GPU_IRQ,
                            BCM2835_VC4_INTC_NUM_IRQS);
}

static void vc4_intc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, vc4_intc_reset);
    dc->vmsd = &vmstate_vc4_intc;
}

static const TypeInfo vc4_intc_type_info = {
    .name = TYPE_BCM2835_VC4_INTC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835VC4IntcState),
    .instance_init = vc4_intc_init,
    .class_init = vc4_intc_class_init,
};

static void vc4_intc_register_types(void)
{
    type_register_static(&vc4_intc_type_info);
}

type_init(vc4_intc_register_types)
