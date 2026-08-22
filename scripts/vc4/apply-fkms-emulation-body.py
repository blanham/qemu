#!/usr/bin/env python3
"""Apply the BCM2835 firmware-KMS emulation slice deterministically."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    source = Path(path)
    text = source.read_text()
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{path}: expected one replacement anchor, found {count}")
    source.write_text(text.replace(old, new, 1))


replace_once(
    "include/hw/misc/bcm2835_property.h",
    '#include "hw/nvram/bcm2835_otp.h"\n#include "qom/object.h"\n',
    '#include "hw/nvram/bcm2835_otp.h"\n#include "qemu/timer.h"\n#include "qom/object.h"\n',
)

replace_once(
    "include/hw/misc/bcm2835_property.h",
    """    MemoryRegion iomem;
    qemu_irq mbox_irq;
    BCM2835FBState *fbdev;
    BCM2835OTPState *otp;

    MACAddr macaddr;
    uint32_t board_rev;
    uint32_t addr;
    uint32_t legacy_power_state;
    uint32_t power_domain_state;
    char *command_line;
    bool pending;
""",
    """    MemoryRegion iomem;
    MemoryRegion smi_iomem;
    qemu_irq mbox_irq;
    qemu_irq smi_irq;
    BCM2835FBState *fbdev;
    BCM2835OTPState *otp;
    QEMUTimer fkms_vblank_timer;

    MACAddr macaddr;
    uint32_t board_rev;
    uint32_t addr;
    uint32_t legacy_power_state;
    uint32_t power_domain_state;
    uint32_t smi_cs;
    uint32_t smi_dsw0;
    uint32_t smi_dsw1;
    uint32_t fkms_display;
    uint32_t fkms_width;
    uint32_t fkms_height;
    uint32_t fkms_refresh_hz;
    uint8_t fkms_edid[128];
    char *command_line;
    bool pending;
    bool fkms_display_power;
    bool fkms_plane_enabled;
