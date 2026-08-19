/*
 * BCM2835 multicore synchronization block
 *
 * The VideoCore firmware uses this block for atomic inter-VPU semaphores,
 * semaphore-release interrupts, inter-core doorbells, and scratch mailboxes.
 * A semaphore read atomically returns its previous value and claims it; any
 * write releases it.  The two VPUSEMA registers use the same protocol.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/core/irq.h"
#include "hw/misc/bcm2835_msync.h"
#include "migration/vmstate.h"
#include "qemu/log.h"

#define MS_SEMA_LAST           0x07c
#define MS_STATUS              0x080
#define MS_IREQ_0              0x084
#define MS_IREQ_1              0x088
#define MS_ICSET_0             0x090
#define MS_ICSET_1             0x094
#define MS_ICCLR_0             0x098
#define MS_ICCLR_1             0x09c
#define MS_MBOX_FIRST          0x0a0
#define MS_MBOX_LAST           0x0bc
#define MS_VPUSEMA_0           0x0c0
#define MS_VPUSEMA_1           0x0c4
#define MS_VPU_STAT            0x0c8

static void bcm2835_msync_update_irqs(BCM2835MSyncState *s)
{
    unsigned i;

    /*
     * An enabled semaphore interrupt is level-sensitive while any selected
     * semaphore is free.  Broadcom firmware relies on enabling an interrupt
     * for an already-free semaphore causing an immediate interrupt.
     */
    for (i = 0; i < 2; i++) {
        qemu_set_irq(s->irq[i],
                     (s->irq_requests[i] & ~s->semaphores) != 0);
    }

    qemu_set_irq(s->irq[2], (s->intercore_pending & 1) != 0);
    qemu_set_irq(s->irq[3], (s->intercore_pending & 2) != 0);
}

static uint32_t bcm2835_msync_claim(uint32_t *state, unsigned index)
{
    uint32_t mask = UINT32_C(1) << index;
    uint32_t previous = (*state & mask) != 0;

    *state |= mask;
    return previous;
}

static uint64_t bcm2835_msync_read(void *opaque, hwaddr addr,
                                   unsigned size)
{
    BCM2835MSyncState *s = BCM2835_MSYNC(opaque);
    unsigned index;
    uint32_t result;

    if (addr <= MS_SEMA_LAST) {
        index = addr >> 2;
        result = bcm2835_msync_claim(&s->semaphores, index);
        bcm2835_msync_update_irqs(s);
        return result;
    }

    switch (addr) {
    case MS_STATUS:
        return s->semaphores;
    case MS_IREQ_0:
        return s->irq_requests[0];
    case MS_IREQ_1:
        return s->irq_requests[1];
    case MS_ICSET_0:
    case MS_ICCLR_0:
        return s->intercore_pending & 1;
    case MS_ICSET_1:
    case MS_ICCLR_1:
        return (s->intercore_pending >> 1) & 1;
    case MS_VPUSEMA_0:
    case MS_VPUSEMA_1:
        index = (addr - MS_VPUSEMA_0) >> 2;
        return bcm2835_msync_claim(&s->vpu_semaphores, index);
    case MS_VPU_STAT:
        /*
         * The current heterogeneous machine instantiates VPU0 only.  No
         * second VPU is running, stalled, or awaiting an inter-core event.
         */
        return 0;
    default:
        break;
    }

    if (addr >= MS_MBOX_FIRST && addr <= MS_MBOX_LAST) {
        index = (addr - MS_MBOX_FIRST) >> 2;
        return s->mailboxes[index];
    }

    qemu_log_mask(LOG_UNIMP,
                  TYPE_BCM2835_MSYNC
                  ": unimplemented read at 0x%03" HWADDR_PRIx "\n",
                  addr);
    return 0;
}

