/*
 * QEMU ATI SVGA emulation
 * 2D engine functions
 *
 * Copyright (c) 2019 BALATON Zoltan
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#include "qemu/osdep.h"
#include "ati_int.h"
#include "ati_regs.h"
#include "ati_rop3.h"
#include "qemu/log.h"
#include "ui/console.h"
#include "ui/rect.h"

/*
 * Keep the common copy and solid-fill paths mapped to pixman. The software
 * path is deliberately general: it covers all 256 ROP3 truth tables, partial
 * write masks, 24-bpp pixels, and direction-sensitive overlapping blits.
 */

static int ati_bpp_from_datatype(const ATIVGAState *s)
{
    switch (s->regs.dp_datatype & DP_DST_DATATYPE) {
    case DST_8BPP:
        return 8;
    case DST_15BPP:
    case DST_16BPP:
        return 16;
    case DST_24BPP:
        return 24;
    case DST_32BPP:
        return 32;
    default:
        qemu_log_mask(LOG_UNIMP, "Unknown dst datatype %d\n",
                      s->regs.dp_datatype & DP_DST_DATATYPE);
        return 0;
    }
}

typedef struct {
    VGACommonState *vga;
    int bpp;
    uint8_t rop3;
    uint32_t write_mask;
    uint32_t frgd_clr;
    uint32_t src_clr;
    uint32_t src_source;
    bool host_data_active;
    bool left_to_right;
    bool top_to_bottom;
    bool need_swap;
    QemuRect scissor;

    QemuRect dst;
    int dst_stride;
    uint8_t *dst_bits;
    uint32_t dst_offset;
    size_t dst_size;

    QemuRect src;
    int src_stride;
    const uint8_t *src_bits;
    uint32_t src_offset;
    size_t src_size;
    const uint8_t *src_valid;
    unsigned int src_valid_stride;
} ATI2DCtx;

#ifdef CONFIG_PIXMAN
static uint32_t ati_pixel_mask(unsigned int bypp)
{
    return bypp == sizeof(uint32_t) ? UINT32_MAX :
           (UINT32_C(1) << (bypp * 8)) - 1;
}
#endif

static uint8_t ati_reg_pixel_byte(const ATI2DCtx *ctx, uint32_t value,
                                  unsigned int byte)
{
    unsigned int bypp = ctx->bpp / 8;
    unsigned int shift = ctx->vga->big_endian_fb ?
                         (bypp - 1 - byte) * 8 : byte * 8;

    return value >> shift;
}

static void ati_store_reg_pixel(const ATI2DCtx *ctx, uint8_t *dst,
                                uint32_t value)
{
    unsigned int bypp = ctx->bpp / 8;

    for (unsigned int byte = 0; byte < bypp; byte++) {
        dst[byte] = ati_reg_pixel_byte(ctx, value, byte);
    }
}

static bool ati_rect_in_buffer(size_t size, uint32_t offset, int stride,
                               const QemuRect *rect, unsigned int bypp)
{
    uint64_t end;

    if (rect->x < 0 || rect->y < 0 || rect->width <= 0 ||
        rect->height <= 0 || stride <= 0 || offset >= size) {
        return false;
    }

    end = (uint64_t)offset +
          (uint64_t)(rect->y + rect->height - 1) * stride +
          (uint64_t)(rect->x + rect->width) * bypp;
    return end <= size;
}

