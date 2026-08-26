/*
 * Raspberry Pi BCM2835 Hardware Video Scaler model
 *
 * The VC4 renderer and plane-list construction remain in the guest driver.
 * This device models the hardware-visible HVS register/DLIST window, the
 * display-list handoff needed by the CRTC vblank path, and the common native
 * KMS scanout contract: one full-screen, unity-scaled, linear packed-RGB
 * plane using a layout advertised by the Linux VC4 driver, including the
 * horizontal and vertical reflection bits programmed in the display list.
 * Unsupported compositions remain visible to the guest as programmed but do
 * not silently receive an incorrect software rendering approximation.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/display/bcm2835_fb.h"
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
#define SCALER_DLIST_START           0x2000

#define SCALER_CHANNEL_COUNT         3
#define SCALER_LIST_STRIDE           0x4
#define SCALER_CHANNEL_STRIDE        0x10

#define SCALER_DISPCTRLX_ENABLE      BIT(31)
#define SCALER_DISPCTRLX_RESET       BIT(30)
#define SCALER_DISPCTRLX_WIDTH_SHIFT 12
#define SCALER_DISPCTRLX_WIDTH_MASK  UINT32_C(0xfff)
#define SCALER_DISPCTRLX_HEIGHT_MASK UINT32_C(0xfff)

#define SCALER_DISPSTATX_MODE_SHIFT  30
#define SCALER_DISPSTATX_MODE_RUN    UINT32_C(2)
#define SCALER_DISPSTATX_FULL        BIT(29)
#define SCALER_DISPSTATX_EMPTY       BIT(28)

#define SCALER_CTL0_END              BIT(31)
#define SCALER_CTL0_VALID            BIT(30)
#define SCALER_CTL0_SIZE_SHIFT       24
#define SCALER_CTL0_SIZE_MASK        UINT32_C(0x3f)
#define SCALER_CTL0_TILING_SHIFT     20
#define SCALER_CTL0_TILING_MASK      UINT32_C(0x3)
#define SCALER_CTL0_TILING_LINEAR    UINT32_C(0)
#define SCALER_CTL0_HFLIP            BIT(16)
#define SCALER_CTL0_VFLIP            BIT(15)
#define SCALER_CTL0_ORDER_SHIFT      13
#define SCALER_CTL0_ORDER_MASK       UINT32_C(0x3)
#define SCALER_CTL0_RGBA_EXPAND_SHIFT 11
#define SCALER_CTL0_RGBA_EXPAND_MASK UINT32_C(0x3)
#define SCALER_CTL0_RGBA_EXPAND_ROUND UINT32_C(3)
#define SCALER_CTL0_UNITY            BIT(4)
#define SCALER_CTL0_FORMAT_MASK      UINT32_C(0xf)

#define HVS_PIXEL_ORDER_RGBA         UINT32_C(0)
#define HVS_PIXEL_ORDER_BGRA         UINT32_C(1)
#define HVS_PIXEL_ORDER_ARGB         UINT32_C(2)
#define HVS_PIXEL_ORDER_ABGR         UINT32_C(3)
#define HVS_PIXEL_ORDER_XRGB         UINT32_C(2)
#define HVS_PIXEL_ORDER_XBGR         UINT32_C(3)
#define HVS_PIXEL_FORMAT_RGB332      UINT32_C(0)
#define HVS_PIXEL_FORMAT_RGBA4444    UINT32_C(1)
#define HVS_PIXEL_FORMAT_RGBA5551    UINT32_C(3)
#define HVS_PIXEL_FORMAT_RGB565      UINT32_C(4)
#define HVS_PIXEL_FORMAT_RGB888      UINT32_C(5)
#define HVS_PIXEL_FORMAT_RGBA8888    UINT32_C(7)

#define SCALER_POS0_START_Y_SHIFT    12
#define SCALER_POS0_START_Y_MASK     UINT32_C(0xfff)
#define SCALER_POS0_START_X_MASK     UINT32_C(0xfff)
#define SCALER_POS2_HEIGHT_SHIFT     16
#define SCALER_POS2_HEIGHT_MASK      UINT32_C(0xfff)
#define SCALER_POS2_WIDTH_MASK       UINT32_C(0xfff)
#define SCALER_SRC_PITCH_MASK        UINT32_C(0xffff)

#define SCALER_UNITY_PLANE_WORDS      7
#define SCALER_UNITY_PLANE_POS0_WORD  1
#define SCALER_UNITY_PLANE_POS2_WORD  2
#define SCALER_UNITY_PLANE_PTR_WORD   4
#define SCALER_UNITY_PLANE_PITCH_WORD 6

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

static bool bcm2835_hvs_scanout_config(BCM2835HVSState *s,
                                       unsigned channel,
                                       BCM2835FBConfig *config)
{
    uint32_t control = s->regs[bcm2835_hvs_index(
        bcm2835_hvs_control_offset(channel))];
    uint32_t channel_width;
    uint32_t channel_height;
    uint32_t list_word;
    unsigned list_index;
    uint32_t ctl0;
    uint32_t element_words;
    uint32_t tiling;
    uint32_t order;
    uint32_t expand;
    uint32_t format;
    uint32_t bytes_per_pixel;
    uint32_t bits_per_pixel;
    uint32_t pixo;
    uint32_t pos0;
    uint32_t pos2;
    uint32_t width;
    uint32_t height;
    uint32_t pitch;
    uint32_t transform;
    uint32_t base;
    uint64_t last_row_offset;

    if (!(control & SCALER_DISPCTRLX_ENABLE) || !s->fb) {
        return false;
    }

    channel_width = (control >> SCALER_DISPCTRLX_WIDTH_SHIFT) &
                    SCALER_DISPCTRLX_WIDTH_MASK;
    channel_height = control & SCALER_DISPCTRLX_HEIGHT_MASK;
    if (!channel_width || !channel_height) {
        return false;
    }

    list_word = s->regs[bcm2835_hvs_index(
        bcm2835_hvs_active_offset(channel))];
    list_index = bcm2835_hvs_index(SCALER_DLIST_START);
    if (list_word >= BCM2835_HVS_REG_WORDS - list_index) {
        return false;
    }
    list_index += list_word;

    ctl0 = s->regs[list_index];
    element_words = (ctl0 >> SCALER_CTL0_SIZE_SHIFT) &
                    SCALER_CTL0_SIZE_MASK;
    tiling = (ctl0 >> SCALER_CTL0_TILING_SHIFT) &
             SCALER_CTL0_TILING_MASK;
    order = (ctl0 >> SCALER_CTL0_ORDER_SHIFT) &
            SCALER_CTL0_ORDER_MASK;
    expand = (ctl0 >> SCALER_CTL0_RGBA_EXPAND_SHIFT) &
             SCALER_CTL0_RGBA_EXPAND_MASK;
    format = ctl0 & SCALER_CTL0_FORMAT_MASK;

    if ((ctl0 & (SCALER_CTL0_END | SCALER_CTL0_VALID)) !=
        SCALER_CTL0_VALID ||
        element_words != SCALER_UNITY_PLANE_WORDS ||
        list_index + element_words >= BCM2835_HVS_REG_WORDS ||
        !(s->regs[list_index + element_words] & SCALER_CTL0_END) ||
        tiling != SCALER_CTL0_TILING_LINEAR ||
        !(ctl0 & SCALER_CTL0_UNITY)) {
        return false;
    }

    switch (format) {
    case HVS_PIXEL_FORMAT_RGB332:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND ||
            (order != HVS_PIXEL_ORDER_ARGB &&
             order != HVS_PIXEL_ORDER_ABGR)) {
            return false;
        }
        bytes_per_pixel = sizeof(uint8_t);
        bits_per_pixel = 8;
        pixo = order == HVS_PIXEL_ORDER_ABGR ?
               BCM2835_FB_PIXEL_ORDER_HVS_BGR233 :
               BCM2835_FB_PIXEL_ORDER_HVS_RGB332;
        break;
    case HVS_PIXEL_FORMAT_RGBA4444:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND) {
            return false;
        }
        switch (order) {
        case HVS_PIXEL_ORDER_ABGR:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_XRGB4444;
            break;
        case HVS_PIXEL_ORDER_ARGB:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_XBGR4444;
            break;
        case HVS_PIXEL_ORDER_RGBA:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_BGRX4444;
            break;
        case HVS_PIXEL_ORDER_BGRA:
            pixo = BCM2835_FB_PIXEL_ORDER_HVS_RGBX4444;
            break;
        default:
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        break;
    case HVS_PIXEL_FORMAT_RGBA5551:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND ||
            order != HVS_PIXEL_ORDER_ABGR) {
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        pixo = BCM2835_FB_PIXEL_ORDER_HVS_XRGB1555;
        break;
    case HVS_PIXEL_FORMAT_RGB565:
        if (expand != SCALER_CTL0_RGBA_EXPAND_ROUND ||
            (order != HVS_PIXEL_ORDER_XRGB &&
             order != HVS_PIXEL_ORDER_XBGR)) {
            return false;
        }
        bytes_per_pixel = sizeof(uint16_t);
        bits_per_pixel = 16;
        pixo = order == HVS_PIXEL_ORDER_XBGR ?
               BCM2835_FB_PIXEL_ORDER_HVS_BGR565 :
               BCM2835_FB_PIXEL_ORDER_HVS_RGB565;
        break;
    case HVS_PIXEL_FORMAT_RGB888:
        if (order != HVS_PIXEL_ORDER_XRGB &&
            order != HVS_PIXEL_ORDER_XBGR) {
            return false;
        }
        bytes_per_pixel = 3;
        bits_per_pixel = 24;
        /* DRM RGB888 is B,G,R in memory; BGR888 is R,G,B. */
        pixo = order == HVS_PIXEL_ORDER_XRGB ?
               BCM2835_FB_PIXEL_ORDER_BGR :
               BCM2835_FB_PIXEL_ORDER_RGB;
        break;
    case HVS_PIXEL_FORMAT_RGBA8888:
        switch (order) {
        case HVS_PIXEL_ORDER_ABGR:
            pixo = BCM2835_FB_PIXEL_ORDER_BGR;
            break;
        case HVS_PIXEL_ORDER_ARGB:
            pixo = BCM2835_FB_PIXEL_ORDER_RGB;
            break;
        case HVS_PIXEL_ORDER_RGBA:
            pixo = BCM2835_FB_PIXEL_ORDER_RGBA;
            break;
        case HVS_PIXEL_ORDER_BGRA:
            pixo = BCM2835_FB_PIXEL_ORDER_BGRA;
            break;
        default:
            return false;
        }
        bytes_per_pixel = sizeof(uint32_t);
        bits_per_pixel = 32;
        break;
    default:
        return false;
    }

    transform = 0;
    if (ctl0 & SCALER_CTL0_HFLIP) {
        transform |= BCM2835_FB_TRANSFORM_HFLIP;
    }
    if (ctl0 & SCALER_CTL0_VFLIP) {
        transform |= BCM2835_FB_TRANSFORM_VFLIP;
    }

    pos0 = s->regs[list_index + SCALER_UNITY_PLANE_POS0_WORD];
    pos2 = s->regs[list_index + SCALER_UNITY_PLANE_POS2_WORD];
    width = pos2 & SCALER_POS2_WIDTH_MASK;
    height = (pos2 >> SCALER_POS2_HEIGHT_SHIFT) &
             SCALER_POS2_HEIGHT_MASK;
    pitch = s->regs[list_index + SCALER_UNITY_PLANE_PITCH_WORD] &
            SCALER_SRC_PITCH_MASK;

    if (((pos0 >> SCALER_POS0_START_Y_SHIFT) &
         SCALER_POS0_START_Y_MASK) != 0 ||
        (pos0 & SCALER_POS0_START_X_MASK) != 0 ||
        width != channel_width || height != channel_height ||
        pitch < width * bytes_per_pixel ||
        pitch % bytes_per_pixel != 0) {
        return false;
    }

    base = s->regs[list_index + SCALER_UNITY_PLANE_PTR_WORD];
    if (transform & BCM2835_FB_TRANSFORM_VFLIP) {
        /*
         * Linux points a reflected linear element at its final source row.
         * The generic framebuffer helper walks source rows forward and
         * reflects the host destination, so normalize back to row zero.
         */
        last_row_offset = (uint64_t)(height - 1) * pitch;
        if (base < last_row_offset) {
            return false;
        }
        base -= last_row_offset;
    }

    *config = s->fb->config;
    config->xres = width;
    config->yres = height;
    config->xres_virtual = pitch / bytes_per_pixel;
    config->yres_virtual = height;
    config->xoffset = 0;
    config->yoffset = 0;
    config->bpp = bits_per_pixel;
    config->base = base;
    config->pixo = pixo;
    config->transform = transform;
    return true;
}

