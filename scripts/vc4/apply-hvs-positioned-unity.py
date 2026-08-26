#!/usr/bin/env python3
"""Apply the bounded BCM2835 HVS positioned-unity-plane tranche."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one replacement anchor, found {count}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_index = text.find(start)
    if start_index < 0:
        raise SystemExit(f"{path}: start anchor not found: {start!r}")
    if text.find(start, start_index + 1) >= 0:
        raise SystemExit(f"{path}: start anchor is not unique: {start!r}")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise SystemExit(f"{path}: end anchor not found: {end!r}")
    path.write_text(
        text[:start_index] + replacement + text[end_index:],
        encoding="utf-8",
    )


def patch_header() -> None:
    path = Path("include/hw/display/bcm2835_fb.h")
    replace_once(
        path,
        """    bool lock, invalidate, pending;

    BCM2835FBConfig config;
""",
        """    bool lock, invalidate, pending;
    bool hvs_scanout;
    uint32_t scanout_xres, scanout_yres;
    uint32_t scanout_x, scanout_y;

    BCM2835FBConfig config;
""",
    )
    replace_once(
        path,
        """void bcm2835_fb_reconfigure(BCM2835FBState *s, BCM2835FBConfig *newconfig);

/**
""",
        """void bcm2835_fb_reconfigure(BCM2835FBState *s,
                              BCM2835FBConfig *newconfig);
void bcm2835_fb_reconfigure_scanout(BCM2835FBState *s,
                                    BCM2835FBConfig *newconfig,
                                    uint32_t output_xres,
                                    uint32_t output_yres,
                                    uint32_t scanout_x,
                                    uint32_t scanout_y);

/**
""",
    )


def patch_framebuffer() -> None:
    path = Path("hw/display/bcm2835_fb.c")
    replace_between(
        path,
        "static bool fb_update_display(void *opaque)\n",
        "void bcm2835_fb_validate_config(BCM2835FBConfig *config)\n",
        """static bool fb_update_display(void *opaque)
{
    BCM2835FBState *s = opaque;
    DisplaySurface *surface = qemu_console_surface(s->con);
    DisplaySurface *draw_surface = surface;
    int first = 0;
    int last = 0;
    int src_width;
    uint32_t xoff = 0;
    uint32_t yoff = 0;
    uint32_t output_xres;
    uint32_t output_yres;
    uint32_t scanout_x;
    uint32_t scanout_y;
    bool positioned;
    bool invalidate;

    if (s->lock || !s->config.xres) {
        return true;
    }

    output_xres = s->hvs_scanout ? s->scanout_xres : s->config.xres;
    output_yres = s->hvs_scanout ? s->scanout_yres : s->config.yres;
    scanout_x = s->hvs_scanout ? s->scanout_x : 0;
    scanout_y = s->hvs_scanout ? s->scanout_y : 0;
    if (!output_xres || !output_yres ||
        surface_bits_per_pixel(surface) == 0) {
        return true;
    }

    positioned = s->hvs_scanout &&
        (scanout_x != 0 || scanout_y != 0 ||
         s->config.xres != output_xres ||
         s->config.yres != output_yres);
    invalidate = s->invalidate;
    src_width = bcm2835_fb_get_pitch(&s->config);
    if (fb_use_offsets(&s->config)) {
        xoff = s->config.xoffset;
        yoff = s->config.yoffset;
    }

    if (invalidate) {
        hwaddr base = s->config.base + xoff + (hwaddr)yoff * src_width;

        framebuffer_update_memory_section(&s->fbsection, s->dma_mr,
                                          base, s->config.yres,
                                          src_width);
        if (positioned) {
            memset(surface_data(surface), 0,
                   (size_t)surface_stride(surface) *
                   surface_height(surface));
        }
    }

    if (positioned) {
        uint8_t *dest = surface_data(surface);

        dest += (size_t)scanout_y * surface_stride(surface);
        dest += (size_t)scanout_x * surface_bytes_per_pixel(surface);
        draw_surface = qemu_create_displaysurface_from(
            s->config.xres, s->config.yres,
            surface_format(surface), surface_stride(surface), dest);
    }

    framebuffer_update_display(draw_surface, &s->fbsection,
                               s->config.xres, s->config.yres,
                               src_width, surface_stride(draw_surface),
                               0, invalidate, draw_line_src16, s,
                               &first, &last);

    if (draw_surface != surface) {
        qemu_free_displaysurface(draw_surface);
    }

    if (first >= 0) {
        if (invalidate && positioned) {
            qemu_console_update(s->con, 0, 0, output_xres, output_yres);
        } else {
            qemu_console_update(s->con, scanout_x, scanout_y + first,
                                s->config.xres, last - first + 1);
        }
    }

    s->invalidate = false;
    return true;
}

""",
    )
    replace_between(
        path,
        "void bcm2835_fb_reconfigure(BCM2835FBState *s,\n",
        "static void bcm2835_fb_mbox_push(BCM2835FBState *s, uint32_t value)\n",
        """static void bcm2835_fb_apply_config(BCM2835FBState *s,
                                     BCM2835FBConfig *newconfig,
                                     uint32_t output_xres,
                                     uint32_t output_yres,
                                     uint32_t scanout_x,
                                     uint32_t scanout_y,
                                     bool hvs_scanout)
{
    s->lock = true;
    s->config = *newconfig;
    s->hvs_scanout = hvs_scanout;
    s->scanout_xres = output_xres;
    s->scanout_yres = output_yres;
    s->scanout_x = scanout_x;
    s->scanout_y = scanout_y;
    s->invalidate = true;
    qemu_console_resize(s->con, output_xres, output_yres);
    s->lock = false;
}

void bcm2835_fb_reconfigure(BCM2835FBState *s,
                            BCM2835FBConfig *newconfig)
{
    bcm2835_fb_apply_config(s, newconfig,
                            newconfig->xres, newconfig->yres,
                            0, 0, false);
}

void bcm2835_fb_reconfigure_scanout(BCM2835FBState *s,
                                    BCM2835FBConfig *newconfig,
                                    uint32_t output_xres,
                                    uint32_t output_yres,
                                    uint32_t scanout_x,
                                    uint32_t scanout_y)
{
    if (!output_xres || !output_yres ||
        output_xres > XRES_MAX || output_yres > YRES_MAX ||
        newconfig->xres > output_xres ||
        newconfig->yres > output_yres ||
        scanout_x > output_xres - newconfig->xres ||
        scanout_y > output_yres - newconfig->yres) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_FB ": invalid HVS scanout geometry "
                      "%ux%u+%u+%u in %ux%u\n",
                      newconfig->xres, newconfig->yres,
                      scanout_x, scanout_y,
                      output_xres, output_yres);
        return;
    }

    bcm2835_fb_apply_config(s, newconfig,
                            output_xres, output_yres,
                            scanout_x, scanout_y, true);
}

""",
    )
    replace_once(
        path,
        """static const VMStateDescription vmstate_bcm2835_fb = {
    .name = TYPE_BCM2835_FB,
    .version_id = 1,
    .minimum_version_id = 1,
""",
        """static int bcm2835_fb_post_load(void *opaque, int version_id)
{
    BCM2835FBState *s = opaque;

    if (version_id < 2 || !s->scanout_xres || !s->scanout_yres) {
        s->hvs_scanout = false;
        s->scanout_xres = s->config.xres;
        s->scanout_yres = s->config.yres;
        s->scanout_x = 0;
        s->scanout_y = 0;
    }
    if (s->scanout_xres > XRES_MAX || s->scanout_yres > YRES_MAX ||
        s->config.xres > s->scanout_xres ||
        s->config.yres > s->scanout_yres ||
        s->scanout_x > s->scanout_xres - s->config.xres ||
        s->scanout_y > s->scanout_yres - s->config.yres) {
        return -EINVAL;
    }

    qemu_console_resize(s->con, s->scanout_xres, s->scanout_yres);
    s->invalidate = true;
    return 0;
}

static const VMStateDescription vmstate_bcm2835_fb = {
    .name = TYPE_BCM2835_FB,
    .version_id = 2,
    .minimum_version_id = 1,
    .post_load = bcm2835_fb_post_load,
""",
    )
    replace_once(
        path,
        """        VMSTATE_UINT32(config.pixo, BCM2835FBState),
        VMSTATE_UINT32(config.alpha, BCM2835FBState),
        VMSTATE_END_OF_LIST()
""",
        """        VMSTATE_UINT32(config.pixo, BCM2835FBState),
        VMSTATE_UINT32(config.alpha, BCM2835FBState),
        VMSTATE_BOOL_V(hvs_scanout, BCM2835FBState, 2),
        VMSTATE_UINT32_V(scanout_xres, BCM2835FBState, 2),
        VMSTATE_UINT32_V(scanout_yres, BCM2835FBState, 2),
        VMSTATE_UINT32_V(scanout_x, BCM2835FBState, 2),
        VMSTATE_UINT32_V(scanout_y, BCM2835FBState, 2),
        VMSTATE_END_OF_LIST()
""",
    )
    replace_once(
        path,
        """    s->config = s->initial_config;

    s->invalidate = true;
""",
        """    s->config = s->initial_config;
    s->hvs_scanout = false;
    s->scanout_xres = s->config.xres;
    s->scanout_yres = s->config.yres;
    s->scanout_x = 0;
    s->scanout_y = 0;

    s->invalidate = true;
""",
    )
    replace_once(
        path,
        """    qemu_console_resize(s->con, s->config.xres, s->config.yres);
""",
        """    qemu_console_resize(s->con,
                        s->scanout_xres, s->scanout_yres);
""",
    )


def patch_hvs() -> None:
    path = Path("hw/display/bcm2835_hvs.c")
    replace_once(
        path,
        """static bool bcm2835_hvs_scanout_config(BCM2835HVSState *s,
                                       unsigned channel,
                                       BCM2835FBConfig *config)
""",
        """static bool bcm2835_hvs_scanout_config(BCM2835HVSState *s,
                                       unsigned channel,
                                       BCM2835FBConfig *config,
                                       uint32_t *output_xres,
                                       uint32_t *output_yres,
                                       uint32_t *scanout_x,
                                       uint32_t *scanout_y)
""",
    )
    replace_once(
        path,
        """    uint32_t width;
    uint32_t height;
    uint32_t pitch;
""",
        """    uint32_t width;
    uint32_t height;
    uint32_t start_x;
    uint32_t start_y;
    uint32_t pitch;
""",
    )
    replace_once(
        path,
        """    width = pos2 & SCALER_POS2_WIDTH_MASK;
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
""",
        """    width = pos2 & SCALER_POS2_WIDTH_MASK;
    height = (pos2 >> SCALER_POS2_HEIGHT_SHIFT) &
             SCALER_POS2_HEIGHT_MASK;
    start_x = pos0 & SCALER_POS0_START_X_MASK;
    start_y = (pos0 >> SCALER_POS0_START_Y_SHIFT) &
              SCALER_POS0_START_Y_MASK;
    pitch = s->regs[list_index + SCALER_UNITY_PLANE_PITCH_WORD] &
            SCALER_SRC_PITCH_MASK;

    if (!width || !height ||
        width > channel_width || height > channel_height ||
        start_x > channel_width - width ||
        start_y > channel_height - height ||
        pitch < width * bytes_per_pixel ||
        pitch % bytes_per_pixel != 0) {
        return false;
    }
""",
    )
    replace_once(
        path,
        """    config->base = s->regs[list_index + SCALER_UNITY_PLANE_PTR_WORD];
    config->pixo = pixo;
    return true;
""",
        """    config->base = s->regs[list_index + SCALER_UNITY_PLANE_PTR_WORD];
    config->pixo = pixo;
    *output_xres = channel_width;
    *output_yres = channel_height;
    *scanout_x = start_x;
    *scanout_y = start_y;
    return true;
""",
    )
    replace_between(
        path,
        "static void bcm2835_hvs_refresh_scanout(BCM2835HVSState *s)\n",
        "static void bcm2835_hvs_vblank(void *opaque, int channel, int level)\n",
        """static void bcm2835_hvs_refresh_scanout(BCM2835HVSState *s)
{
    BCM2835FBConfig config;
    uint32_t output_xres;
    uint32_t output_yres;
    uint32_t scanout_x;
    uint32_t scanout_y;
    int channel;

    /* BCM2835 HDMI is fed by HVS channel 2.  Retain lower-channel support
     * for focused device tests and the other first-generation outputs.
     */
    for (channel = SCALER_CHANNEL_COUNT - 1; channel >= 0; channel--) {
        if (!bcm2835_hvs_scanout_config(s, channel, &config,
                                        &output_xres, &output_yres,
                                        &scanout_x, &scanout_y)) {
            continue;
        }

        if (memcmp(&s->fb->config, &config, sizeof(config)) != 0 ||
            !s->fb->hvs_scanout ||
            s->fb->scanout_xres != output_xres ||
            s->fb->scanout_yres != output_yres ||
            s->fb->scanout_x != scanout_x ||
            s->fb->scanout_y != scanout_y) {
            bcm2835_fb_reconfigure_scanout(s->fb, &config,
                                            output_xres, output_yres,
                                            scanout_x, scanout_y);
        } else {
            s->fb->invalidate = true;
        }
        return;
    }
}