static void ati_set_dirty(const ATI2DCtx *ctx)
{
    VGACommonState *vga = ctx->vga;
    DisplaySurface *ds = qemu_console_surface(vga->con);
    QemuRect dirty;
    unsigned int bypp = ctx->bpp / 8;
    hwaddr dirty_start;
    hwaddr dirty_end;

    qemu_rect_intersect(&ctx->dst, &ctx->scissor, &dirty);
    if (!dirty.width || !dirty.height) {
        return;
    }
    dirty_start = ctx->dst_offset + dirty.x * bypp +
                  dirty.y * ctx->dst_stride;
    dirty_end = dirty_start + dirty.width * bypp +
                (dirty.height - 1) * ctx->dst_stride;
    /*
     * The blit may be outside of the visible screen (e.g. virtual desktops.)
     * Dirty only the intersection of the visible screen and the blit.
     */
    hwaddr vis_start = vga->vbe_start_addr * 4;
    hwaddr vis_end = vis_start + vga->vbe_regs[VBE_DISPI_INDEX_YRES] *
                       vga->vbe_line_offset;
    hwaddr start = MAX(vis_start, dirty_start);
    hwaddr end = MIN(MIN(vis_end, dirty_end), vga->vram_size);

    (void)ds;
    DPRINTF("%p %u ds: %p %d %d rop: %x\n", vga->vram_ptr,
            vga->vbe_start_addr, surface_data(ds), surface_stride(ds),
            surface_bits_per_pixel(ds), ctx->rop3);

    if (start < end) {
        memory_region_set_dirty(&vga->vram, start, end - start);
    }
}

static void setup_2d_blt_ctx(ATIVGAState *s, ATI2DCtx *ctx)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->vga = &s->vga;
    ctx->bpp = ati_bpp_from_datatype(s);
    ctx->rop3 = (s->regs.dp_mix & GMC_ROP3_MASK) >> 16;
    ctx->write_mask = s->regs.dp_write_mask;
    ctx->host_data_active = s->host_data.active;
    ctx->left_to_right = s->regs.dp_cntl & DST_X_LEFT_TO_RIGHT;
    ctx->top_to_bottom = s->regs.dp_cntl & DST_Y_TOP_TO_BOTTOM;
    ctx->need_swap = (HOST_BIG_ENDIAN != s->vga.big_endian_fb);
    ctx->frgd_clr = s->regs.dp_brush_frgd_clr;
    ctx->src_clr = s->regs.dp_src_frgd_clr;
    ctx->src_source = s->regs.dp_mix & DP_SRC_SOURCE;

    if (s->regs.sc_right >= s->regs.sc_left &&
        s->regs.sc_bottom >= s->regs.sc_top) {
        ctx->scissor.width = s->regs.sc_right - s->regs.sc_left + 1;
        ctx->scissor.height = s->regs.sc_bottom - s->regs.sc_top + 1;
    }
    ctx->scissor.x = s->regs.sc_left;
    ctx->scissor.y = s->regs.sc_top;

    ctx->dst.width = s->regs.dst_width;
    ctx->dst.height = s->regs.dst_height;
    ctx->dst.x = ctx->left_to_right ?
                 s->regs.dst_x : s->regs.dst_x + 1 - ctx->dst.width;
    ctx->dst.y = ctx->top_to_bottom ?
                 s->regs.dst_y : s->regs.dst_y + 1 - ctx->dst.height;
    ctx->dst_stride = s->regs.dst_pitch;
    ctx->dst_bits = s->vga.vram_ptr;
    ctx->dst_offset = s->regs.dst_offset;
    ctx->dst_size = s->vga.vram_size;
    if (s->dev_id == PCI_DEVICE_ID_ATI_RAGE128_PF) {
        /* Rage 128 pitch registers count groups of eight pixels. */
        ctx->dst_stride *= ctx->bpp;
    }

    ctx->src.x = ctx->left_to_right ?
                 s->regs.src_x : s->regs.src_x + 1 - ctx->dst.width;
    ctx->src.y = ctx->top_to_bottom ?
                 s->regs.src_y : s->regs.src_y + 1 - ctx->dst.height;
    ctx->src.width = ctx->dst.width;
    ctx->src.height = ctx->dst.height;
    ctx->src_stride = s->regs.src_pitch;
    ctx->src_bits = s->vga.vram_ptr;
    ctx->src_offset = s->regs.src_offset;
    ctx->src_size = s->vga.vram_size;
    if (s->dev_id == PCI_DEVICE_ID_ATI_RAGE128_PF) {
        ctx->src_stride *= ctx->bpp;
    }
    DPRINTF("%d %d %d, %d %d %d, (%d,%d) -> (%d,%d) %dx%d %c %c\n",
            s->regs.src_offset, s->regs.dst_offset, s->regs.default_offset,
            ctx->src_stride, ctx->dst_stride, s->regs.default_pitch,
            ctx->src.x, ctx->src.y, ctx->dst.x, ctx->dst.y,
            ctx->dst.width, ctx->dst.height,
            ctx->left_to_right ? '>' : '<',
            ctx->top_to_bottom ? 'v' : '^');
}

