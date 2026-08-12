/*
 * BCM2835 Power Management emulation
 *
 * Copyright (C) 2017 Marcin Chojnacki <marcinch7@gmail.com>
 * Copyright (C) 2021 Nolan Leake <nolan@sigbus.net>
 *
 * This work is licensed under the terms of the GNU GPL, version 2 or later.
 * See the COPYING file in the top-level directory.
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "qemu/module.h"
#include "hw/misc/bcm2835_powermgt.h"
#include "migration/vmstate.h"
#include "system/runstate.h"

#define PASSWORD 0x5a000000
#define PASSWORD_MASK 0xff000000

#define R_RSTC 0x1c
#define V_RSTC_RESET 0x20
#define R_RSTS 0x20
#define V_RSTS_POWEROFF 0x555 /* Linux uses partition 63 to indicate halt. */
#define R_WDOG 0x24

/*
 * PM_PROC is the firmware-visible processor power-domain register.  The bit
 * layout comes from Broadcom's generated cpr_powman.h retained by the open
 * VideoCore firmware projects.
 */
#define R_PROC 0x110
#define V_PROC_CFG_MASK  0x007f0000
#define V_PROC_ENAB      (1u << 12)
#define V_PROC_ARMRSTN   (1u << 6)
#define V_PROC_ISFUNC    (1u << 5)
#define V_PROC_MRDONE    (1u << 4)
#define V_PROC_MEMREP    (1u << 3)
#define V_PROC_ISPOW     (1u << 2)
#define V_PROC_POWOK     (1u << 1)
#define V_PROC_POWUP     (1u << 0)

#define V_PROC_WRITABLE (V_PROC_CFG_MASK | V_PROC_ENAB | V_PROC_ARMRSTN | \
                         V_PROC_ISFUNC | V_PROC_MEMREP | V_PROC_ISPOW | \
                         V_PROC_POWUP)
#define V_PROC_READY (V_PROC_POWUP | V_PROC_POWOK | V_PROC_ISPOW | \
                      V_PROC_MEMREP | V_PROC_MRDONE | V_PROC_ISFUNC | \
                      V_PROC_ARMRSTN)

static void bcm2835_powermgt_update_proc(BCM2835PowerMgtState *s,
                                         uint32_t requested)
{
    bool arm_powered;

    requested &= V_PROC_WRITABLE;

    /*
     * The analogue power controller completes these handshakes
     * asynchronously on hardware.  They complete immediately in this model,
     * while preserving the firmware-visible polling protocol.
     */
    if (requested & V_PROC_POWUP) {
        requested |= V_PROC_POWOK;
    }
    if ((requested & (V_PROC_POWUP | V_PROC_ISPOW | V_PROC_MEMREP)) ==
        (V_PROC_POWUP | V_PROC_ISPOW | V_PROC_MEMREP)) {
        requested |= V_PROC_MRDONE;
    }

    s->proc = requested;
    arm_powered = (s->proc & V_PROC_READY) == V_PROC_READY;
    if (arm_powered != s->arm_powered) {
        s->arm_powered = arm_powered;
        qemu_set_irq(s->arm_power_on, arm_powered);
    }
}

static uint64_t bcm2835_powermgt_read(void *opaque, hwaddr offset,
                                      unsigned size)
{
    BCM2835PowerMgtState *s = (BCM2835PowerMgtState *)opaque;
    uint32_t res = 0;

    switch (offset) {
    case R_RSTC:
        res = s->rstc;
        break;
    case R_RSTS:
        res = s->rsts;
        break;
    case R_WDOG:
        res = s->wdog;
        break;
    case R_PROC:
        res = s->proc;
        break;

    default:
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_powermgt_read: Unknown offset 0x%08"HWADDR_PRIx
                      "\n", offset);
        res = 0;
        break;
    }

    return res;
}

static void bcm2835_powermgt_write(void *opaque, hwaddr offset,
                                   uint64_t value, unsigned size)
{
    BCM2835PowerMgtState *s = (BCM2835PowerMgtState *)opaque;

    if ((value & PASSWORD_MASK) != PASSWORD) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2835_powermgt_write: Bad password 0x%"PRIx64
                      " at offset 0x%08"HWADDR_PRIx"\n",
                      value, offset);
        return;
    }

    value = value & ~PASSWORD_MASK;

    switch (offset) {
    case R_RSTC:
        s->rstc = value;
        if (value & V_RSTC_RESET) {
            if ((s->rsts & 0xfff) == V_RSTS_POWEROFF) {
                qemu_system_shutdown_request(SHUTDOWN_CAUSE_GUEST_SHUTDOWN);
            } else {
                qemu_system_reset_request(SHUTDOWN_CAUSE_GUEST_RESET);
            }
        }
        break;
    case R_RSTS:
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_powermgt_write: RSTS\n");
        s->rsts = value;
        break;
    case R_WDOG:
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_powermgt_write: WDOG\n");
        s->wdog = value;
        break;
    case R_PROC:
        bcm2835_powermgt_update_proc(s, value);
        break;

    default:
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_powermgt_write: Unknown offset 0x%08"HWADDR_PRIx
                      "\n", offset);
        break;
    }
}

static const MemoryRegionOps bcm2835_powermgt_ops = {
    .read = bcm2835_powermgt_read,
    .write = bcm2835_powermgt_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
};

static int bcm2835_powermgt_post_load(void *opaque, int version_id)
{
    BCM2835PowerMgtState *s = opaque;

    qemu_set_irq(s->arm_power_on, s->arm_powered);
    return 0;
}

static const VMStateDescription vmstate_bcm2835_powermgt = {
    .name = TYPE_BCM2835_POWERMGT,
    .version_id = 2,
    .minimum_version_id = 1,
    .post_load = bcm2835_powermgt_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(rstc, BCM2835PowerMgtState),
        VMSTATE_UINT32(rsts, BCM2835PowerMgtState),
        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),
        VMSTATE_UINT32_V(proc, BCM2835PowerMgtState, 2),
        VMSTATE_BOOL_V(arm_powered, BCM2835PowerMgtState, 2),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_powermgt_init(Object *obj)
{
    BCM2835PowerMgtState *s = BCM2835_POWERMGT(obj);

    memory_region_init_io(&s->iomem, obj, &bcm2835_powermgt_ops, s,
                          TYPE_BCM2835_POWERMGT, 0x200);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    qdev_init_gpio_out_named(DEVICE(s), &s->arm_power_on,
                             BCM2835_POWERMGT_ARM_POWER_ON, 1);
}

static void bcm2835_powermgt_reset(DeviceState *dev)
{
    BCM2835PowerMgtState *s = BCM2835_POWERMGT(dev);

    /* https://elinux.org/BCM2835_registers#PM */
    s->rstc = 0x00000102;
    s->rsts = 0x00001000;
    s->wdog = 0x00000000;
    s->proc = 0;
    s->arm_powered = false;
    qemu_set_irq(s->arm_power_on, 0);
}

static void bcm2835_powermgt_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, bcm2835_powermgt_reset);
    dc->vmsd = &vmstate_bcm2835_powermgt;
}

static const TypeInfo bcm2835_powermgt_info = {
    .name          = TYPE_BCM2835_POWERMGT,
    .parent        = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835PowerMgtState),
    .class_init    = bcm2835_powermgt_class_init,
    .instance_init = bcm2835_powermgt_init,
};

static void bcm2835_powermgt_register_types(void)
{
    type_register_static(&bcm2835_powermgt_info);
}

type_init(bcm2835_powermgt_register_types)