""",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    '#include "hw/arm/raspberrypi-fw-defs.h"\n#include "system/dma.h"\n',
    '#include "hw/arm/raspberrypi-fw-defs.h"\n#include "hw/display/edid.h"\n#include "system/dma.h"\n',
)
replace_once(
    "hw/misc/bcm2835_property.c",
    '#include "qemu/module.h"\n#include "trace.h"\n',
    '#include "qemu/module.h"\n#include "qemu/timer.h"\n#include "trace.h"\n',
)

fkms_helpers = r'''
#define BCM2835_FKMS_DEFAULT_WIDTH       800
#define BCM2835_FKMS_DEFAULT_HEIGHT      480
#define BCM2835_FKMS_DEFAULT_REFRESH     60
#define BCM2835_FKMS_DEFAULT_PIXEL_CLOCK 25979
#define BCM2835_FKMS_MAX_PIXEL_CLOCK     165000000
#define BCM2835_FKMS_DISPLAY_ID_HDMI0    2
#define BCM2835_FKMS_SMI_CS              0x00
#define BCM2835_FKMS_SMI_DSW0            0x14
#define BCM2835_FKMS_SMI_DSW1            0x1c
#define BCM2835_FKMS_SMI_IRQ_MASK        \
    ((UINT32_C(1) << 9) | (UINT32_C(1) << 10) | (UINT32_C(1) << 11))

/* Selected values from the firmware's vc_image_type enumeration. */
#define BCM2835_FKMS_IMAGE_RGB565        1
#define BCM2835_FKMS_IMAGE_RGBA32        15
#define BCM2835_FKMS_IMAGE_ARGB8888      43
#define BCM2835_FKMS_IMAGE_XRGB8888      44
#define BCM2835_FKMS_IMAGE_RGBX32        49
#define BCM2835_FKMS_IMAGE_RGBX8888      50
#define BCM2835_FKMS_IMAGE_BGRX8888      51

static void bcm2835_fkms_schedule_vblank(BCM2835PropertyState *s)
{
    uint32_t refresh = s->fkms_refresh_hz;
    int64_t period;

    if (refresh == 0) {
        refresh = BCM2835_FKMS_DEFAULT_REFRESH;
    }
    period = INT64_C(1000000000) / refresh;
    timer_mod_ns(&s->fkms_vblank_timer,
                 qemu_clock_get_ns(QEMU_CLOCK_VIRTUAL) + period);
}

static void bcm2835_fkms_vblank(void *opaque)
{
    BCM2835PropertyState *s = opaque;

    if (s->fkms_display_power || s->fkms_plane_enabled) {
        /*
         * Advertise the legacy single-display callback.  The downstream
         * vc4_firmware_kms driver treats this as vblank/page-flip completion
         * for every firmware KMS CRTC.
         */
        s->smi_dsw0 = 1;
        s->smi_cs |= BCM2835_FKMS_SMI_IRQ_MASK;
        qemu_set_irq(s->smi_irq, 1);
    }
    bcm2835_fkms_schedule_vblank(s);
}

static bool bcm2835_fkms_image_format(uint8_t image_type,
                                      uint32_t *bpp, uint32_t *pixo)
{
    switch (image_type) {
    case BCM2835_FKMS_IMAGE_RGB565:
        *bpp = 16;
        *pixo = 1;
        return true;
    case BCM2835_FKMS_IMAGE_XRGB8888:
    case BCM2835_FKMS_IMAGE_ARGB8888:
        /* DRM XRGB/ARGB8888 is B,G,R,X/A in little-endian memory. */
        *bpp = 32;
        *pixo = 0;
        return true;
    case BCM2835_FKMS_IMAGE_RGBA32:
    case BCM2835_FKMS_IMAGE_RGBX32:
    case BCM2835_FKMS_IMAGE_RGBX8888:
    case BCM2835_FKMS_IMAGE_BGRX8888:
        *bpp = 32;
        *pixo = 1;
        return true;
    default:
        return false;
    }
}

static void bcm2835_fkms_apply_plane(BCM2835PropertyState *s,
                                      hwaddr payload,
                                      BCM2835FBConfig *config,
                                      bool *config_updated)
{
    uint8_t plane[60];
    uint8_t plane_id;
    uint8_t image_type;
    uint16_t width;
    uint16_t height;
    uint16_t pitch;
    uint16_t dst_width;
    uint16_t dst_height;
    uint32_t src_x;
    uint32_t src_y;
    uint32_t src_width;
    uint32_t src_height;
    uint32_t bus_addr;
    uint32_t bpp;
    uint32_t pixo;
    uint32_t bytes_per_pixel;
    uint32_t visible_width;
    uint32_t visible_height;
    uint64_t base;

    if (dma_memory_read(&s->dma_as, payload, plane, sizeof(plane),
                        MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2835_property: FKMS SET_PLANE DMA read failed\n");
        return;
    }

    plane_id = plane[1];
    image_type = plane[2];
    width = lduw_le_p(plane + 4);
    height = lduw_le_p(plane + 6);
    pitch = lduw_le_p(plane + 8);
    src_x = ldl_le_p(plane + 12) >> 16;
    src_y = ldl_le_p(plane + 16) >> 16;
    src_width = ldl_le_p(plane + 20) >> 16;
    src_height = ldl_le_p(plane + 24) >> 16;
    dst_width = lduw_le_p(plane + 32);
    dst_height = lduw_le_p(plane + 34);
    bus_addr = ldl_le_p(plane + 40);

    /* Only the primary plane drives QEMU's single display console. */
    if ((plane_id & 7) != 0) {
        return;
    }
    if (bus_addr == 0 || width == 0 || height == 0 || pitch == 0) {
        s->fkms_plane_enabled = false;
        return;
    }
    if (!bcm2835_fkms_image_format(image_type, &bpp, &pixo)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2835_property: unsupported FKMS image type %u\n",
                      image_type);
        return;
    }

    bytes_per_pixel = bpp >> 3;
    base = (uint64_t)bus_addr + (uint64_t)src_y * pitch +
           (uint64_t)src_x * bytes_per_pixel;
    if (base > UINT32_MAX) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "bcm2835_property: FKMS plane base exceeds 32 bits\n");
        return;
    }

    visible_width = dst_width ? dst_width :
                    (src_width ? src_width : width);
    visible_height = dst_height ? dst_height :
                     (src_height ? src_height : height);
    if (visible_width == 0 || visible_height == 0) {
        return;
    }

    config->xres = visible_width;
    config->yres = visible_height;
    config->xres_virtual = MAX((uint32_t)(pitch / bytes_per_pixel),
                               visible_width);
    config->yres_virtual = MAX((uint32_t)height, visible_height);
    config->xoffset = 0;
    config->yoffset = 0;
    config->bpp = bpp;
    config->base = base;
    config->pixo = pixo;
    config->alpha = 0;
    bcm2835_fb_validate_config(config);

    s->fkms_width = config->xres;
    s->fkms_height = config->yres;
    s->fkms_plane_enabled = true;
    *config_updated = true;
}

static void bcm2835_fkms_write_timing(BCM2835PropertyState *s,
                                       hwaddr payload)
{
    uint8_t timing[36] = { 0 };
    uint32_t width = s->fkms_width ?: BCM2835_FKMS_DEFAULT_WIDTH;
    uint32_t height = s->fkms_height ?: BCM2835_FKMS_DEFAULT_HEIGHT;
    uint32_t refresh = s->fkms_refresh_hz ?: BCM2835_FKMS_DEFAULT_REFRESH;

    timing[0] = BCM2835_FKMS_DISPLAY_ID_HDMI0;
    stl_le_p(timing + 4, BCM2835_FKMS_DEFAULT_PIXEL_CLOCK);
    stw_le_p(timing + 8, width);
    stw_le_p(timing + 10, width + 1);
    stw_le_p(timing + 12, width + 3);
    stw_le_p(timing + 14, width + 49);
    stw_le_p(timing + 18, height);
    stw_le_p(timing + 20, height + 7);
    stw_le_p(timing + 22, height + 9);
    stw_le_p(timing + 24, height + 30);
    stw_le_p(timing + 28, refresh);

    dma_memory_write(&s->dma_as, payload, timing, sizeof(timing),
                     MEMTXATTRS_UNSPECIFIED);
}

static void bcm2835_fkms_read_timing(BCM2835PropertyState *s,
                                      hwaddr payload,
                                      BCM2835FBConfig *config,
                                      bool *config_updated)
{
    uint8_t timing[36];
    uint32_t width;
    uint32_t height;
    uint32_t refresh;

    if (dma_memory_read(&s->dma_as, payload, timing, sizeof(timing),
                        MEMTXATTRS_UNSPECIFIED) != MEMTX_OK) {
        return;
    }
    width = lduw_le_p(timing + 8);
    height = lduw_le_p(timing + 18);
    refresh = lduw_le_p(timing + 28);
    if (width != 0 && height != 0) {
        s->fkms_width = width;
        s->fkms_height = height;
        config->xres = width;
        config->yres = height;
        config->xres_virtual = MAX(config->xres_virtual, width);
        config->yres_virtual = MAX(config->yres_virtual, height);
        bcm2835_fb_validate_config(config);
        *config_updated = true;
    }
    if (refresh != 0) {
        s->fkms_refresh_hz = refresh;
    }
}

'''

replace_once(
    "hw/misc/bcm2835_property.c",
    "static void bcm2835_property_mbox_push(BCM2835PropertyState *s, uint32_t value)\n",
    fkms_helpers +
    "static void bcm2835_property_mbox_push(BCM2835PropertyState *s, uint32_t value)\n",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    """        case RPI_FWREQ_FRAMEBUFFER_BLANK:
            resplen = 4;
            break;
