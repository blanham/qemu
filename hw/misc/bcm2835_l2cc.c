/*
 * BCM2835 VideoCore L2 cache-control block
 *
 * The VideoCore firmware programs a cache-line range, starts an L2
 * operation through CONTROL.FLUSH, and polls that command bit until
 * hardware clears it.  QEMU has no separately visible VideoCore L2
 * cache at this boundary, so the operation completes synchronously
 * while every persistent control field remains latched.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/misc/bcm2835_l2cc.h"
#include "migration/vmstate.h"
#include "qemu/log.h"
#include "trace.h"

#define R_CONTROL               0x000
#define R_FLUSH_START           0x004
#define R_FLUSH_END             0x008
#define R_ALIAS_EXCEPTION       0x080
#define R_ALIAS_EXCEPTION_ID    0x084
#define R_ALIAS_EXCEPTION_ADDR  0x088
#define R_RD_HITS               0x100
#define R_RD_MISSES             0x104
#define R_WR_HITS               0x108
#define R_WR_MISSES             0x10c
#define R_WR_BACKS              0x110
#define R_IN_FLIGHT             0x114
#define R_STALLS                0x11c
#define R_TAG_STALLS            0x120
#define R_SD_STALLS             0x124

#define CONTROL_MASK            0x00ff0c3f
#define CONTROL_FLUSH           (1u << 2)
#define FLUSH_ADDRESS_MASK      0x0fffffe0
#define IN_FLIGHT_MASK          0x0000000f

static uint64_t bcm2835_l2cc_read(void *opaque, hwaddr offset,
                                  unsigned size)
{
    BCM2835L2CCState *s = BCM2835_L2CC(opaque);
    uint32_t value;

    switch (offset) {
    case R_CONTROL:
        value = s->control;
        break;
    case R_FLUSH_START:
        value = s->flush_start;
        break;
    case R_FLUSH_END:
        value = s->flush_end;
        break;
    case R_ALIAS_EXCEPTION:
        value = s->alias_exception;
        break;
    case R_ALIAS_EXCEPTION_ID:
        value = s->alias_exception_id;
        break;
    case R_ALIAS_EXCEPTION_ADDR:
        value = s->alias_exception_addr;
        break;
    case R_RD_HITS:
        value = s->rd_hits;
        break;
    case R_RD_MISSES:
        value = s->rd_misses;
        break;
    case R_WR_HITS:
        value = s->wr_hits;
        break;
    case R_WR_MISSES:
        value = s->wr_misses;
        break;
    case R_WR_BACKS:
        value = s->wr_backs;
        break;
    case R_IN_FLIGHT:
        value = s->in_flight & IN_FLIGHT_MASK;
        break;
    case R_STALLS:
        value = s->stalls;
        break;
    case R_TAG_STALLS:
        value = s->tag_stalls;
        break;
    case R_SD_STALLS:
        value = s->sd_stalls;
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_L2CC
                      ": unimplemented read at 0x%03" HWADDR_PRIx
                      "\n", offset);
        value = 0;
        break;
    }

    trace_bcm2835_l2cc_read(offset, value);
    return value;
}

static void bcm2835_l2cc_read_only_write(hwaddr offset,
                                         uint32_t value)
{
    qemu_log_mask(LOG_GUEST_ERROR,
                  TYPE_BCM2835_L2CC
                  ": write to read-only register 0x%03" HWADDR_PRIx
                  " (value 0x%08x)\n", offset, value);
}

static void bcm2835_l2cc_write(void *opaque, hwaddr offset,
                               uint64_t value, unsigned size)
{
    BCM2835L2CCState *s = BCM2835_L2CC(opaque);
    uint32_t v = value;
    uint32_t result = 0;

    switch (offset) {
    case R_CONTROL:
        /* FLUSH is a command bit and self-clears on completion. */
        s->control = (v & CONTROL_MASK) & ~CONTROL_FLUSH;
        result = s->control;
        break;
    case R_FLUSH_START:
        s->flush_start = v & FLUSH_ADDRESS_MASK;
        result = s->flush_start;
        break;
    case R_FLUSH_END:
        s->flush_end = v & FLUSH_ADDRESS_MASK;
        result = s->flush_end;
        break;
    case R_ALIAS_EXCEPTION:
        s->alias_exception = v;
        result = s->alias_exception;
        break;
    case R_RD_HITS:
        /* This is the one architecturally writable counter. */
        s->rd_hits = v;
        result = s->rd_hits;
        break;
    case R_ALIAS_EXCEPTION_ID:
    case R_ALIAS_EXCEPTION_ADDR:
    case R_RD_MISSES:
    case R_WR_HITS:
    case R_WR_MISSES:
    case R_WR_BACKS:
    case R_IN_FLIGHT:
    case R_STALLS:
    case R_TAG_STALLS:
    case R_SD_STALLS:
        bcm2835_l2cc_read_only_write(offset, v);
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_L2CC
                      ": unimplemented write at 0x%03" HWADDR_PRIx
                      " (value 0x%08x)\n", offset, v);
        break;
    }

    trace_bcm2835_l2cc_write(offset, v, result);
}

