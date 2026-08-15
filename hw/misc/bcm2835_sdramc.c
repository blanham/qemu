/*
 * BCM2835 SDRAM controller and PHY initialization model
 *
 * This models the synchronous firmware-visible completion contract used
 * while bootcode.bin brings LPDDR2 online.  Timing, signal integrity, and
 * memory-cell behavior remain outside this device; QEMU's RAM region supplies
 * the initialized memory once the controller reports ready.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/misc/bcm2835_sdramc.h"
#include "migration/vmstate.h"

#define SD_CS                       0x00
#define SD_MR                       0x90

#define SD_CS_RESTART               (1u << 0)
#define SD_CS_ENABLE                (1u << 1)
#define SD_CS_STANDBY               (1u << 3)
#define SD_CS_STOP                  (1u << 7)
#define SD_CS_UP                    (1u << 15)

#define SD_MR_READ_DATA_MASK        (0xffu << 16)
#define SD_MR_READ_DATA_SHIFT       16
#define SD_MR_WRITE                 (1u << 28)
#define SD_MR_TIMEOUT               (1u << 30)
#define SD_MR_DONE                  (1u << 31)

#define APHY_ADDR_DLL_RESET         0x04
#define APHY_ADDR_DLL_LOCK_STATUS   0x20
#define APHY_DDR_PLL_GLOBAL_RESET   0x24
#define APHY_DDR_PLL_LOCK_STATUS    0x48
#define APHY_DDR_PLL_POWERDOWN      0x58
#define APHY_ADDR_PVT_CTRL          0x70
#define APHY_ADDR_PVT_STATUS        0x78

#define DPHY_DQ_DLL_RESET           0x04
#define DPHY_MASTER_DLL_LOCK_STATUS 0x18
#define DPHY_DQ_PVT_CTRL            0x54
#define DPHY_DQ_PVT_STATUS          0x5c

#define APHY_ADDR_DLL_LOCKED        0x00000003u
#define APHY_DDR_PLL_LOCKED         (1u << 16)
#define PHY_PVT_CALIBRATED          (1u << 1)
#define DPHY_MASTER_DLL_LOCKED      0x0000ffffu

#define LPDDR2_MR_MANUFACTURER_ID   5
#define LPDDR2_MR_BASIC_CONFIG      8

static unsigned bcm2835_sdramc_reg_index(hwaddr addr)
{
    g_assert(addr < BCM2835_SDRAMC_WINDOW_SIZE);
    g_assert((addr & 3) == 0);
    return addr >> 2;
}

static uint64_t bcm2835_sdramc_ctrl_read(void *opaque, hwaddr addr,
                                        unsigned size)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(opaque);

    return s->ctrl_regs[bcm2835_sdramc_reg_index(addr)];
}

static void bcm2835_sdramc_ctrl_write(void *opaque, hwaddr addr,
                                     uint64_t value, unsigned size)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(opaque);
    unsigned index = bcm2835_sdramc_reg_index(addr);
    uint32_t v = value;

    switch (addr) {
    case SD_CS:
        /*
         * The behavioral model completes controller start/stop
         * synchronously.  SDUP follows the enabled, non-quiescent state.
         */
        if ((v & SD_CS_ENABLE) && !(v & (SD_CS_STANDBY | SD_CS_STOP))) {
            v |= SD_CS_UP;
        } else {
            v &= ~SD_CS_UP;
        }
        s->ctrl_regs[index] = v;
        break;

    case SD_MR: {
        unsigned mr = v & 0xff;
        unsigned data;

        if (v & SD_MR_WRITE) {
            data = (v >> 8) & 0xff;
            s->mode_regs[mr] = data;
        } else {
            data = s->mode_regs[mr];
        }

        s->ctrl_regs[index] =
            (v & ~(SD_MR_READ_DATA_MASK | SD_MR_TIMEOUT)) |
            SD_MR_DONE |
            (data << SD_MR_READ_DATA_SHIFT);
        break;
    }

    default:
        s->ctrl_regs[index] = v;
        break;
    }
}

static uint64_t bcm2835_sdramc_aphy_read(void *opaque, hwaddr addr,
                                        unsigned size)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(opaque);

    switch (addr) {
    case APHY_ADDR_DLL_LOCK_STATUS:
        return (s->aphy_regs[APHY_ADDR_DLL_RESET >> 2] & 1) ?
            0 : APHY_ADDR_DLL_LOCKED;

    case APHY_DDR_PLL_LOCK_STATUS:
        return ((s->aphy_regs[APHY_DDR_PLL_GLOBAL_RESET >> 2] & 1) &&
                !(s->aphy_regs[APHY_DDR_PLL_POWERDOWN >> 2] & 1)) ?
            APHY_DDR_PLL_LOCKED : 0;

    case APHY_ADDR_PVT_STATUS:
        return (s->aphy_regs[APHY_ADDR_PVT_CTRL >> 2] & 1) ?
            PHY_PVT_CALIBRATED : 0;

    default:
        return s->aphy_regs[bcm2835_sdramc_reg_index(addr)];
    }
}

