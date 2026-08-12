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
 * PM_IMAGE, PM_GRAFX, and PM_PROC are firmware-visible power-domain
 * registers.  The register layout comes from Broadcom's generated
 * cpr_powman.h; the inrush field is also exercised by the upstream Linux
 * bcm2835 power-domain driver.
 */
#define R_IMAGE 0x108
#define R_GRAFX 0x10c
#define R_PROC 0x110

#define V_DOMAIN_CFG_MASK    0x007f0000
#define V_DOMAIN_INRUSH_MASK (3u << 13)
#define V_DOMAIN_ENAB        (1u << 12)
#define V_DOMAIN_ISFUNC      (1u << 5)
#define V_DOMAIN_MRDONE      (1u << 4)
#define V_DOMAIN_MEMREP      (1u << 3)
#define V_DOMAIN_ISPOW       (1u << 2)
#define V_DOMAIN_POWOK       (1u << 1)
#define V_DOMAIN_POWUP       (1u << 0)

#define V_IMAGE_ISPRSTN      (1u << 8)
#define V_IMAGE_H264RSTN     (1u << 7)
#define V_IMAGE_PERIRSTN     (1u << 6)
#define V_GRAFX_V3DRSTN      (1u << 6)
#define V_PROC_ARMRSTN       (1u << 6)

#define V_DOMAIN_REQUEST_BITS \
    (V_DOMAIN_CFG_MASK | V_DOMAIN_INRUSH_MASK | V_DOMAIN_ENAB | \
     V_DOMAIN_ISFUNC | V_DOMAIN_MEMREP | V_DOMAIN_ISPOW | V_DOMAIN_POWUP)

#define V_IMAGE_WRITABLE \
    (V_DOMAIN_REQUEST_BITS | V_IMAGE_ISPRSTN | V_IMAGE_H264RSTN | \
     V_IMAGE_PERIRSTN)
#define V_GRAFX_WRITABLE (V_DOMAIN_REQUEST_BITS | V_GRAFX_V3DRSTN)
#define V_PROC_WRITABLE \
    (V_DOMAIN_CFG_MASK | V_DOMAIN_ENAB | V_PROC_ARMRSTN | \
     V_DOMAIN_ISFUNC | V_DOMAIN_MEMREP | V_DOMAIN_ISPOW | \
     V_DOMAIN_POWUP)

#define V_PROC_READY \
    (V_DOMAIN_POWUP | V_DOMAIN_POWOK | V_DOMAIN_ISPOW | \
     V_DOMAIN_MEMREP | V_DOMAIN_MRDONE | V_DOMAIN_ISFUNC | \
     V_PROC_ARMRSTN)

#define V_IMAGE_RESET V_DOMAIN_ENAB
#define V_GRAFX_RESET V_DOMAIN_ENAB

static uint32_t bcm2835_powermgt_complete_domain(uint32_t requested,
                                                 uint32_t writable)
{
    requested &= writable;

    /*
     * The analogue controller completes these edges asynchronously on real
     * silicon.  Completing them immediately keeps the firmware-visible
     * request/poll protocol intact without introducing host-time races.
     */
    if (requested & V_DOMAIN_POWUP) {
        requested |= V_DOMAIN_POWOK;
    }
    if ((requested &
         (V_DOMAIN_POWUP | V_DOMAIN_ISPOW | V_DOMAIN_MEMREP)) ==
        (V_DOMAIN_POWUP | V_DOMAIN_ISPOW | V_DOMAIN_MEMREP)) {
        requested |= V_DOMAIN_MRDONE;
    }

    return requested;
}

static void bcm2835_powermgt_update_proc(BCM2835PowerMgtState *s,
                                         uint32_t requested)
{
    bool arm_powered;

    s->proc = bcm2835_powermgt_complete_domain(requested,
                                               V_PROC_WRITABLE);
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
    case R_IMAGE:
        res = s->image;
        break;
    case R_GRAFX:
        res = s->grafx;
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

    value &= ~PASSWORD_MASK;

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
    case R_IMAGE:
        s->image = bcm2835_powermgt_complete_domain(value,
                                                    V_IMAGE_WRITABLE);
        break;
    case R_GRAFX:
        s->grafx = bcm2835_powermgt_complete_domain(value,
                                                    V_GRAFX_WRITABLE);
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
    .version_id = 3,
    .minimum_version_id = 1,
    .post_load = bcm2835_powermgt_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32(rstc, BCM2835PowerMgtState),
        VMSTATE_UINT32(rsts, BCM2835PowerMgtState),
        VMSTATE_UINT32(wdog, BCM2835PowerMgtState),
        VMSTATE_UINT32_V(proc, BCM2835PowerMgtState, 2),
        VMSTATE_BOOL_V(arm_powered, BCM2835PowerMgtState, 2),
        VMSTATE_UINT32_V(image, BCM2835PowerMgtState, 3),
        VMSTATE_UINT32_V(grafx, BCM2835PowerMgtState, 3),
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

    /* Broadcom cpr_powman.h reset values. */
    s->rstc = 0x00000102;
    s->rsts = 0x00001000;
    s->wdog = 0x00000000;
    s->image = V_IMAGE_RESET;
    s->grafx = V_GRAFX_RESET;
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
