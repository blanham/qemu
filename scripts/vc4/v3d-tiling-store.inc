static bool bcm2835_v3d_tiled_pixel_address(
    BCM2835V3DState *s, const VC4V3DCLState *cl, uint32_t stride,
    uint32_t x, uint32_t y, hwaddr *address)
{
    uint32_t memory_format =
        (cl->render_config & VC4_RENDER_CONFIG_MEMORY_FORMAT_MASK) >> 6;
    uint64_t offset;

    if (!vc4_tiling_rgba8888_offset(
            memory_format, stride, x, y, &offset) ||
        offset > UINT64_MAX - cl->render_base) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_RENDER;
        return false;
    }
    *address = cl->render_base + offset;
    return true;
}

static bool bcm2835_v3d_read_tiled_pixel(
    BCM2835V3DState *s, const VC4V3DCLState *cl, uint32_t stride,
    uint32_t x, uint32_t y, uint8_t pixel[4])
{
    hwaddr address;

    return bcm2835_v3d_tiled_pixel_address(
               s, cl, stride, x, y, &address) &&
           bcm2835_v3d_dma_read(s, address, pixel, 4);
}

static bool bcm2835_v3d_write_tiled_pixel(
    BCM2835V3DState *s, const VC4V3DCLState *cl, uint32_t stride,
    uint32_t x, uint32_t y, const uint8_t pixel[4])
{
    hwaddr address;

    return bcm2835_v3d_tiled_pixel_address(
               s, cl, stride, x, y, &address) &&
           bcm2835_v3d_dma_write(s, address, pixel, 4);
}

static bool bcm2835_v3d_store_clear_tile(BCM2835V3DState *s,
                                         VC4V3DCLState *cl)
{
    uint32_t format = cl->render_config & VC4_RENDER_CONFIG_FORMAT_MASK;
    uint32_t memory_format =
        (cl->render_config & VC4_RENDER_CONFIG_MEMORY_FORMAT_MASK) >> 6;
    uint32_t tile_size =
        (cl->render_config & VC4_RENDER_CONFIG_MS_MODE_4X) ? 32 : 64;
    uint32_t x0 = cl->tile_x * tile_size;
    uint32_t y0 = cl->tile_y * tile_size;
    uint32_t x1;
    uint32_t y1;
    uint32_t stride;
    uint32_t row;
    uint32_t col;
    uint8_t *line;
    size_t line_size;

    if (!cl->have_render_config ||
        cl->width == 0 || cl->height == 0 ||
        cl->width > VC4_MAX_RENDER_DIMENSION ||
        cl->height > VC4_MAX_RENDER_DIMENSION) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_RENDER;
        return false;
    }

    if (format != VC4_RENDER_CONFIG_FORMAT_RGBA8888 ||
        !vc4_tiling_rgba8888_stride(
            memory_format, cl->width, &stride)) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": render-store unsupported config=0x%04x\n",
                      cl->render_config);
        return false;
    }

    if (x0 >= cl->width || y0 >= cl->height) {
        /* Hardware clips out-of-range edge tiles. */
        return true;
    }

    x1 = MIN((uint32_t)cl->width, x0 + tile_size);
    y1 = MIN((uint32_t)cl->height, y0 + tile_size);
    line_size = (x1 - x0) * 4;
    line = g_malloc(line_size);

    for (row = y0; row < y1; row++) {
        if (cl->have_clear_color) {
            for (col = 0; col < x1 - x0; col++) {
                stl_le_p(line + col * 4, cl->clear_color[0]);
            }
        } else {
            if (memory_format == VC4_TILING_FORMAT_LINEAR) {
                hwaddr address = cl->render_base +
                    (hwaddr)row * stride + x0 * 4;

                if (!bcm2835_v3d_dma_read(
                        s, address, line, line_size)) {
                    g_free(line);
                    return false;
                }
            } else {
                for (col = 0; col < x1 - x0; col++) {
                    if (!bcm2835_v3d_read_tiled_pixel(
                            s, cl, stride, x0 + col, row,
                            line + col * 4)) {
                        g_free(line);
                        return false;
                    }
                }
            }
        }

        bcm2835_v3d_rasterize_line(s, line, x0, x1, row);

        if (memory_format == VC4_TILING_FORMAT_LINEAR) {
            hwaddr address = cl->render_base +
                (hwaddr)row * stride + x0 * 4;

            if (!bcm2835_v3d_dma_write(
                    s, address, line, line_size)) {
                g_free(line);
                return false;
            }
        } else {
            for (col = 0; col < x1 - x0; col++) {
                if (!bcm2835_v3d_write_tiled_pixel(
                        s, cl, stride, x0 + col, row,
                        line + col * 4)) {
                    g_free(line);
                    return false;
                }
            }
        }
    }

    g_free(line);
    return true;
}