static const MemoryRegionOps bcm2835_l2cc_ops = {
    .read = bcm2835_l2cc_read,
    .write = bcm2835_l2cc_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_l2cc_reset(DeviceState *dev)
{
    BCM2835L2CCState *s = BCM2835_L2CC(dev);

    s->control = 0;
    s->flush_start = 0;
    s->flush_end = FLUSH_ADDRESS_MASK;
    s->alias_exception = 0;
    s->alias_exception_id = 0;
    s->alias_exception_addr = 0;
    s->rd_hits = 0;
    s->rd_misses = 0;
    s->wr_hits = 0;
    s->wr_misses = 0;
    s->wr_backs = 0;
    s->in_flight = 0;
    s->stalls = 0;
    s->tag_stalls = 0;
    s->sd_stalls = 0;
}

static void bcm2835_l2cc_realize(DeviceState *dev, Error **errp)
{
    BCM2835L2CCState *s = BCM2835_L2CC(dev);

    memory_region_init_io(&s->iomem, OBJECT(s), &bcm2835_l2cc_ops, s,
                          TYPE_BCM2835_L2CC,
                          BCM2835_L2CC_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
}

static const VMStateDescription bcm2835_l2cc_vmstate = {
    .name = TYPE_BCM2835_L2CC,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(control, BCM2835L2CCState),
        VMSTATE_UINT32(flush_start, BCM2835L2CCState),
        VMSTATE_UINT32(flush_end, BCM2835L2CCState),
        VMSTATE_UINT32(alias_exception, BCM2835L2CCState),
        VMSTATE_UINT32(alias_exception_id, BCM2835L2CCState),
        VMSTATE_UINT32(alias_exception_addr, BCM2835L2CCState),
        VMSTATE_UINT32(rd_hits, BCM2835L2CCState),
        VMSTATE_UINT32(rd_misses, BCM2835L2CCState),
        VMSTATE_UINT32(wr_hits, BCM2835L2CCState),
        VMSTATE_UINT32(wr_misses, BCM2835L2CCState),
        VMSTATE_UINT32(wr_backs, BCM2835L2CCState),
        VMSTATE_UINT32(in_flight, BCM2835L2CCState),
        VMSTATE_UINT32(stalls, BCM2835L2CCState),
        VMSTATE_UINT32(tag_stalls, BCM2835L2CCState),
        VMSTATE_UINT32(sd_stalls, BCM2835L2CCState),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_l2cc_class_init(ObjectClass *klass,
                                    const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_l2cc_realize;
    device_class_set_legacy_reset(dc, bcm2835_l2cc_reset);
    dc->vmsd = &bcm2835_l2cc_vmstate;
}

static const TypeInfo bcm2835_l2cc_info = {
    .name = TYPE_BCM2835_L2CC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835L2CCState),
    .class_init = bcm2835_l2cc_class_init,
};

static void bcm2835_l2cc_register_types(void)
{
    type_register_static(&bcm2835_l2cc_info);
}

type_init(bcm2835_l2cc_register_types)