""",
    )


def patch_smoke() -> None:
    path = Path("scripts/vc4/hvs-dlist-smoke.py")
    replace_once(
        path,
        """RGBA8888_SCANOUT_LIST_WORD = 0x160
RGBA8888_SCANOUT_BUFFER_A = 0x00206000
RGBA8888_SCANOUT_BUFFER_B = 0x00207000
""",
        """RGBA8888_SCANOUT_LIST_WORD = 0x160
RGBA8888_SCANOUT_BUFFER_A = 0x00206000
RGBA8888_SCANOUT_BUFFER_B = 0x00207000
POSITIONED_OUTPUT_WIDTH = 12
POSITIONED_OUTPUT_HEIGHT = 8
POSITIONED_X = 2
POSITIONED_Y = 2
POSITIONED_SCANOUT_LIST_WORD = 0x180
POSITIONED_SCANOUT_BUFFER_A = 0x00208000
POSITIONED_SCANOUT_BUFFER_B = 0x00209000
""",
    )
    replace_once(
        path,
        """def exercise_rgb565_scanout(
""",
        """def expect_positioned_pattern(
    path: Path,
    expected_colors: tuple[tuple[int, int, int], ...],
) -> None:
    width, height, pixels = read_ppm(path)
    if (width, height) != (POSITIONED_OUTPUT_WIDTH, POSITIONED_OUTPUT_HEIGHT):
        raise RuntimeError(
            f"positioned HVS size mismatch: {(width, height)} != "
            f"{(POSITIONED_OUTPUT_WIDTH, POSITIONED_OUTPUT_HEIGHT)}"
        )

    for y in range(height):
        for x in range(width):
            inside = (
                POSITIONED_X <= x < POSITIONED_X + SCANOUT_WIDTH
                and POSITIONED_Y <= y < POSITIONED_Y + SCANOUT_HEIGHT
            )
            if inside:
                source_x = x - POSITIONED_X
                source_y = y - POSITIONED_Y
                quadrant = (
                    2 if source_y >= SCANOUT_HEIGHT // 2 else 0
                ) + (1 if source_x >= SCANOUT_WIDTH // 2 else 0)
                expected = expected_colors[quadrant]
            else:
                expected = (0, 0, 0)
            offset = (y * width + x) * 3
            actual = tuple(pixels[offset:offset + 3])
            if actual != expected:
                raise RuntimeError(
                    f"positioned HVS pixel {(x, y)} is {actual}, "
                    f"expected {expected}"
                )


def exercise_positioned_scanout(
    qtest: Any,
    qmp: QMPClient,
    temp: Path,
) -> None:
    colors_a = (0x00FF0000, 0x0000FF00, 0x000000FF, 0x00FFFFFF)
    colors_b = (0x00FFFFFF, 0x000000FF, 0x0000FF00, 0x00FF0000)
    expected_a = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255))
    expected_b = ((255, 255, 255), (0, 0, 255), (0, 255, 0), (255, 0, 0))
    dlist = SCALER_DLIST_START + POSITIONED_SCANOUT_LIST_WORD * 4
    ctl0 = (
        SCALER_CTL0_VALID
        | (7 << 24)
        | SCALER_CTL0_ORDER_ABGR
        | SCALER_CTL0_RGBA_EXPAND_ROUND
        | SCALER_CTL0_UNITY
        | HVS_PIXEL_FORMAT_RGBA8888
    )
    element = (
        ctl0,
        0xFF000000 | (POSITIONED_Y << 12) | POSITIONED_X,
        (SCANOUT_HEIGHT << 16) | SCANOUT_WIDTH,
        0xC0C0C0C0,
        POSITIONED_SCANOUT_BUFFER_A,
        0xC0C0C0C0,
        SCANOUT_PITCH,
        SCALER_CTL0_END,
    )

    write_pattern(qtest, POSITIONED_SCANOUT_BUFFER_A, colors_a)
    write_pattern(qtest, POSITIONED_SCANOUT_BUFFER_B, colors_b)
    for index, value in enumerate(element):
        qtest.writel(dlist + index * 4, value)

    qtest.writel(
        SCALER_DISPLIST[SCANOUT_CHANNEL], POSITIONED_SCANOUT_LIST_WORD
    )
    qtest.writel(
        SCALER_DISPCTRLX[SCANOUT_CHANNEL],
        SCALER_DISPCTRLX_ENABLE
        | (POSITIONED_OUTPUT_WIDTH << 12)
        | POSITIONED_OUTPUT_HEIGHT,
    )

    first = temp / "hvs-positioned-a.ppm"
    qmp.execute("screendump", {"filename": str(first)})
    expect_positioned_pattern(first, expected_a)

    qtest.writel(dlist + 4 * 4, POSITIONED_SCANOUT_BUFFER_B)
    second = temp / "hvs-positioned-b.ppm"
    qmp.execute("screendump", {"filename": str(second)})
    expect_positioned_pattern(second, expected_b)

    qtest.writel(SCALER_DISPCTRLX[SCANOUT_CHANNEL], 0)
    expect_disabled_empty(qtest, SCANOUT_CHANNEL)


def exercise_rgb565_scanout(
""",
    )
    replace_once(
        path,
        """            exercise_scanout(qtest, qmp, temp)
            exercise_rgb565_scanout(qtest, qmp, temp)
""",
        """            exercise_scanout(qtest, qmp, temp)
            exercise_positioned_scanout(qtest, qmp, temp)
            exercise_rgb565_scanout(qtest, qmp, temp)
""",
    )
    replace_once(
        path,
        """    print("BCM2835 HVS display-list and linear scanout smoke test passed")
""",
        """    print(
        "BCM2835 HVS display-list, linear, and positioned scanout "
        "smoke test passed"
    )
""",
    )


def main() -> int:
    patch_header()
    patch_framebuffer()
    patch_hvs()
    patch_smoke()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