""",
    """        case RPI_FWREQ_FRAMEBUFFER_BLANK:
            s->fkms_display_power =
                !ldl_le_phys(&s->dma_as, value + 12);
            resplen = 4;
            break;
""",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    """        case RPI_FWREQ_FRAMEBUFFER_GET_NUM_DISPLAYS:
            stl_le_phys(&s->dma_as, value + 12, 1);
            resplen = 4;
            break;

        case RPI_FWREQ_GET_DMA_CHANNELS:
""",
    """        case RPI_FWREQ_FRAMEBUFFER_GET_NUM_DISPLAYS:
            stl_le_phys(&s->dma_as, value + 12, 1);
            resplen = 4;
            break;
        case RPI_FWREQ_FRAMEBUFFER_SET_DISPLAY_NUM:
            s->fkms_display = ldl_le_phys(&s->dma_as, value + 12);
            resplen = 4;
            break;
        case RPI_FWREQ_FRAMEBUFFER_GET_DISPLAY_ID:
            stl_le_phys(&s->dma_as, value + 12,
                        BCM2835_FKMS_DISPLAY_ID_HDMI0);
            resplen = 4;
            break;
        case RPI_FWREQ_GET_DISPLAY_CFG:
            stl_le_phys(&s->dma_as, value + 12,
                        BCM2835_FKMS_MAX_PIXEL_CLOCK);
            stl_le_phys(&s->dma_as, value + 16,
                        BCM2835_FKMS_MAX_PIXEL_CLOCK);
            resplen = 8;
            break;
        case RPI_FWREQ_GET_DISPLAY_TIMING:
            bcm2835_fkms_write_timing(s, value + 12);
            resplen = 36;
            break;
        case RPI_FWREQ_SET_TIMING:
            bcm2835_fkms_read_timing(s, value + 12, &fbconfig,
                                     &fbconfig_updated);
            resplen = 36;
            break;
        case RPI_FWREQ_SET_DISPLAY_POWER:
            s->fkms_display_power =
                !!ldl_le_phys(&s->dma_as, value + 16);
            resplen = 8;
            break;
        case RPI_FWREQ_GET_EDID_BLOCK_DISPLAY:
        {
            uint32_t block = ldl_le_phys(&s->dma_as, value + 12);
            uint8_t edid[128] = { 0 };

            if (block == 0) {
                memcpy(edid, s->fkms_edid, sizeof(edid));
            }
            dma_memory_write(&s->dma_as, value + 20, edid, sizeof(edid),
                             MEMTXATTRS_UNSPECIFIED);
            resplen = 136;
            break;
        }
        case RPI_FWREQ_SET_PLANE:
            bcm2835_fkms_apply_plane(s, value + 12, &fbconfig,
                                      &fbconfig_updated);
            resplen = 60;
            break;

        case RPI_FWREQ_GET_DMA_CHANNELS:
""",
)

smi_support = r'''
static uint64_t bcm2835_fkms_smi_read(void *opaque, hwaddr offset,
                                      unsigned size)
{
    BCM2835PropertyState *s = opaque;

    switch (offset) {
    case BCM2835_FKMS_SMI_CS:
        return s->smi_cs;
    case BCM2835_FKMS_SMI_DSW0:
        return s->smi_dsw0;
    case BCM2835_FKMS_SMI_DSW1:
        return s->smi_dsw1;
    default:
        return 0;
    }
}

static void bcm2835_fkms_smi_write(void *opaque, hwaddr offset,
                                    uint64_t value, unsigned size)
{
    BCM2835PropertyState *s = opaque;

    switch (offset) {
    case BCM2835_FKMS_SMI_CS:
        s->smi_cs = value & BCM2835_FKMS_SMI_IRQ_MASK;
        qemu_set_irq(s->smi_irq,
                     !!(s->smi_cs & BCM2835_FKMS_SMI_IRQ_MASK));
        break;
    case BCM2835_FKMS_SMI_DSW0:
        s->smi_dsw0 = value;
        break;
    case BCM2835_FKMS_SMI_DSW1:
        s->smi_dsw1 = value;
        break;
    default:
        break;
    }
}

static const MemoryRegionOps bcm2835_fkms_smi_ops = {
    .read = bcm2835_fkms_smi_read,
    .write = bcm2835_fkms_smi_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
};

static int bcm2835_property_post_load(void *opaque, int version_id)
{
    BCM2835PropertyState *s = opaque;

    qemu_set_irq(s->smi_irq,
                 !!(s->smi_cs & BCM2835_FKMS_SMI_IRQ_MASK));
    bcm2835_fkms_schedule_vblank(s);
    return 0;
}

'''
replace_once(
    "hw/misc/bcm2835_property.c",
    "static const VMStateDescription vmstate_bcm2835_property = {\n",
    smi_support + "static const VMStateDescription vmstate_bcm2835_property = {\n",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    """static const VMStateDescription vmstate_bcm2835_property = {
    .name = TYPE_BCM2835_PROPERTY,
    .version_id = 2,
    .minimum_version_id = 1,
    .fields = (const VMStateField[]) {
        VMSTATE_MACADDR(macaddr, BCM2835PropertyState),
        VMSTATE_UINT32(addr, BCM2835PropertyState),
        VMSTATE_UINT32_V(legacy_power_state, BCM2835PropertyState, 2),
        VMSTATE_UINT32_V(power_domain_state, BCM2835PropertyState, 2),
        VMSTATE_BOOL(pending, BCM2835PropertyState),
        VMSTATE_END_OF_LIST()
    }
};
""",
    """static const VMStateDescription vmstate_bcm2835_property = {
    .name = TYPE_BCM2835_PROPERTY,
    .version_id = 3,
    .minimum_version_id = 1,
    .post_load = bcm2835_property_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_MACADDR(macaddr, BCM2835PropertyState),
        VMSTATE_UINT32(addr, BCM2835PropertyState),
        VMSTATE_UINT32_V(legacy_power_state, BCM2835PropertyState, 2),
        VMSTATE_UINT32_V(power_domain_state, BCM2835PropertyState, 2),
        VMSTATE_UINT32_V(smi_cs, BCM2835PropertyState, 3),
        VMSTATE_UINT32_V(smi_dsw0, BCM2835PropertyState, 3),
        VMSTATE_UINT32_V(smi_dsw1, BCM2835PropertyState, 3),
        VMSTATE_UINT32_V(fkms_display, BCM2835PropertyState, 3),
        VMSTATE_UINT32_V(fkms_width, BCM2835PropertyState, 3),
        VMSTATE_UINT32_V(fkms_height, BCM2835PropertyState, 3),
        VMSTATE_UINT32_V(fkms_refresh_hz, BCM2835PropertyState, 3),
        VMSTATE_BOOL_V(fkms_display_power, BCM2835PropertyState, 3),
        VMSTATE_BOOL_V(fkms_plane_enabled, BCM2835PropertyState, 3),
        VMSTATE_BOOL(pending, BCM2835PropertyState),
        VMSTATE_END_OF_LIST()
    }
};
""",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    """    memory_region_init_io(&s->iomem, OBJECT(s), &bcm2835_property_ops, s,
                          TYPE_BCM2835_PROPERTY, 0x10);

    /*
""",
    """    memory_region_init_io(&s->iomem, OBJECT(s), &bcm2835_property_ops, s,
                          TYPE_BCM2835_PROPERTY, 0x10);
    memory_region_init_io(&s->smi_iomem, OBJECT(s), &bcm2835_fkms_smi_ops, s,
                          TYPE_BCM2835_PROPERTY "-smi", 0x100);
    timer_init_ns(&s->fkms_vblank_timer, QEMU_CLOCK_VIRTUAL,
                  bcm2835_fkms_vblank, s);

    /*
""",
)
replace_once(
    "hw/misc/bcm2835_property.c",
    """    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->mbox_irq);
""",
    """    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->smi_iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->mbox_irq);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->smi_irq);
""",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    """    s->pending = false;
    s->legacy_power_state = 0;
    s->power_domain_state = 0;
""",
    """    timer_del(&s->fkms_vblank_timer);
    s->pending = false;
    s->legacy_power_state = 0;
    s->power_domain_state = 0;
    s->smi_cs = 0;
    s->smi_dsw0 = 0;
    s->smi_dsw1 = 0;
    s->fkms_display = 0;
    s->fkms_width = BCM2835_FKMS_DEFAULT_WIDTH;
    s->fkms_height = BCM2835_FKMS_DEFAULT_HEIGHT;
    s->fkms_refresh_hz = BCM2835_FKMS_DEFAULT_REFRESH;
    s->fkms_display_power = false;
    s->fkms_plane_enabled = false;
    qemu_set_irq(s->smi_irq, 0);
    bcm2835_fkms_schedule_vblank(s);
""",
)

replace_once(
    "hw/misc/bcm2835_property.c",
    """    BCM2835PropertyState *s = BCM2835_PROPERTY(dev);
    Object *obj;
""",
    """    BCM2835PropertyState *s = BCM2835_PROPERTY(dev);
    qemu_edid_info edid_info = {
        .vendor = "QEM",
        .name = "VC4-FKMS",
        .serial = "1",
        .width_mm = 154,
        .height_mm = 86,
        .prefx = BCM2835_FKMS_DEFAULT_WIDTH,
        .prefy = BCM2835_FKMS_DEFAULT_HEIGHT,
        .maxx = BCM2835_FKMS_DEFAULT_WIDTH,
        .maxy = BCM2835_FKMS_DEFAULT_HEIGHT,
        .refresh_rate = BCM2835_FKMS_DEFAULT_REFRESH,
    };
    Object *obj;
""",
)
replace_once(
    "hw/misc/bcm2835_property.c",
    """    obj = object_property_get_link(OBJECT(dev), "otp", &error_abort);
    s->otp = BCM2835_OTP(obj);

    /* TODO: connect to MAC address of USB NIC device, once we emulate it */