static void bcm2835_hvs_refresh_scanout(BCM2835HVSState *s)
{
    BCM2835FBConfig config;
    int channel;

    /* BCM2835 HDMI is fed by HVS channel 2.  Retain lower-channel support
     * for focused device tests and the other first-generation outputs.
     */
    for (channel = SCALER_CHANNEL_COUNT - 1; channel >= 0; channel--) {
        if (!bcm2835_hvs_scanout_config(s, channel, &config)) {
            continue;
        }

        if (memcmp(&s->fb->config, &config, sizeof(config)) != 0) {
            bcm2835_fb_reconfigure(s->fb, &config);
        } else {
            s->fb->invalidate = true;
        }
        return;
    }
}

static void bcm2835_hvs_vblank(void *opaque, int channel, int level)
{
    BCM2835HVSState *s = opaque;
    hwaddr control;
    hwaddr list;
    hwaddr active;

    if (!level || channel < 0 || channel >= SCALER_CHANNEL_COUNT) {
        return;
    }

    control = bcm2835_hvs_control_offset(channel);
    if (!(s->regs[bcm2835_hvs_index(control)] &
          SCALER_DISPCTRLX_ENABLE)) {
        return;
    }

    list = bcm2835_hvs_list_offset(channel);
    active = bcm2835_hvs_active_offset(channel);
    if (s->regs[bcm2835_hvs_index(active)] ==
        s->regs[bcm2835_hvs_index(list)]) {
        return;
    }

    s->regs[bcm2835_hvs_index(active)] =
        s->regs[bcm2835_hvs_index(list)];
    bcm2835_hvs_refresh_scanout(s);
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
        bcm2835_hvs_refresh_scanout(s);
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
    } else if (offset >= SCALER_DLIST_START) {
        /* Async flips can replace the active pointer in place. */
        bcm2835_hvs_refresh_scanout(s);
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
    bcm2835_hvs_refresh_scanout(s);
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

static void bcm2835_hvs_realize(DeviceState *dev, Error **errp)
{
    BCM2835HVSState *s = BCM2835_HVS(dev);
    Object *fb;

    fb = object_property_get_link(OBJECT(dev), "fb", errp);
    if (!fb) {
        return;
    }
    s->fb = BCM2835_FB(fb);
}

static void bcm2835_hvs_init(Object *obj)
{
    BCM2835HVSState *s = BCM2835_HVS(obj);

    memory_region_init_io(&s->iomem, obj, &bcm2835_hvs_ops, s,
                          TYPE_BCM2835_HVS, BCM2835_HVS_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
    qdev_init_gpio_in_named(DEVICE(s),
                            bcm2835_hvs_vblank,
                            "vblank",
                            SCALER_CHANNEL_COUNT);
}

static void bcm2835_hvs_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    device_class_set_legacy_reset(dc, bcm2835_hvs_reset);
    dc->realize = bcm2835_hvs_realize;
    dc->vmsd = &vmstate_bcm2835_hvs;
    dc->desc = "BCM2835 Hardware Video Scaler and linear scanout";
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
