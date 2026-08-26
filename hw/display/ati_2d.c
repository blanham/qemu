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
    uint32_t bkgd_clr;
    uint32_t src_clr;
    uint32_t src_source;
    uint32_t pixel_mask;
    uint32_t clr_cmp_cntl;
    uint32_t clr_cmp_clr_src;
    uint32_t clr_cmp_clr_dst;
    uint32_t clr_cmp_mask;
    uint32_t brush_datatype;
    uint32_t brush_y_x;
    const uint32_t *brush_data;
    bool byte_pix_lsb;
    bool host_data_active;
    bool left_to_right;
    bool top_to_bottom;
    bool need_swap;
    bool tiled;
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

static uint32_t ati_pixel_mask(const ATIVGAState *s)
{
    switch (s->regs.dp_datatype & DP_DST_DATATYPE) {
    case DST_8BPP:
        return 0x000000ffU;
    case DST_15BPP:
        return 0x00007fffU;
    case DST_16BPP:
        return 0x0000ffffU;
    case DST_24BPP:
        return 0x00ffffffU;
    case DST_32BPP:
        return UINT32_MAX;
    default:
        return 0;
    }
}

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

static uint32_t ati_load_reg_pixel(const ATI2DCtx *ctx, const uint8_t *src)
{
    unsigned int bypp = ctx->bpp / 8;
    uint32_t value = 0;

    for (unsigned int byte = 0; byte < bypp; byte++) {
        unsigned int shift = ctx->vga->big_endian_fb ?
                             (bypp - 1 - byte) * 8 : byte * 8;

        value |= (uint32_t)src[byte] << shift;
    }
    return value & ctx->pixel_mask;
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
    ctx->pixel_mask = ati_pixel_mask(s);
    ctx->rop3 = (s->regs.dp_mix & GMC_ROP3_MASK) >> 16;
    ctx->write_mask = s->regs.dp_write_mask;
    ctx->host_data_active = s->host_data.active;
    ctx->left_to_right = s->regs.dp_cntl & DST_X_LEFT_TO_RIGHT;
    ctx->top_to_bottom = s->regs.dp_cntl & DST_Y_TOP_TO_BOTTOM;
    ctx->need_swap = (HOST_BIG_ENDIAN != s->vga.big_endian_fb);
    ctx->frgd_clr = s->regs.dp_brush_frgd_clr;
    ctx->bkgd_clr = s->regs.dp_brush_bkgd_clr;
    ctx->src_clr = s->regs.dp_src_frgd_clr;
    ctx->src_source = s->regs.dp_mix & DP_SRC_SOURCE;
    ctx->clr_cmp_cntl = s->regs.clr_cmp_cntl;
    ctx->clr_cmp_clr_src = s->regs.clr_cmp_clr_src;
    ctx->clr_cmp_clr_dst = s->regs.clr_cmp_clr_dst;
    ctx->clr_cmp_mask = s->regs.clr_cmp_mask;
    ctx->brush_datatype = s->regs.dp_datatype & DP_BRUSH_DATATYPE;
    ctx->brush_y_x = s->regs.brush_y_x;
    ctx->brush_data = s->regs.brush_data;
    ctx->byte_pix_lsb = !!(s->regs.dp_datatype & DP_BYTE_PIX_ORDER);
    ctx->tiled = s->regs.src_tile || s->regs.dst_tile ||
                 !!(s->regs.dp_cntl & (DST_X_TILE | DST_Y_TILE));

    if (s->regs.sc_right >= s->regs.sc_left &&
        s->regs.sc_bottom >= s->regs.sc_top) {
        ctx->scissor.width = s->regs.sc_right - s->regs.sc_left + 1;
        ctx->scissor.height = s->regs.sc_bottom - s->regs.sc_top + 1;
    }
    ctx->scissor.x = s->regs.sc_left;
    ctx->scissor.y = s->regs.sc_top;

    /* Rage 128 expresses horizontal 24-bpp scissors in byte coordinates. */
    if (ctx->bpp == 24 && ctx->scissor.width > 0) {
        int right = (ctx->scissor.x + ctx->scissor.width - 1) / 3;

        ctx->scissor.x /= 3;
        ctx->scissor.width = right - ctx->scissor.x + 1;
    }

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

static bool ati_brush_supported(uint32_t brush)
{
    switch (brush) {
    case BRUSH_8X8_MONO_FG_BG:
    case BRUSH_8X8_MONO_FG_LA:
    case BRUSH_32X1_MONO_FG_BG:
    case BRUSH_32X1_MONO_FG_LA:
    case BRUSH_SOLIDCOLOR:
        return true;
    default:
        return false;
    }
}

/*
 * Resolve the pattern operand for one pixel. A false return means a
 * foreground-only monochrome brush selected its transparent background bit.
 */
static bool ati_brush_pixel(const ATI2DCtx *ctx, int x, int y,
                            uint32_t line_index, bool line,
                            uint32_t *color)
{
    unsigned int bit;
    bool foreground;

    switch (ctx->brush_datatype) {
    case BRUSH_SOLIDCOLOR:
        *color = ctx->frgd_clr;
        return true;

    case BRUSH_8X8_MONO_FG_BG:
    case BRUSH_8X8_MONO_FG_LA:
    {
        unsigned int px = (unsigned int)
                          (x - (int)(ctx->brush_y_x & 0xffU)) & 7U;
        unsigned int py = (unsigned int)
                          (y - (int)((ctx->brush_y_x >> 8) & 0xffU)) & 7U;
        uint32_t word = ctx->brush_data[py >> 2];
        uint8_t row = word >> ((py & 3U) * 8U);

        bit = ctx->byte_pix_lsb ? px : 7U - px;
        foreground = !!(row & (UINT8_C(1) << bit));
        break;
    }

    case BRUSH_32X1_MONO_FG_BG:
    case BRUSH_32X1_MONO_FG_LA:
    {
        unsigned int phase = ctx->brush_y_x & 31U;
        unsigned int px = line ? line_index + phase :
                                 (unsigned int)(x - (int)phase);

        bit = px & 31U;
        if (!ctx->byte_pix_lsb) {
            bit = 31U - bit;
        }
        foreground = !!(ctx->brush_data[0] & (UINT32_C(1) << bit));
        break;
    }

    default:
        return false;
    }

    if (!foreground &&
        (ctx->brush_datatype == BRUSH_8X8_MONO_FG_LA ||
         ctx->brush_datatype == BRUSH_32X1_MONO_FG_LA)) {
        return false;
    }

    *color = foreground ? ctx->frgd_clr : ctx->bkgd_clr;
    return true;
}

static bool ati_source_is_memory(const ATI2DCtx *ctx)
{
    return ctx->host_data_active || ctx->src_source == DP_SRC_RECT;
}

static bool ati_source_is_solid(const ATI2DCtx *ctx)
{
    return !ctx->host_data_active && ctx->src_source == 0;
}

static bool ati_color_compare_enabled(const ATI2DCtx *ctx)
{
    return (ctx->clr_cmp_cntl & CLR_CMP_FN_MASK) != 0;
}

static bool ati_color_compare_uses_source(const ATI2DCtx *ctx)
{
    return ati_color_compare_enabled(ctx) &&
           (ctx->clr_cmp_cntl & CLR_CMP_SRC_SOURCE);
}

static bool ati_color_compare_supported(const ATI2DCtx *ctx,
                                        bool source_available)
{
    uint32_t function = ctx->clr_cmp_cntl & CLR_CMP_FN_MASK;

    if (!function) {
        return true;
    }
    if (function != SRC_CMP_EQ_COLOR &&
        function != SRC_CMP_NEQ_COLOR) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI color-compare function %x is not implemented\n",
                      function);
        return false;
    }
    if ((ctx->clr_cmp_cntl & CLR_CMP_SRC_SOURCE) &&
        !source_available) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI source color comparison has no source\n");
        return false;
    }
    return true;
}

