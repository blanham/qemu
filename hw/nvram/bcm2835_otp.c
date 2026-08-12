/*
 * BCM2835 One-Time Programmable (OTP) Memory
 *
 * The row array is also accessed directly by peripherals such as the
 * firmware-property mailbox.  The register interface below models the
 * synchronous command handshake used by the VideoCore boot firmware.
 * Irreversible OTP programming commands remain deliberately unsupported.
 *
 * Copyright (c) 2024 Rayhan Faizel <rayhan.faizel@gmail.com>
 *
 * SPDX-License-Identifier: MIT
 */

#include "qemu/osdep.h"
#include "qemu/log.h"
#include "hw/nvram/bcm2835_otp.h"
#include "migration/vmstate.h"

#define BCM2835_OTP_CTRL_LO_START          BIT(0)
#define BCM2835_OTP_STATUS_BUSY            BIT(0)
#define BCM2835_OTP_CONFIG_MASK            0x7
#define BCM2835_OTP_CTRL_HI_MASK           0xffff
#define BCM2835_OTP_BITSEL_MASK            0x1f

/* OTP rows are 1-indexed */
uint32_t bcm2835_otp_get_row(BCM2835OTPState *s, unsigned int row)
{
    assert(row <= BCM2835_OTP_ROW_COUNT && row >= 1);

    return s->otp_rows[row - 1];
}

void bcm2835_otp_set_row(BCM2835OTPState *s, unsigned int row,
                         uint32_t value)
{
    assert(row <= BCM2835_OTP_ROW_COUNT && row >= 1);

    /* Real OTP rows work as e-fuses */
    s->otp_rows[row - 1] |= value;
}

static uint32_t bcm2835_otp_selected_row(BCM2835OTPState *s)
{
    if (s->addr < 1 || s->addr > BCM2835_OTP_ROW_COUNT) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2835_otp: row address %" PRIu32
                      " is outside 1..%u\n",
                      s->addr, BCM2835_OTP_ROW_COUNT);
        return 0;
    }

    return bcm2835_otp_get_row(s, s->addr);
}

static void bcm2835_otp_complete_command(BCM2835OTPState *s)
{
    uint32_t command = s->ctrl_lo & ~BCM2835_OTP_CTRL_LO_START;

    /*
     * STATUS bit 0 is an active-high command-busy indication.  The physical
     * controller exposes the pulse long enough for asynchronous polling;
     * commands complete before the next guest-visible load in this model.
     */
    s->status |= BCM2835_OTP_STATUS_BUSY;

    switch (command) {
    case 0:
        /* Read the selected row into the CPU-visible data latch. */
        s->data = bcm2835_otp_selected_row(s);
        break;
    default:
        /*
         * Complete unsupported programming commands without changing any
         * e-fuses.  This keeps firmware polling finite while preserving the
         * read-only safety boundary of the model.
         */
        qemu_log_mask(LOG_UNIMP,
                      "bcm2835_otp: command 0x%08" PRIx32
                      " for row %" PRIu32 " is not implemented\n",
                      command, s->addr);
        break;
    }

    s->status &= ~BCM2835_OTP_STATUS_BUSY;
}

static uint64_t bcm2835_otp_read(void *opaque, hwaddr addr, unsigned size)
{
    BCM2835OTPState *s = opaque;

    switch (addr) {
    case BCM2835_OTP_BOOTMODE_REG:
        return s->bootmode;
    case BCM2835_OTP_CONFIG_REG:
        return s->config;
    case BCM2835_OTP_CTRL_LO_REG:
        return s->ctrl_lo;
    case BCM2835_OTP_CTRL_HI_REG:
        return s->ctrl_hi;
    case BCM2835_OTP_STATUS_REG:
        return s->status;
    case BCM2835_OTP_BITSEL_REG:
        return s->bitsel;
    case BCM2835_OTP_DATA_REG:
        return s->data;
    case BCM2835_OTP_ADDR_REG:
        return s->addr;
    case BCM2835_OTP_WRITE_DATA_READ_REG:
        return s->write_data_read;
    case BCM2835_OTP_INIT_STATUS_REG:
        return s->init_status;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: Bad offset 0x%" HWADDR_PRIx "\n", __func__, addr);
        return 0;
    }
}