""",
    """    obj = object_property_get_link(OBJECT(dev), "otp", &error_abort);
    s->otp = BCM2835_OTP(obj);

    qemu_edid_generate(s->fkms_edid, sizeof(s->fkms_edid), &edid_info);

    /* TODO: connect to MAC address of USB NIC device, once we emulate it */
""",
)

replace_once(
    "hw/arm/bcm2835_peripherals.c",
    """    sysbus_connect_irq(SYS_BUS_DEVICE(&s->property), 0,
                      qdev_get_gpio_in(DEVICE(&s->mboxes), MBOX_CHAN_PROPERTY));

    /* Extended Mass Media Controller
""",
    """    sysbus_connect_irq(SYS_BUS_DEVICE(&s->property), 0,
                      qdev_get_gpio_in(DEVICE(&s->mboxes), MBOX_CHAN_PROPERTY));

    /* Firmware KMS SMI callback registers and vblank interrupt. */
    memory_region_add_subregion(
        &s->peri_mr, SMI_OFFSET,
        sysbus_mmio_get_region(SYS_BUS_DEVICE(&s->property), 1));
    sysbus_connect_irq(
        SYS_BUS_DEVICE(&s->property), 1,
        qdev_get_gpio_in_named(DEVICE(&s->ic),
                               BCM2835_IC_GPU_IRQ,
                               INTERRUPT_SMI));

    /* Extended Mass Media Controller
""",
)
replace_once(
    "hw/arm/bcm2835_peripherals.c",
    '    create_unimp(s, &s->smi, "bcm2835-smi", SMI_OFFSET, 0x100);\n',
    "",
)

for path, token in (
    ("hw/misc/bcm2835_property.c", "RPI_FWREQ_SET_PLANE"),
    ("hw/misc/bcm2835_property.c", "bcm2835_fkms_smi_ops"),
    ("hw/arm/bcm2835_peripherals.c", "Firmware KMS SMI callback"),
    ("include/hw/misc/bcm2835_property.h", "fkms_vblank_timer"),
):
    if token not in Path(path).read_text():
        raise SystemExit(f"{path}: postcondition missing {token!r}")