static bool ati_color_compare_pass(const ATI2DCtx *ctx,
                                   uint32_t source, uint32_t dest)
{
    uint32_t function = ctx->clr_cmp_cntl & CLR_CMP_FN_MASK;
    uint32_t mask;
    uint32_t value;
    uint32_t reference;
    bool equal;

    if (!function) {
        return true;
    }

    mask = ctx->clr_cmp_mask & ctx->pixel_mask;
    if (ctx->clr_cmp_cntl & CLR_CMP_SRC_SOURCE) {
        value = source;
        reference = ctx->clr_cmp_clr_src;
    } else {
        value = dest;
        reference = ctx->clr_cmp_clr_dst;
    }

    equal = (value & mask) == (reference & mask);
    return function == SRC_CMP_EQ_COLOR ? equal : !equal;
}

static bool ati_2d_software_blt(const ATI2DCtx *ctx,
                                const QemuRect *vis_src,
                                const QemuRect *vis_dst)
{
    unsigned int bypp = ctx->bpp / 8;
    bool uses_pattern = ati_rop3_uses_pattern(ctx->rop3);
    bool uses_src = ati_rop3_uses_source(ctx->rop3);
    bool needs_src = uses_src || ati_color_compare_uses_source(ctx);

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
            uint32_t source_pixel = 0;
            uint32_t dest_pixel = ati_load_reg_pixel(ctx, dst);
            uint32_t pixel_pattern = ctx->frgd_clr;

            if (uses_pattern &&
                !ati_brush_pixel(ctx, dx, dy, 0, false, &pixel_pattern)) {
                continue;
            }
            if (needs_src) {
                if (ati_source_is_memory(ctx)) {
                    if (ctx->src_valid &&
                        !ctx->src_valid[sy * ctx->src_valid_stride + sx]) {
                        continue;
                    }
                    src = ctx->src_bits + ctx->src_offset +
                          sy * ctx->src_stride + sx * bypp;
                    source_pixel = ati_load_reg_pixel(ctx, src);
                } else {
                    source_pixel = ctx->src_clr & ctx->pixel_mask;
                }
            }
            if (!ati_color_compare_pass(ctx, source_pixel, dest_pixel)) {
                continue;
            }

            for (unsigned int byte = 0; byte < bypp; byte++) {
                uint8_t dst_byte = dst[byte];
                uint8_t src_byte = 0;
                uint8_t pat_byte = ati_reg_pixel_byte(ctx, pixel_pattern,
                                                      byte);
                uint8_t mask_byte = ati_reg_pixel_byte(ctx,
                                                       ctx->write_mask,
                                                       byte);
                uint8_t result;

                if (uses_src) {
                    src_byte = src ? src[byte] :
                               ati_reg_pixel_byte(ctx, source_pixel, byte);
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
    bool source_available;
    bool uses_src;
    bool needs_src;

    if (!ctx->bpp) {
        qemu_log_mask(LOG_GUEST_ERROR, "Invalid bpp\n");
        return false;
    }
    if (!ctx->dst_stride) {
        qemu_log_mask(LOG_GUEST_ERROR, "Zero dest pitch\n");
        return false;
    }
    if (ctx->tiled) {
        qemu_log_mask(LOG_UNIMP,
                      "Tiled ATI 2D surfaces are not implemented\n");
        return false;
    }
    if (ati_rop3_uses_pattern(ctx->rop3) &&
        !ati_brush_supported(ctx->brush_datatype)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI 2D brush datatype %x is not implemented\n",
                      ctx->brush_datatype);
        return false;
    }

    source_available = ati_source_is_memory(ctx) ||
                       ati_source_is_solid(ctx);
    if (!ati_color_compare_supported(ctx, source_available)) {
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
    needs_src = uses_src || ati_color_compare_uses_source(ctx);
    if (needs_src) {
        if (!source_available) {
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
    pixel_mask = ctx->pixel_mask;

    /* Fast path for the overwhelmingly common plain screen-to-screen copy. */
    if (!ati_color_compare_enabled(ctx) &&
        (use_pixman & BIT(1)) && ctx->rop3 == 0xcc &&
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
    if (!ati_color_compare_enabled(ctx) &&
        (use_pixman & BIT(0)) && ctx->bpp != 24 &&
        (ctx->write_mask & pixel_mask) == pixel_mask &&
        (ctx->rop3 == 0x00 || ctx->rop3 == 0xf0 || ctx->rop3 == 0xff) &&
        (ctx->rop3 != 0xf0 ||
         ctx->brush_datatype == BRUSH_SOLIDCOLOR) &&
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

void ati_2d_line(ATIVGAState *s)
{
    ATI2DCtx ctx;
    unsigned int bypp;
    uint32_t flags = s->regs.dp_cntl_xdir_ydir_ymajor;
    uint32_t length = s->regs.dst_bres_lnth & 0x7fff;
    int32_t error = sextract32(s->regs.dst_bres_err, 0, 18);
    int32_t increment = sextract32(s->regs.dst_bres_inc, 0, 18);
    int32_t decrement = sextract32(s->regs.dst_bres_dec, 0, 18);
    int x = s->regs.dst_x;
    int y = s->regs.dst_y;
    int x_step = flags & DST_LINE_X_LEFT_TO_RIGHT ? 1 : -1;
    int y_step = flags & DST_LINE_Y_TOP_TO_BOTTOM ? 1 : -1;
    bool y_major = flags & DST_LINE_Y_MAJOR;
    bool uses_pattern;

    ati_host_data_finish(s);
    setup_2d_blt_ctx(s, &ctx);

    if (!length || !ctx.bpp || !ctx.dst_stride) {
        return;
    }
    if (ctx.tiled) {
        qemu_log_mask(LOG_UNIMP,
                      "Tiled ATI 2D line destinations are not implemented\n");
        return;
    }
    uses_pattern = ati_rop3_uses_pattern(ctx.rop3);
    if (uses_pattern && !ati_brush_supported(ctx.brush_datatype)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI 2D line brush datatype %x is not implemented\n",
                      ctx.brush_datatype);
        return;
    }
    if (ati_rop3_uses_source(ctx.rop3)) {
        qemu_log_mask(LOG_UNIMP,
                      "Source-dependent ATI 2D line ROP %02x is not implemented\n",
                      ctx.rop3);
        return;
    }
    if (!ati_color_compare_supported(&ctx, false)) {
        return;
    }

    bypp = ctx.bpp / 8;
    for (uint32_t i = 0; i < length; i++) {
        if (ctx.scissor.width > 0 && ctx.scissor.height > 0 &&
            x >= ctx.scissor.x &&
            x < ctx.scissor.x + ctx.scissor.width &&
            y >= ctx.scissor.y &&
            y < ctx.scissor.y + ctx.scissor.height &&
            x >= 0 && y >= 0) {
            uint64_t offset = (uint64_t)ctx.dst_offset +
                              (uint64_t)(unsigned int)y * ctx.dst_stride +
                              (uint64_t)(unsigned int)x * bypp;
            uint32_t pixel_pattern = ctx.frgd_clr;
            bool draw = !uses_pattern ||
                        ati_brush_pixel(&ctx, x, y, i, true,
                                        &pixel_pattern);

            if (draw && offset + bypp <= ctx.dst_size) {
                uint8_t *dst = ctx.dst_bits + offset;
                uint32_t dest_pixel = ati_load_reg_pixel(&ctx, dst);

                if (ati_color_compare_pass(&ctx, 0, dest_pixel)) {
                    for (unsigned int byte = 0; byte < bypp; byte++) {
                        uint8_t pattern =
                            ati_reg_pixel_byte(&ctx, pixel_pattern, byte);
                        uint8_t mask =
                            ati_reg_pixel_byte(&ctx, ctx.write_mask, byte);

                        dst[byte] = ati_rop3_apply_mask(ctx.rop3, pattern, 0,
                                                       dst[byte], mask);
                    }
                    memory_region_set_dirty(&s->vga.vram, offset, bypp);
                }
            }
        }

        if (y_major) {
            y += y_step;
            if (error >= 0) {
                error += decrement;
                x += x_step;
            } else {
                error += increment;
            }
        } else {
            x += x_step;
            if (error >= 0) {
                error += decrement;
                y += y_step;
            } else {
                error += increment;
            }
        }
    }

    s->regs.dst_x = x & 0x3fff;
    s->regs.dst_y = y & 0x3fff;
    s->regs.dst_bres_err = error & 0x3ffff;
}

void ati_2d_blt(ATIVGAState *s)
{
    ATI2DCtx ctx;
    uint32_t src_source = s->regs.dp_mix & DP_SRC_SOURCE;

    /* Finish any active HOST_DATA blits before starting a new blit. */
    ati_host_data_finish(s);

    if (src_source == DP_SRC_HOST_BYTEALIGN) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI HOST_DATA byte-aligned blits are not implemented\n");
        return;
    }
    if (src_source == DP_SRC_HOST) {
        /* Begin a HOST_DATA blit. */
        memset(&s->host_data, 0, sizeof(s->host_data));
        s->host_data.active = true;
        return;
    }
    setup_2d_blt_ctx(s, &ctx);
    if (ati_2d_do_blt(&ctx, s->use_pixman)) {
        ati_set_dirty(&ctx);
    }
}

static void ati_host_data_blt_span(ATIVGAState *s,
                                   const ATI2DCtx *ctx,
                                   const uint8_t *pixels,
                                   unsigned int count,
                                   unsigned int row,
                                   unsigned int col)
{
    ATI2DCtx chunk = *ctx;
    unsigned int bypp = ctx->bpp / 8;

    if (!count) {
        return;
    }

    chunk.src_bits = pixels;
    chunk.src_offset = 0;
    chunk.src_size = count * bypp;
    chunk.src.x = 0;
    chunk.src.y = 0;
    chunk.src.width = count;
    chunk.src.height = 1;
    chunk.src_stride = count * bypp;
    chunk.src_valid = NULL;
    chunk.src_valid_stride = count;

    chunk.dst.x = ctx->dst.x + col;
    chunk.dst.y = ctx->dst.y + row;
    chunk.dst.width = count;
    chunk.dst.height = 1;

    if (ati_2d_do_blt(&chunk, s->use_pixman)) {
        ati_set_dirty(&chunk);
    }
}

static bool ati_host_data_flush_color(ATIVGAState *s,
                                      const ATI2DCtx *ctx,
                                      unsigned int dst_width,
                                      unsigned int dst_height,
                                      unsigned int word_count)
{
    ATIHostDataState *host = &s->host_data;
    unsigned int bypp = ctx->bpp / 8;
    uint8_t bytes[sizeof(host->acc)];
    uint8_t pixels[sizeof(host->acc) + sizeof(host->pixel)];
    unsigned int pixel_count = 0;
    unsigned int span_row = host->row;
    unsigned int span_col = host->col;
    unsigned int capacity = sizeof(pixels) / bypp;

    for (unsigned int word = 0; word < word_count; word++) {
        uint32_t value = host->acc[word];

        for (unsigned int byte = 0; byte < sizeof(uint32_t); byte++) {
            bytes[word * sizeof(uint32_t) + byte] = value >> (byte * 8);
        }
    }

    for (unsigned int i = 0; i < word_count * sizeof(uint32_t); i++) {
        if (host->row >= dst_height) {
            host->active = false;
            break;
        }

        if (host->row_padding) {
            host->row_padding--;
            continue;
        }

        host->pixel[host->pixel_bytes_used++] = bytes[i];
        if (host->pixel_bytes_used < bypp) {
            continue;
        }

        memcpy(&pixels[pixel_count * bypp], host->pixel, bypp);
        host->pixel_bytes_used = 0;
        pixel_count++;
        host->col++;

        if (host->col >= dst_width) {
            ati_host_data_blt_span(s, ctx, pixels, pixel_count,
                                   span_row, span_col);
            pixel_count = 0;
            host->col = 0;
            host->row++;
            host->row_padding =
                (4U - ((dst_width * bypp) & 3U)) & 3U;
            span_row = host->row;
            span_col = 0;

            if (host->row >= dst_height) {
                host->active = false;
                break;
            }
        } else if (pixel_count >= capacity) {
            ati_host_data_blt_span(s, ctx, pixels, pixel_count,
                                   span_row, span_col);
            pixel_count = 0;
            span_row = host->row;
            span_col = host->col;
        }
    }

    ati_host_data_blt_span(s, ctx, pixels, pixel_count,
                           span_row, span_col);
    return host->active;
}

bool ati_host_data_flush(ATIVGAState *s)
{
    ATI2DCtx ctx, chunk;
    unsigned int bypp, pix_count, row, col, idx;
    unsigned int dst_width, dst_height;
    unsigned int word_count;
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
    if (!ctx.left_to_right || !ctx.top_to_bottom) {
        qemu_log_mask(LOG_UNIMP,
                      "host_data_blt: unsupported blit direction %c%c\n",
                      ctx.left_to_right ? '>' : '<',
                      ctx.top_to_bottom ? 'v' : '^');
        return false;
    }

    word_count = MIN(s->host_data.next,
                     (uint32_t)G_N_ELEMENTS(s->host_data.acc));
    if (!word_count) {
        return s->host_data.active;
    }

    if (src_datatype == SRC_COLOR) {
        bool active = ati_host_data_flush_color(s, &ctx, dst_width,
                                                dst_height, word_count);

        memset(s->host_data.acc, 0, sizeof(s->host_data.acc));
        s->host_data.next = 0;
        return active;
    }

    bypp = ctx.bpp / 8;
    memset(valid_buf, 1, sizeof(valid_buf));
    pix_count = word_count * sizeof(uint32_t) * BITS_PER_BYTE;

    /* Expand monochrome bits to color pixels. */
    {
        uint32_t byte_pix_order = s->regs.dp_datatype & DP_BYTE_PIX_ORDER;

        idx = 0;
        for (unsigned int word = 0; word < word_count; word++) {
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
    chunk.src_size = pix_count * bypp;
    chunk.src.y = 0;
    chunk.src_stride = pix_count * bypp;
    chunk.src_valid = src_datatype == SRC_MONO_FRGD ? valid_buf : NULL;
    chunk.src_valid_stride = pix_count;

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
        chunk.dst.x = ctx.dst.x + col;
        chunk.dst.y = ctx.dst.y + row;
        chunk.dst.width = pix_in_scanline;
        chunk.dst.height = 1;
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

    s->host_data.row = row;
    s->host_data.col = col;
    if (s->host_data.row >= dst_height) {
        s->host_data.active = false;
    }
    memset(s->host_data.acc, 0, sizeof(s->host_data.acc));
    s->host_data.next = 0;

    return s->host_data.active;
}

void ati_host_data_finish(ATIVGAState *s)
{
    if (ati_host_data_flush(s)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "HOST_DATA blit ended before all data was written\n");
    }
    s->host_data.active = false;
    s->host_data.next = 0;
    s->host_data.pixel_bytes_used = 0;
    s->host_data.row_padding = 0;
    memset(s->host_data.pixel, 0, sizeof(s->host_data.pixel));
}