static void bcm2835_sdramc_aphy_write(void *opaque, hwaddr addr,
                                     uint64_t value, unsigned size)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(opaque);

    s->aphy_regs[bcm2835_sdramc_reg_index(addr)] = value;
}

static uint64_t bcm2835_sdramc_dphy_read(void *opaque, hwaddr addr,
                                        unsigned size)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(opaque);

    switch (addr) {
    case DPHY_MASTER_DLL_LOCK_STATUS:
        return (s->dphy_regs[DPHY_DQ_DLL_RESET >> 2] & 1) ?
            0 : DPHY_MASTER_DLL_LOCKED;

    case DPHY_DQ_PVT_STATUS:
        return (s->dphy_regs[DPHY_DQ_PVT_CTRL >> 2] & 1) ?
            PHY_PVT_CALIBRATED : 0;

    default:
        return s->dphy_regs[bcm2835_sdramc_reg_index(addr)];
    }
}

static void bcm2835_sdramc_dphy_write(void *opaque, hwaddr addr,
                                     uint64_t value, unsigned size)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(opaque);

    s->dphy_regs[bcm2835_sdramc_reg_index(addr)] = value;
}

static const MemoryRegionOps bcm2835_sdramc_ctrl_ops = {
    .read = bcm2835_sdramc_ctrl_read,
    .write = bcm2835_sdramc_ctrl_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static const MemoryRegionOps bcm2835_sdramc_aphy_ops = {
    .read = bcm2835_sdramc_aphy_read,
    .write = bcm2835_sdramc_aphy_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static const MemoryRegionOps bcm2835_sdramc_dphy_ops = {
    .read = bcm2835_sdramc_dphy_read,
    .write = bcm2835_sdramc_dphy_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_sdramc_reset(DeviceState *dev)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(dev);

    memset(s->ctrl_regs, 0, sizeof(s->ctrl_regs));
    memset(s->aphy_regs, 0, sizeof(s->aphy_regs));
    memset(s->dphy_regs, 0, sizeof(s->dphy_regs));
    memset(s->mode_regs, 0, sizeof(s->mode_regs));

    /*
     * The Pi 3 machine is fixed at 1 GiB.  Return a valid LPDDR2 identity
     * and the MR8 density/configuration byte for that capacity.
     */
    s->mode_regs[LPDDR2_MR_MANUFACTURER_ID] = 6;  /* Hynix */
    s->mode_regs[LPDDR2_MR_BASIC_CONFIG] = 0x58; /* 1 GiB LPDDR2 */
    s->ctrl_regs[SD_MR >> 2] = SD_MR_DONE;
}

static void bcm2835_sdramc_realize(DeviceState *dev, Error **errp)
{
    BCM2835SdramcState *s = BCM2835_SDRAMC(dev);

    memory_region_init_io(&s->ctrl_iomem, OBJECT(s),
                          &bcm2835_sdramc_ctrl_ops, s,
                          "bcm2835-sdramc", BCM2835_SDRAMC_WINDOW_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->ctrl_iomem);

    memory_region_init_io(&s->aphy_iomem, OBJECT(s),
                          &bcm2835_sdramc_aphy_ops, s,
                          "bcm2835-sdramc-aphy",
                          BCM2835_SDRAMC_WINDOW_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->aphy_iomem);

    memory_region_init_io(&s->dphy_iomem, OBJECT(s),
                          &bcm2835_sdramc_dphy_ops, s,
                          "bcm2835-sdramc-dphy",
                          BCM2835_SDRAMC_WINDOW_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->dphy_iomem);
}

static const VMStateDescription bcm2835_sdramc_vmstate = {
    .name = TYPE_BCM2835_SDRAMC,
    .version_id = 1,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(ctrl_regs, BCM2835SdramcState,
                             BCM2835_SDRAMC_REG_COUNT),
        VMSTATE_UINT32_ARRAY(aphy_regs, BCM2835SdramcState,
                             BCM2835_SDRAMC_REG_COUNT),
        VMSTATE_UINT32_ARRAY(dphy_regs, BCM2835SdramcState,
                             BCM2835_SDRAMC_REG_COUNT),
        VMSTATE_UINT8_ARRAY(mode_regs, BCM2835SdramcState, 256),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_sdramc_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_sdramc_realize;
    device_class_set_legacy_reset(dc, bcm2835_sdramc_reset);
    dc->vmsd = &bcm2835_sdramc_vmstate;
}

static const TypeInfo bcm2835_sdramc_info = {
    .name = TYPE_BCM2835_SDRAMC,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835SdramcState),
    .class_init = bcm2835_sdramc_class_init,
};

static void bcm2835_sdramc_register_types(void)
{
    type_register_static(&bcm2835_sdramc_info);
}

type_init(bcm2835_sdramc_register_types)