#ifdef CONFIG_PIXMAN
static uint32_t make_filler(int bpp, uint32_t color)
{
    if (bpp < 24) {
        color |= color << 16;
        if (bpp < 15) {
            color |= color << 8;
        }
    }
    return color;
}
#endif

static bool ati_source_is_memory(const ATI2DCtx *ctx)
{
    return ctx->host_data_active || ctx->src_source == DP_SRC_RECT;
}

static bool ati_source_is_solid(const ATI2DCtx *ctx)
{
    return !ctx->host_data_active && ctx->src_source == 0;
}

static bool ati_2d_software_blt(const ATI2DCtx *ctx,
                                const QemuRect *vis_src,
                                const QemuRect *vis_dst)
{
    unsigned int bypp = ctx->bpp / 8;
    bool uses_src = ati_rop3_uses_source(ctx->rop3);

    for (int row = 0; row < vis_dst->height; row++) {
        int dy = ctx->top_to_bottom ? vis_dst->y + row :
                 vis_dst->y + vis_dst->height - 1 - row;
        int sy = ctx->top_to_bottom ? vis_src->y + row :
                 vis_src->y + vis_src->height - 1 - row;

        for (int col = 0; col < vis_dst->width; col++) {
            int dx = ctx->left_to_right ? vis_dst->x + col :
                     vis_dst->x + vis_dst->width - 1 - col;
            int sx = ctx->left_to_right ? vis_src->x + col :
                     vis_src->x + vis_src->width - 1 - col;
            uint8_t *dst = ctx->dst_bits + ctx->dst_offset +
                           dy * ctx->dst_stride + dx * bypp;
            const uint8_t *src = NULL;

            if (uses_src && ati_source_is_memory(ctx)) {
                if (ctx->src_valid &&
                    !ctx->src_valid[sy * ctx->src_valid_stride + sx]) {
                    continue;
                }
                src = ctx->src_bits + ctx->src_offset +
                      sy * ctx->src_stride + sx * bypp;
            }

            for (unsigned int byte = 0; byte < bypp; byte++) {
                uint8_t dst_byte = dst[byte];
                uint8_t src_byte = 0;
                uint8_t pat_byte = ati_reg_pixel_byte(ctx, ctx->frgd_clr,
                                                      byte);
                uint8_t mask_byte = ati_reg_pixel_byte(ctx,
                                                       ctx->write_mask,
                                                       byte);
                uint8_t result;

                if (uses_src) {
                    if (src) {
                        src_byte = src[byte];
                    } else {
                        src_byte = ati_reg_pixel_byte(ctx, ctx->src_clr,
                                                      byte);
                    }
                }
                result = ati_rop3_apply_mask(ctx->rop3, pat_byte,
                                             src_byte, dst_byte, mask_byte);
                dst[byte] = result;
            }
        }
    }
    return true;
}

