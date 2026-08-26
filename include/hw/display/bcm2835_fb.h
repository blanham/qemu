/*
 * Raspberry Pi emulation (c) 2012 Gregory Estrade
 * Upstreaming code cleanup [including bcm2835_*] (c) 2013 Jan Petrous
 *
 * Rasperry Pi 2 emulation and refactoring Copyright (c) 2015, Microsoft
 * Written by Andrew Baumann
 *
 * This work is licensed under the terms of the GNU GPL, version 2 or later.
 * See the COPYING file in the top-level directory.
 */

#ifndef BCM2835_FB_H
#define BCM2835_FB_H

#include "hw/core/sysbus.h"
#include "qom/object.h"

#define UPPER_RAM_BASE 0x40000000

#define TYPE_BCM2835_FB "bcm2835-fb"
OBJECT_DECLARE_SIMPLE_TYPE(BCM2835FBState, BCM2835_FB)

/*
 * Framebuffer source layouts.  Values 0 and 1 retain the mailbox ABI;
 * the remaining values describe HVS-only packed-RGB layouts.
 */
#define BCM2835_FB_PIXEL_ORDER_BGR           0
#define BCM2835_FB_PIXEL_ORDER_RGB           1
#define BCM2835_FB_PIXEL_ORDER_RGBA          2
#define BCM2835_FB_PIXEL_ORDER_BGRA          3
#define BCM2835_FB_PIXEL_ORDER_HVS_RGB565    4
#define BCM2835_FB_PIXEL_ORDER_HVS_BGR565    5
#define BCM2835_FB_PIXEL_ORDER_HVS_RGB332    6
#define BCM2835_FB_PIXEL_ORDER_HVS_BGR233    7
#define BCM2835_FB_PIXEL_ORDER_HVS_XRGB4444  8
#define BCM2835_FB_PIXEL_ORDER_HVS_XBGR4444  9
#define BCM2835_FB_PIXEL_ORDER_HVS_BGRX4444 10
#define BCM2835_FB_PIXEL_ORDER_HVS_RGBX4444 11
#define BCM2835_FB_PIXEL_ORDER_HVS_XRGB1555 12

/*
 * Configuration information about the fb which the guest can program
 * via the mailbox property interface.
 */
typedef struct {
    uint32_t xres, yres;
    uint32_t xres_virtual, yres_virtual;
    uint32_t xoffset, yoffset;
    uint32_t bpp;
    uint32_t base;
    uint32_t pixo;
    uint32_t alpha;
} BCM2835FBConfig;

struct BCM2835FBState {
    /*< private >*/
    SysBusDevice busdev;
    /*< public >*/

    uint32_t vcram_base, vcram_size;
    MemoryRegion *dma_mr;
    AddressSpace dma_as;
    MemoryRegion iomem;
    MemoryRegionSection fbsection;
    QemuConsole *con;
    qemu_irq mbox_irq;

    bool lock, invalidate, pending;

    BCM2835FBConfig config;
    BCM2835FBConfig initial_config;
};

void bcm2835_fb_reconfigure(BCM2835FBState *s, BCM2835FBConfig *newconfig);

/**
 * bcm2835_fb_get_pitch: return number of bytes per line of the framebuffer
 * @config: configuration info for the framebuffer
 *
 * Return the number of bytes per line of the framebuffer, ie the number
 * that must be added to a pixel address to get the address of the pixel
 * directly below it on screen.
 */
static inline uint32_t bcm2835_fb_get_pitch(BCM2835FBConfig *config)
{
    uint32_t xres = MAX(config->xres, config->xres_virtual);
    return xres * (config->bpp >> 3);
}

/**
 * bcm2835_fb_get_size: return total size of framebuffer in bytes
 * @config: configuration info for the framebuffer
 */
static inline uint32_t bcm2835_fb_get_size(BCM2835FBConfig *config)
{
    uint32_t yres = MAX(config->yres, config->yres_virtual);
    return yres * bcm2835_fb_get_pitch(config);
}

/**
 * bcm2835_fb_validate_config: check provided config
 *
 * Validates the configuration information provided by the guest and
 * adjusts it if necessary.
 */
void bcm2835_fb_validate_config(BCM2835FBConfig *config);

#endif