static void bcm2835_otp_write(void *opaque, hwaddr addr,
                              uint64_t value, unsigned int size)
{
    BCM2835OTPState *s = opaque;
    uint32_t val = value;

    switch (addr) {
    case BCM2835_OTP_BOOTMODE_REG:
        s->bootmode = val;
        break;
    case BCM2835_OTP_CONFIG_REG:
        s->config = val & BCM2835_OTP_CONFIG_MASK;
        break;
    case BCM2835_OTP_CTRL_LO_REG:
    {
        bool old_start = s->ctrl_lo & BCM2835_OTP_CTRL_LO_START;
        bool new_start = val & BCM2835_OTP_CTRL_LO_START;

        s->ctrl_lo = val;
        if (!new_start) {
            /* START-clear is the idle phase of the firmware handshake. */
            s->status &= ~BCM2835_OTP_STATUS_BUSY;
        } else if (!old_start) {
            /* The command latch is edge-triggered by START. */
            bcm2835_otp_complete_command(s);
        }
        break;
    }
    case BCM2835_OTP_CTRL_HI_REG:
        s->ctrl_hi = val & BCM2835_OTP_CTRL_HI_MASK;
        break;
    case BCM2835_OTP_STATUS_REG:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2835_otp: write to read-only status register\n");
        break;
    case BCM2835_OTP_BITSEL_REG:
        s->bitsel = val & BCM2835_OTP_BITSEL_MASK;
        break;
    case BCM2835_OTP_DATA_REG:
        s->data = val;
        break;
    case BCM2835_OTP_ADDR_REG:
        /* Rows above 31 exist, so do not apply the stale public 5-bit mask. */
        s->addr = val;
        break;
    case BCM2835_OTP_WRITE_DATA_READ_REG:
        s->write_data_read = val;
        break;
    case BCM2835_OTP_INIT_STATUS_REG:
        s->init_status = val;
        break;
    default:
        qemu_log_mask(LOG_GUEST_ERROR,
                      "%s: Bad offset 0x%" HWADDR_PRIx "\n", __func__, addr);
        break;
    }
}

static const MemoryRegionOps bcm2835_otp_ops = {
    .read = bcm2835_otp_read,
    .write = bcm2835_otp_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .impl = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
    .valid = {
        .min_access_size = 4,
        .max_access_size = 4,
    },
};

static void bcm2835_otp_reset(DeviceState *dev)
{
    BCM2835OTPState *s = BCM2835_OTP(dev);

    s->bootmode = 0;
    s->config = 0;
    s->ctrl_lo = 0;
    s->ctrl_hi = 0;
    s->status = 0;
    s->bitsel = 0;
    s->data = 0;
    s->addr = 0;
    s->write_data_read = 0;
    s->init_status = 0;
}

static void bcm2835_otp_realize(DeviceState *dev, Error **errp)
{
    BCM2835OTPState *s = BCM2835_OTP(dev);

    memory_region_init_io(&s->iomem, OBJECT(dev), &bcm2835_otp_ops, s,
                          TYPE_BCM2835_OTP, 0x80);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->iomem);

    memset(s->otp_rows, 0x00, sizeof(s->otp_rows));
    bcm2835_otp_reset(dev);
}

static const VMStateDescription vmstate_bcm2835_otp = {
    .name = TYPE_BCM2835_OTP,
    .version_id = 2,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(otp_rows, BCM2835OTPState,
                             BCM2835_OTP_ROW_COUNT),
        VMSTATE_UINT32_V(bootmode, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(config, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(ctrl_lo, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(ctrl_hi, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(status, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(bitsel, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(data, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(addr, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(write_data_read, BCM2835OTPState, 2),
        VMSTATE_UINT32_V(init_status, BCM2835OTPState, 2),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_otp_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_otp_realize;
    dc->vmsd = &vmstate_bcm2835_otp;
    device_class_set_legacy_reset(dc, bcm2835_otp_reset);
}

static const TypeInfo bcm2835_otp_info = {
    .name = TYPE_BCM2835_OTP,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835OTPState),
    .class_init = bcm2835_otp_class_init,
};

static void bcm2835_otp_register_types(void)
{
    type_register_static(&bcm2835_otp_info);
}

type_init(bcm2835_otp_register_types)