static bool ati_2d_do_blt(const ATI2DCtx *ctx, uint8_t use_pixman)
{
    QemuRect vis_src, vis_dst;
    unsigned int bypp = ctx->bpp / 8;
#ifdef CONFIG_PIXMAN
    uint32_t pixel_mask;
#endif
    bool uses_src;

    if (!ctx->bpp) {
        qemu_log_mask(LOG_GUEST_ERROR, "Invalid bpp\n");
        return false;
    }
    if (!ctx->dst_stride) {
        qemu_log_mask(LOG_GUEST_ERROR, "Zero dest pitch\n");
        return false;
    }
    qemu_rect_intersect(&ctx->dst, &ctx->scissor, &vis_dst);
    if (!vis_dst.height || !vis_dst.width) {
        /* Nothing is visible, completely clipped. */
        return false;
    }
    if (!ati_rect_in_buffer(ctx->dst_size, ctx->dst_offset,
                            ctx->dst_stride, &vis_dst, bypp)) {
        qemu_log_mask(LOG_GUEST_ERROR, "2D destination outside VRAM\n");
        return false;
    }

    /* Keep source and destination aligned when destination clipping applies. */
    vis_src.x = ctx->src.x + (vis_dst.x - ctx->dst.x);
    vis_src.y = ctx->src.y + (vis_dst.y - ctx->dst.y);
    vis_src.width = vis_dst.width;
    vis_src.height = vis_dst.height;

    DPRINTF("dst: (%d,%d) %dx%d -> vis_dst: (%d,%d) %dx%d\n",
            ctx->dst.x, ctx->dst.y, ctx->dst.width, ctx->dst.height,
            vis_dst.x, vis_dst.y, vis_dst.width, vis_dst.height);
    DPRINTF("src: (%d,%d) %dx%d -> vis_src: (%d,%d) %dx%d\n",
            ctx->src.x, ctx->src.y, ctx->dst.width, ctx->dst.height,
            vis_src.x, vis_src.y, vis_src.width, vis_src.height);

    uses_src = ati_rop3_uses_source(ctx->rop3);
    if (uses_src) {
        if (!ati_source_is_memory(ctx) && !ati_source_is_solid(ctx)) {
            qemu_log_mask(LOG_UNIMP,
                          "Unsupported ATI 2D source selector %x\n",
                          ctx->src_source);
            return false;
        }
        if (ati_source_is_memory(ctx)) {
            if (!ctx->src_stride) {
                qemu_log_mask(LOG_GUEST_ERROR, "Zero source pitch\n");
                return false;
            }
            if (!ati_rect_in_buffer(ctx->src_size, ctx->src_offset,
                                    ctx->src_stride, &vis_src, bypp)) {
                qemu_log_mask(LOG_GUEST_ERROR,
                              "2D source outside source buffer\n");
                return false;
            }
        }
    }

#ifdef CONFIG_PIXMAN
    pixel_mask = ati_pixel_mask(bypp);

    /* Fast path for the overwhelmingly common plain screen-to-screen copy. */
    if ((use_pixman & BIT(1)) && ctx->rop3 == 0xcc &&
        (ctx->write_mask & pixel_mask) == pixel_mask &&
        ati_source_is_memory(ctx) && !ctx->host_data_active &&
        !ctx->src_valid &&
        ctx->left_to_right && ctx->top_to_bottom &&
        !(ctx->src_stride % sizeof(uint32_t)) &&
        !(ctx->dst_stride % sizeof(uint32_t))) {
        DPRINTF("pixman_blt(%p, %p, %d, %d, %d, %d, %d, %d, %d, %d, %d, %d)\n",
                ctx->src_bits + ctx->src_offset,
                ctx->dst_bits + ctx->dst_offset,
                ctx->src_stride / (int)sizeof(uint32_t),
                ctx->dst_stride / (int)sizeof(uint32_t),
                ctx->bpp, ctx->bpp, vis_src.x, vis_src.y,
                vis_dst.x, vis_dst.y, vis_dst.width, vis_dst.height);
        if (pixman_blt((uint32_t *)(ctx->src_bits + ctx->src_offset),
                       (uint32_t *)(ctx->dst_bits + ctx->dst_offset),
                       ctx->src_stride / sizeof(uint32_t),
                       ctx->dst_stride / sizeof(uint32_t),
                       ctx->bpp, ctx->bpp, vis_src.x, vis_src.y,
                       vis_dst.x, vis_dst.y,
                       vis_dst.width, vis_dst.height)) {
            return true;
        }
    }

    /* Fast solid fills; 24-bpp and partial masks use the generic path. */
    if ((use_pixman & BIT(0)) && ctx->bpp != 24 &&
        (ctx->write_mask & pixel_mask) == pixel_mask &&
        (ctx->rop3 == 0x00 || ctx->rop3 == 0xf0 || ctx->rop3 == 0xff) &&
        !(ctx->dst_stride % sizeof(uint32_t))) {
        uint32_t filler = ctx->rop3 == 0x00 ? 0 :
                          ctx->rop3 == 0xff ? UINT32_MAX :
                          make_filler(ctx->bpp, ctx->frgd_clr);

        if (ctx->need_swap) {
            bswap32s(&filler);
        }
        DPRINTF("pixman_fill(%p, %d, %d, %d, %d, %d, %d, %x)\n",
                ctx->dst_bits + ctx->dst_offset,
                ctx->dst_stride / (int)sizeof(uint32_t), ctx->bpp,
                vis_dst.x, vis_dst.y, vis_dst.width, vis_dst.height,
                filler);
        if (pixman_fill((uint32_t *)(ctx->dst_bits + ctx->dst_offset),
                        ctx->dst_stride / sizeof(uint32_t), ctx->bpp,
                        vis_dst.x, vis_dst.y,
                        vis_dst.width, vis_dst.height, filler)) {
            return true;
        }
    }
#endif

    return ati_2d_software_blt(ctx, &vis_src, &vis_dst);
}