static void bcm2835_msync_write(void *opaque, hwaddr addr,
                                uint64_t value, unsigned size)
{
    BCM2835MSyncState *s = BCM2835_MSYNC(opaque);
    uint32_t v = value;
    uint32_t mask;
    unsigned index;

    if (addr <= MS_SEMA_LAST) {
        index = addr >> 2;
        s->semaphores &= ~(UINT32_C(1) << index);
        bcm2835_msync_update_irqs(s);
        return;
    }

    switch (addr) {
    case MS_STATUS:
    case MS_VPU_STAT:
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_MSYNC
                      ": write to read-only register 0x%03" HWADDR_PRIx
                      " (value 0x%08x)\n", addr, v);
        return;
    case MS_IREQ_0:
        s->irq_requests[0] = v;
        bcm2835_msync_update_irqs(s);
        return;
    case MS_IREQ_1:
        s->irq_requests[1] = v;
        bcm2835_msync_update_irqs(s);
        return;
    case MS_ICSET_0:
    case MS_ICSET_1:
        if (v & 1) {
            index = (addr - MS_ICSET_0) >> 2;
            s->intercore_pending |= UINT32_C(1) << index;
            bcm2835_msync_update_irqs(s);
        }
        return;
    case MS_ICCLR_0:
    case MS_ICCLR_1:
        if (v & 1) {
            index = (addr - MS_ICCLR_0) >> 2;
            s->intercore_pending &= ~(UINT32_C(1) << index);
            bcm2835_msync_update_irqs(s);
        }
        return;
    case MS_VPUSEMA_0:
    case MS_VPUSEMA_1:
        index = (addr - MS_VPUSEMA_0) >> 2;
        mask = UINT32_C(1) << index;
        s->vpu_semaphores &= ~mask;
        return;
    default:
        break;
    }

    if (addr >= MS_MBOX_FIRST && addr <= MS_MBOX_LAST) {
        index = (addr - MS_MBOX_FIRST) >> 2;
        s->mailboxes[index] = v;
        return;
    }

    qemu_log_mask(LOG_UNIMP,
                  TYPE_BCM2835_MSYNC
                  ": unimplemented write at 0x%03" HWADDR_PRIx
                  " (value 0x%08x)\n", addr, v);
}

static const MemoryRegionOps bcm2835_msync_ops = {
    .read = bcm2835_msync_read,
    .write = bcm2835_msync_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_msync_reset(DeviceState *dev)
{
    BCM2835MSyncState *s = BCM2835_MSYNC(dev);

    s->semaphores = 0;
    memset(s->irq_requests, 0, sizeof(s->irq_requests));
    s->intercore_pending = 0;
    memset(s->mailboxes, 0, sizeof(s->mailboxes));
    s->vpu_semaphores = 0;
    bcm2835_msync_update_irqs(s);
}

static int bcm2835_msync_post_load(void *opaque, int version_id)
{
    BCM2835MSyncState *s = opaque;

    bcm2835_msync_update_irqs(s);
    return 0;
}

static void bcm2835_msync_realize(DeviceState *dev, Error **errp)
{
    BCM2835MSyncState *s = BCM2835_MSYNC(dev);
    unsigned i;

    memory_region_init_io(&s->iomem, OBJECT(s), &bcm2835_msync_ops, s,
                          TYPE_BCM2835_MSYNC,
                          BCM2835_MSYNC_WINDOW_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);

    for (i = 0; i < BCM2835_MSYNC_IRQ_COUNT; i++) {
        sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq[i]);
    }
}

static const VMStateDescription bcm2835_msync_vmstate = {
    .name = TYPE_BCM2835_MSYNC,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2835_msync_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(semaphores, BCM2835MSyncState),
        VMSTATE_UINT32_ARRAY(irq_requests, BCM2835MSyncState, 2),
        VMSTATE_UINT32(intercore_pending, BCM2835MSyncState),
        VMSTATE_UINT32_ARRAY(mailboxes, BCM2835MSyncState, 8),
        VMSTATE_UINT32(vpu_semaphores, BCM2835MSyncState),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_msync_class_init(ObjectClass *klass,
                                     const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_msync_realize;
    device_class_set_legacy_reset(dc, bcm2835_msync_reset);
    dc->vmsd = &bcm2835_msync_vmstate;
}

static const TypeInfo bcm2835_msync_info = {
    .name = TYPE_BCM2835_MSYNC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835MSyncState),
    .class_init = bcm2835_msync_class_init,
};

static void bcm2835_msync_register_types(void)
{
    type_register_static(&bcm2835_msync_info);
}

type_init(bcm2835_msync_register_types)
