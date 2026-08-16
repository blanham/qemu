/*
 * BCM2835 VideoCore L1 cache-control block
 *
 * The VideoCore firmware programs cache-line ranges, starts a flush,
 * and polls the command bit until hardware clears it.  QEMU has no
 * separate emulated L1 cache at this boundary, so flushes complete
 * synchronously while the persistent control bits remain latched.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/misc/bcm2835_l1cc.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "trace.h"

#define R_IC0_CONTROL       0x000
#define R_IC0_PRIORITY      0x004
#define R_IC0_FLUSH_START   0x008
#define R_IC0_FLUSH_END     0x00c
#define R_IC1_CONTROL       0x080
#define R_IC1_PRIORITY      0x084
#define R_IC1_FLUSH_START   0x088
#define R_IC1_FLUSH_END     0x08c
#define R_D_CONTROL         0x100
#define R_D_FLUSH_START     0x104
#define R_D_FLUSH_END       0x108
#define R_D_PRIORITY        0x10c

#define IC_CONTROL_MASK       0x0000007f
#define IC_CONTROL_FLUSH      (1u << 1)
#define IC_PRIORITY_MASK      0x0000ffff
#define IC_FLUSH_ADDR_MASK    0xffffffe0

#define D_CONTROL_MASK        0x0000000f
#define D_CONTROL_FLUSH_MASK  ((1u << 1) | (1u << 2))
#define D_FLUSH_ADDR_MASK     0x3fffffe0
#define D_PRIORITY_MASK       0x0fff0fff

static uint64_t bcm2835_l1cc_read(void *opaque, hwaddr offset,
                                  unsigned size)
{
    BCM2835L1CCState *s = BCM2835_L1CC(opaque);
    uint32_t value;

    switch (offset) {
    case R_IC0_CONTROL:
        value = s->ic_control[0];
        break;
    case R_IC0_PRIORITY:
        value = s->ic_priority[0];
        break;
    case R_IC0_FLUSH_START:
        value = s->ic_flush_start[0];
        break;
    case R_IC0_FLUSH_END:
        value = s->ic_flush_end[0];
        break;
    case R_IC1_CONTROL:
        value = s->ic_control[1];
        break;
    case R_IC1_PRIORITY:
        value = s->ic_priority[1];
        break;
    case R_IC1_FLUSH_START:
        value = s->ic_flush_start[1];
        break;
    case R_IC1_FLUSH_END:
        value = s->ic_flush_end[1];
        break;
    case R_D_CONTROL:
        value = s->d_control;
        break;
    case R_D_FLUSH_START:
        value = s->d_flush_start;
        break;
    case R_D_FLUSH_END:
        value = s->d_flush_end;
        break;
    case R_D_PRIORITY:
        value = s->d_priority;
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_L1CC
                      ": unimplemented read at 0x%03" HWADDR_PRIx
                      "\n", offset);
        value = 0;
        break;
    }

    trace_bcm2835_l1cc_read(offset, value);
    return value;
}

static void bcm2835_l1cc_write(void *opaque, hwaddr offset,
                               uint64_t value, unsigned size)
{
    BCM2835L1CCState *s = BCM2835_L1CC(opaque);
    uint32_t v = value;
    uint32_t result = 0;

    switch (offset) {
    case R_IC0_CONTROL:
        s->ic_control[0] = (v & IC_CONTROL_MASK) &
                           ~IC_CONTROL_FLUSH;
        result = s->ic_control[0];
        break;
    case R_IC0_PRIORITY:
        s->ic_priority[0] = v & IC_PRIORITY_MASK;
        result = s->ic_priority[0];
        break;
    case R_IC0_FLUSH_START:
        s->ic_flush_start[0] = v & IC_FLUSH_ADDR_MASK;
        result = s->ic_flush_start[0];
        break;
    case R_IC0_FLUSH_END:
        s->ic_flush_end[0] = v & IC_FLUSH_ADDR_MASK;
        result = s->ic_flush_end[0];
        break;
    case R_IC1_CONTROL:
        s->ic_control[1] = (v & IC_CONTROL_MASK) &
                           ~IC_CONTROL_FLUSH;
        result = s->ic_control[1];
        break;
    case R_IC1_PRIORITY:
        s->ic_priority[1] = v & IC_PRIORITY_MASK;
        result = s->ic_priority[1];
        break;
    case R_IC1_FLUSH_START:
        s->ic_flush_start[1] = v & IC_FLUSH_ADDR_MASK;
        result = s->ic_flush_start[1];
        break;
    case R_IC1_FLUSH_END:
        s->ic_flush_end[1] = v & IC_FLUSH_ADDR_MASK;
        result = s->ic_flush_end[1];
        break;
    case R_D_CONTROL:
        s->d_control = (v & D_CONTROL_MASK) &
                       ~D_CONTROL_FLUSH_MASK;
        result = s->d_control;
        break;
    case R_D_FLUSH_START:
        s->d_flush_start = v & D_FLUSH_ADDR_MASK;
        result = s->d_flush_start;
        break;
    case R_D_FLUSH_END:
        s->d_flush_end = v & D_FLUSH_ADDR_MASK;
        result = s->d_flush_end;
        break;
    case R_D_PRIORITY:
        s->d_priority = v & D_PRIORITY_MASK;
        result = s->d_priority;
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_L1CC
                      ": unimplemented write at 0x%03" HWADDR_PRIx
                      " (value 0x%08x)\n", offset, v);
        break;
    }

    trace_bcm2835_l1cc_write(offset, v, result);
}

static const MemoryRegionOps bcm2835_l1cc_ops = {
    .read = bcm2835_l1cc_read,
    .write = bcm2835_l1cc_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_l1cc_reset(DeviceState *dev)
{
    BCM2835L1CCState *s = BCM2835_L1CC(dev);
    unsigned i;

    for (i = 0; i < ARRAY_SIZE(s->ic_control); i++) {
        s->ic_control[i] = 0;
        s->ic_priority[i] = 0x000034af;
        s->ic_flush_start[i] = 0;
        s->ic_flush_end[i] = 0xffffffff;
    }
    s->d_control = 0;
    s->d_flush_start = 0;
    s->d_flush_end = 0x3fffffff;
    s->d_priority = 0;
}

static void bcm2835_l1cc_realize(DeviceState *dev, Error **errp)
{
    BCM2835L1CCState *s = BCM2835_L1CC(dev);

    memory_region_init_io(&s->iomem, OBJECT(s), &bcm2835_l1cc_ops, s,
                          TYPE_BCM2835_L1CC,
                          BCM2835_L1CC_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static const VMStateDescription bcm2835_l1cc_vmstate = {
    .name = TYPE_BCM2835_L1CC,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(ic_control, BCM2835L1CCState, 2),
        VMSTATE_UINT32_ARRAY(ic_priority, BCM2835L1CCState, 2),
        VMSTATE_UINT32_ARRAY(ic_flush_start, BCM2835L1CCState, 2),
        VMSTATE_UINT32_ARRAY(ic_flush_end, BCM2835L1CCState, 2),
        VMSTATE_UINT32(d_control, BCM2835L1CCState),
        VMSTATE_UINT32(d_flush_start, BCM2835L1CCState),
        VMSTATE_UINT32(d_flush_end, BCM2835L1CCState),
        VMSTATE_UINT32(d_priority, BCM2835L1CCState),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_l1cc_class_init(ObjectClass *klass,
                                    const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_l1cc_realize;
    device_class_set_legacy_reset(dc, bcm2835_l1cc_reset);
    dc->vmsd = &bcm2835_l1cc_vmstate;
}

static const TypeInfo bcm2835_l1cc_info = {
    .name = TYPE_BCM2835_L1CC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835L1CCState),
    .class_init = bcm2835_l1cc_class_init,
};

static void bcm2835_l1cc_register_types(void)
{
    type_register_static(&bcm2835_l1cc_info);
}

type_init(bcm2835_l1cc_register_types)