void ati_2d_blt(ATIVGAState *s)
{
    ATI2DCtx ctx;
    uint32_t src_source = s->regs.dp_mix & DP_SRC_SOURCE;

    /* Finish any active HOST_DATA blits before starting a new blit. */
    ati_host_data_finish(s);

    if (src_source == DP_SRC_HOST || src_source == DP_SRC_HOST_BYTEALIGN) {
        /* Begin a HOST_DATA blit. */
        s->host_data.active = true;
        s->host_data.next = 0;
        s->host_data.col = 0;
        s->host_data.row = 0;
        memset(s->host_data.acc, 0, sizeof(s->host_data.acc));
        return;
    }
    setup_2d_blt_ctx(s, &ctx);
    if (ati_2d_do_blt(&ctx, s->use_pixman)) {
        ati_set_dirty(&ctx);
    }
}

bool ati_host_data_flush(ATIVGAState *s)
{
    ATI2DCtx ctx, chunk;
    unsigned int bypp, pix_count, row, col, idx;
    unsigned int dst_width, dst_height;
    uint8_t pix_buf[ATI_HOST_DATA_ACC_BITS * sizeof(uint32_t)];
    uint8_t valid_buf[ATI_HOST_DATA_ACC_BITS];
    uint32_t src_source = s->regs.dp_mix & DP_SRC_SOURCE;
    uint32_t src_datatype = s->regs.dp_datatype & DP_SRC_DATATYPE;

    if (!s->host_data.active) {
        return false;
    }
    if (src_source != DP_SRC_HOST) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "host_data_blt: unsupported src_source %x\n", src_source);
        return false;
    }
    if (src_datatype != SRC_MONO_FRGD_BKGD &&
        src_datatype != SRC_MONO_FRGD && src_datatype != SRC_COLOR) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "host_data_blt: undefined src_datatype %x\n",
                      src_datatype);
        return false;
    }

    setup_2d_blt_ctx(s, &ctx);

    if (!ctx.bpp) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "host_data_blt: invalid bpp from datatype\n");
        return false;
    }
    if (!ctx.dst.width || !ctx.dst.height) {
        s->host_data.active = false;
        return false;
    }
    dst_width = ctx.dst.width;
    dst_height = ctx.dst.height;
    if (ctx.bpp == 24 && src_datatype == SRC_COLOR) {
        qemu_log_mask(LOG_UNIMP,
                      "host_data_blt: packed color unsupported in 24 bits mode\n");
        return false;
    }
    if (!ctx.left_to_right || !ctx.top_to_bottom) {
        qemu_log_mask(LOG_UNIMP,
                      "host_data_blt: unsupported blit direction %c%c\n",
                      ctx.left_to_right ? '>' : '<',
                      ctx.top_to_bottom ? 'v' : '^');
        return false;
    }

    bypp = ctx.bpp / 8;
    memset(valid_buf, 1, sizeof(valid_buf));
    pix_count = ATI_HOST_DATA_ACC_BITS;
    if (src_datatype == SRC_COLOR) {
        pix_count /= ctx.bpp;
        memcpy(pix_buf, s->host_data.acc, sizeof(s->host_data.acc));
    } else {
        /* Expand monochrome bits to color pixels. */
        uint32_t byte_pix_order = s->regs.dp_datatype & DP_BYTE_PIX_ORDER;

        idx = 0;
        for (int word = 0; word < 4; word++) {
            for (int byte = 0; byte < 4; byte++) {
                uint8_t byte_val = s->host_data.acc[word] >> (byte * 8);
                for (int bit = 0; bit < 8; bit++) {
                    bool is_fg = byte_val & BIT(byte_pix_order ? bit :
                                                7 - bit);
                    uint32_t color = is_fg ? s->regs.dp_src_frgd_clr :
                                            s->regs.dp_src_bkgd_clr;

                    ati_store_reg_pixel(&ctx, &pix_buf[idx], color);
                    valid_buf[idx / bypp] =
                        is_fg || src_datatype == SRC_MONO_FRGD_BKGD;
                    idx += bypp;
                }
            }
        }
    }

    /* Copy and then modify blit ctx for use in a chunked blit. */
    chunk = ctx;
    chunk.src_bits = pix_buf;
    chunk.src_offset = 0;
    chunk.src_size = sizeof(pix_buf);
    chunk.src.y = 0;
    chunk.src_stride = ATI_HOST_DATA_ACC_BITS * bypp;
    chunk.src_valid = src_datatype == SRC_MONO_FRGD ? valid_buf : NULL;
    chunk.src_valid_stride = ATI_HOST_DATA_ACC_BITS;

    /* Blit one scanline chunk at a time. */
    row = s->host_data.row;
    col = s->host_data.col;
    idx = 0;
    DPRINTF("blt %dpx @ row: %d, col: %d\n", pix_count, row, col);
    while (idx < pix_count && row < dst_height) {
        unsigned int pix_in_scanline = MIN(pix_count - idx,
                                           dst_width - col);

        chunk.src.x = idx;
        chunk.src.width = pix_in_scanline;
        chunk.src.height = 1;
        /* Build a rect for this scanline chunk. */
        chunk.dst.x = ctx.dst.x + col;
        chunk.dst.y = ctx.dst.y + row;
        chunk.dst.width = pix_in_scanline;
        chunk.dst.height = 1;
        DPRINTF("blt %dpx span @ row: %d, col: %d to dst (%d,%d)\n",
                pix_in_scanline, row, col, chunk.dst.x, chunk.dst.y);
        if (ati_2d_do_blt(&chunk, s->use_pixman)) {
            ati_set_dirty(&chunk);
        }
        idx += pix_in_scanline;
        col += pix_in_scanline;
        if (col >= dst_width) {
            col = 0;
            row++;
        }
    }

    /* Track state of the overall blit for use by the next flush. */
    s->host_data.row = row;
    s->host_data.col = col;
    if (s->host_data.row >= dst_height) {
        s->host_data.active = false;
    }
    memset(s->host_data.acc, 0, sizeof(s->host_data.acc));

    return s->host_data.active;
}

void ati_host_data_finish(ATIVGAState *s)
{
    if (ati_host_data_flush(s)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "HOST_DATA blit ended before all data was written\n");
    }
    s->host_data.active = false;
}
