/*
 * QEMU ATI Rage 128 fixed-function 3D reference renderer
 *
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#include "qemu/osdep.h"
#include "ati_int.h"
#include "ati_regs.h"
#include "ati_rop3.h"
#include "qemu/bswap.h"
#include "qemu/log.h"
#include <math.h>
#include "ui/console.h"

#define ATI_3D_REG_BASE 0x1800
#define ATI_3D_REG_END  0x1e00
#define ATI_3D_MAX_VERTICES 65535
#define ATI_3D_MAX_RASTER_PIXELS (16U * 1024U * 1024U)

#define ATI_3D_TEX_Z_ENABLE          BIT(0)
#define ATI_3D_TEX_Z_WRITE_ENABLE    BIT(1)
#define ATI_3D_TEX_STENCIL_ENABLE    BIT(3)
#define ATI_3D_TEXMAP_ENABLE         BIT(4)
#define ATI_3D_SEC_TEXMAP_ENABLE     BIT(5)
#define ATI_3D_FOG_ENABLE            BIT(7)
#define ATI_3D_ALPHA_ENABLE          BIT(9)
#define ATI_3D_ALPHA_TEST_ENABLE     BIT(10)
#define ATI_3D_SPEC_LIGHT_ENABLE     BIT(11)

#define ATI_3D_PRIM_TEX_MIN_FILTER_SHIFT 1
#define ATI_3D_PRIM_TEX_MAG_FILTER_SHIFT 4
#define ATI_3D_PRIM_TEX_FILTER_MASK      0x7U
#define ATI_3D_PRIM_TEX_MIP_MAP_DISABLE  BIT(7)
#define ATI_3D_PRIM_TEX_CLAMP_S_SHIFT    8
#define ATI_3D_PRIM_TEX_WRAP_S           BIT(10)
#define ATI_3D_PRIM_TEX_CLAMP_T_SHIFT    11
#define ATI_3D_PRIM_TEX_WRAP_T           BIT(13)
#define ATI_3D_PRIM_TEX_FORMAT_SHIFT     16
#define ATI_3D_PRIM_TEX_FORMAT_MASK      0xfU

#define ATI_3D_TEX_FILTER_NEAREST     0U
#define ATI_3D_TEX_FILTER_LINEAR      1U
#define ATI_3D_TEX_WRAP_REPEAT        0U
#define ATI_3D_TEX_WRAP_MIRROR        1U
#define ATI_3D_TEX_WRAP_CLAMP         2U
#define ATI_3D_TEX_WRAP_BORDER        3U

#define ATI_3D_TEX_FORMAT_ARGB1555    3U
#define ATI_3D_TEX_FORMAT_RGB565      4U
#define ATI_3D_TEX_FORMAT_RGB888      5U
#define ATI_3D_TEX_FORMAT_ARGB8888    6U
#define ATI_3D_TEX_FORMAT_RGB332      7U
#define ATI_3D_TEX_FORMAT_Y8          8U
#define ATI_3D_TEX_FORMAT_RGB8        9U
#define ATI_3D_TEX_FORMAT_ARGB4444    15U

#define ATI_3D_COMB_COLOR_FUNC_MASK      0xfU
#define ATI_3D_COMB_COLOR_FACTOR_SHIFT   4
#define ATI_3D_COMB_COLOR_FACTOR_MASK    0xfU
#define ATI_3D_COMB_COLOR_FUNC_MSB       BIT(8)
#define ATI_3D_COMB_INPUT_FACTOR_SHIFT   10
#define ATI_3D_COMB_INPUT_FACTOR_MASK    0xfU
#define ATI_3D_COMB_ALPHA_FUNC_SHIFT     14
#define ATI_3D_COMB_ALPHA_FUNC_MASK      0xfU
#define ATI_3D_COMB_ALPHA_FACTOR_SHIFT   18
#define ATI_3D_COMB_ALPHA_FACTOR_MASK    0xfU
#define ATI_3D_COMB_ALPHA_INPUT_SHIFT    25
#define ATI_3D_COMB_ALPHA_INPUT_MASK     0x7U

#define ATI_3D_COMB_COLOR_TEXTURE        0U
#define ATI_3D_COMB_COLOR_INPUT          2U
#define ATI_3D_COMB_COLOR_MODULATE       3U
#define ATI_3D_COMB_COLOR_ADD            6U
#define ATI_3D_COMB_COLOR_BLEND_TEXTURE  9U
#define ATI_3D_COMB_COLOR_FACTOR_TEX     4U
#define ATI_3D_COMB_COLOR_FACTOR_NTEX    5U
#define ATI_3D_COMB_COLOR_FACTOR_ALPHA   6U
#define ATI_3D_COMB_COLOR_FACTOR_NALPHA  7U
#define ATI_3D_COMB_INPUT_INTERP_COLOR   4U

#define ATI_3D_COMB_ALPHA_TEXTURE        0U
#define ATI_3D_COMB_ALPHA_INPUT          2U
#define ATI_3D_COMB_ALPHA_MODULATE       3U
#define ATI_3D_COMB_ALPHA_ADD            6U
#define ATI_3D_COMB_ALPHA_FACTOR_TEX     6U
#define ATI_3D_COMB_ALPHA_FACTOR_NTEX    7U
#define ATI_3D_COMB_INPUT_INTERP_ALPHA   2U

#define ATI_3D_Z_PIX_WIDTH_MASK      (3U << 1)
#define ATI_3D_Z_TEST_MASK           (7U << 4)

#define ATI_3D_ALPHA_COMB_MASK       (3U << 12)
#define ATI_3D_ALPHA_SRC_SHIFT       16
#define ATI_3D_ALPHA_DST_SHIFT       20
#define ATI_3D_ALPHA_TEST_SHIFT      24

#define ATI_3D_FRONT_DIR_CCW         BIT(0)
#define ATI_3D_BACKFACE_MASK         (3U << 1)
#define ATI_3D_FRONTFACE_MASK        (3U << 3)
#define ATI_3D_COLOR_MODE_MASK       (3U << 5)
#define ATI_3D_COLOR_SOLID           (0U << 5)
#define ATI_3D_COLOR_FLAT            (1U << 5)
#define ATI_3D_COLOR_GOURAUD         (2U << 5)
#define ATI_3D_COLOR_GOURAUD2        (3U << 5)
#define ATI_3D_FLAT_SHADE_VERTEX_OGL BIT(14)
#define ATI_3D_FACE_CULL             0U
#define ATI_3D_FACE_POINTS           1U
#define ATI_3D_FACE_LINES            2U
#define ATI_3D_FACE_SOLID            3U

#define ATI_3D_AUX1_ENABLE           BIT(0)
#define ATI_3D_AUX2_ENABLE           BIT(2)
#define ATI_3D_AUX3_ENABLE           BIT(4)

#define ATI_3D_Z_TILE                BIT(16)

#define ATI_3D_VC_PRIM_MASK          0x7U
#define ATI_3D_VC_WALK_MASK          0x30U
#define ATI_3D_VC_COUNT_SHIFT        16

#define ATI_3D_VERTEX_RHW            BIT(0)
#define ATI_3D_VERTEX_DIFFUSE_BGR    BIT(1)
#define ATI_3D_VERTEX_DIFFUSE_A      BIT(2)
#define ATI_3D_VERTEX_DIFFUSE_ARGB   BIT(3)
#define ATI_3D_VERTEX_SPEC_BGR       BIT(4)
#define ATI_3D_VERTEX_SPEC_F         BIT(5)
#define ATI_3D_VERTEX_SPEC_FRGB      BIT(6)
#define ATI_3D_VERTEX_ST             BIT(7)
#define ATI_3D_VERTEX_S2T2           BIT(8)
#define ATI_3D_VERTEX_RHW2           BIT(9)

#define ATI_3D_WALK_IND              0x10U
#define ATI_3D_WALK_LIST             0x20U
#define ATI_3D_WALK_RING             0x30U

#define ATI_3D_PRIM_POINT            1U
#define ATI_3D_PRIM_LINE             2U
#define ATI_3D_PRIM_POLYLINE         3U
#define ATI_3D_PRIM_TRI_LIST         4U
#define ATI_3D_PRIM_TRI_FAN          5U
#define ATI_3D_PRIM_TRI_STRIP        6U
#define ATI_3D_PRIM_TRI_TYPE2        7U

typedef struct ATI3DSurface {
    uint32_t offset;
    uint32_t stride;
    uint32_t width;
    uint32_t pixel_mask;
    unsigned int bpp;
    unsigned int bytes_per_pixel;
    bool tiled;
} ATI3DSurface;

typedef struct ATI3DTexture {
    uint32_t offset;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t control;
    uint32_t combine;
    uint32_t border_color;
    unsigned int bytes_per_pixel;
    unsigned int format;
    unsigned int wrap_s;
    unsigned int wrap_t;
    bool linear;
} ATI3DTexture;

typedef struct ATI3DVertex {
    float x;
    float y;
    float z;
    float rhw;
    float color[4];    /* red, green, blue, alpha */
    float specular[4]; /* red, green, blue, fog */
    float texcoord[2][2];
} ATI3DVertex;

typedef struct ATI3DRect {
    int left;
    int top;
    int right;
    int bottom;
} ATI3DRect;

typedef struct ATI3DFragmentContext {
    ATIVGAState *s;
    ATI3DSurface *color_surface;
    ATI3DSurface depth_surface;
    ATI3DTexture texture;
    uint32_t tex_control;
    uint32_t misc;
    uint32_t write_mask;
    uint32_t depth_mask;
    uint32_t depth_function;
    uint64_t color_dirty_start;
    uint64_t color_dirty_end;
    uint64_t depth_dirty_start;
    uint64_t depth_dirty_end;
    bool texture_enabled;
    bool depth_enabled;
} ATI3DFragmentContext;

typedef struct ATI3DShadeState {
    unsigned int mode;
    bool flat_last;
    bool specular_enabled;
    float solid[4];
} ATI3DShadeState;

static uint32_t *ati_3d_reg_ptr(ATIVGAState *s, hwaddr addr)
{
    if (addr < ATI_3D_REG_BASE || addr >= ATI_3D_REG_END || (addr & 3)) {
        return NULL;
    }
    return &s->accel3d.regs[(addr - ATI_3D_REG_BASE) / 4];
}

static const uint32_t *ati_3d_reg_cptr(const ATIVGAState *s, hwaddr addr)
{
    if (addr < ATI_3D_REG_BASE || addr >= ATI_3D_REG_END || (addr & 3)) {
        return NULL;
    }
    return &s->accel3d.regs[(addr - ATI_3D_REG_BASE) / 4];
}

static uint32_t ati_3d_reg(const ATIVGAState *s, hwaddr addr)
{
    const uint32_t *reg = ati_3d_reg_cptr(s, addr);

    return reg ? *reg : 0;
}

static uint32_t ati_3d_read_lane(uint32_t value, hwaddr addr,
                                 unsigned int size)
{
    unsigned int offset = addr & 3;

    if (!offset && size == 4) {
        return value;
    }
    return extract32(value, offset * 8, size * 8);
}

static void ati_3d_write_lane(uint32_t *value, hwaddr addr, uint64_t data,
                              unsigned int size)
{
    unsigned int offset = addr & 3;

    if (!offset && size == 4) {
        *value = data;
    } else {
        *value = deposit32(*value, offset * 8, size * 8, data);
    }
}

static bool ati_3d_aux_reg(ATIVGAState *s, hwaddr addr,
                           uint32_t **value)
{
    if (addr == AUX_SC_CNTL) {
        *value = &s->accel3d.aux_sc_cntl;
        return true;
    }
    if (addr >= AUX1_SC_LEFT && addr <= AUX3_SC_BOTTOM && !(addr & 3)) {
        unsigned int window = (addr - AUX1_SC_LEFT) / 0x10;
        unsigned int field = ((addr - AUX1_SC_LEFT) & 0xf) / 4;

        if (window < 3 && field < 4) {
            *value = &s->accel3d.aux_sc[window][field];
            return true;
        }
    }
    return false;
}

void ati_3d_reset(ATIVGAState *s)
{
    memset(&s->accel3d, 0, sizeof(s->accel3d));
    s->accel3d.regs[(SC_BOTTOM_RIGHT_C - ATI_3D_REG_BASE) / 4] =
        UINT32_C(0x1fff1fff);
    s->accel3d.regs[(PLANE_3D_MASK_C - ATI_3D_REG_BASE) / 4] = UINT32_MAX;
    s->accel3d.regs[(RE_WIDTH_HEIGHT - ATI_3D_REG_BASE) / 4] =
        UINT32_C(0x1fff1fff);
}

bool ati_3d_mm_read(ATIVGAState *s, hwaddr addr, unsigned int size,
                    uint32_t *value)
{
    uint32_t *reg;
    hwaddr aligned = addr & ~3ULL;

    if (size != 1 && size != 2 && size != 4) {
        return false;
    }
    if (aligned >= ATI_3D_REG_BASE && aligned < ATI_3D_REG_END) {
        reg = ati_3d_reg_ptr(s, aligned);
        *value = ati_3d_read_lane(*reg, addr, size);
        return true;
    }
    if (aligned >= GUI_SCRATCH_REG0 && aligned <= GUI_SCRATCH_REG5) {
        reg = &s->accel3d.gui_scratch[(aligned - GUI_SCRATCH_REG0) / 4];
        *value = ati_3d_read_lane(*reg, addr, size);
        return true;
    }
    if (ati_3d_aux_reg(s, aligned, &reg)) {
        *value = ati_3d_read_lane(*reg, addr, size);
        return true;
    }
    if (aligned == RE_TOP_LEFT) {
        *value = ati_3d_read_lane(s->accel3d.re_top_left, addr, size);
        return true;
    }
    return false;
}

bool ati_3d_mm_write(ATIVGAState *s, hwaddr addr, uint64_t data,
                     unsigned int size)
{
    uint32_t *reg;
    hwaddr aligned = addr & ~3ULL;

    if (size != 1 && size != 2 && size != 4) {
        return false;
    }
    if (aligned >= ATI_3D_REG_BASE && aligned < ATI_3D_REG_END) {
        reg = ati_3d_reg_ptr(s, aligned);
        ati_3d_write_lane(reg, addr, data, size);
        return true;
    }
    if (aligned >= GUI_SCRATCH_REG0 && aligned <= GUI_SCRATCH_REG5) {
        reg = &s->accel3d.gui_scratch[(aligned - GUI_SCRATCH_REG0) / 4];
        ati_3d_write_lane(reg, addr, data, size);
        return true;
    }
    if (ati_3d_aux_reg(s, aligned, &reg)) {
        ati_3d_write_lane(reg, addr, data, size);
        return true;
    }
    if (aligned == RE_TOP_LEFT) {
        ati_3d_write_lane(&s->accel3d.re_top_left, addr, data, size);
        return true;
    }
    return false;
}

static bool ati_3d_decode_surface(uint32_t master, uint32_t pitch_offset,
                                  ATI3DSurface *surface)
{
    uint32_t datatype = (master >> 8) & 0xf;
    uint32_t pitch_groups = (pitch_offset >> 21) & 0x3ff;

    memset(surface, 0, sizeof(*surface));
    switch (datatype) {
    case DST_8BPP:
        surface->bpp = 8;
        surface->bytes_per_pixel = 1;
        surface->pixel_mask = 0xff;
        break;
    case DST_15BPP:
        surface->bpp = 15;
        surface->bytes_per_pixel = 2;
        surface->pixel_mask = 0x7fff;
        break;
    case DST_16BPP:
        surface->bpp = 16;
        surface->bytes_per_pixel = 2;
        surface->pixel_mask = 0xffff;
        break;
    case DST_24BPP:
        surface->bpp = 24;
        surface->bytes_per_pixel = 3;
        surface->pixel_mask = 0x00ffffff;
        break;
    case DST_32BPP:
        surface->bpp = 32;
        surface->bytes_per_pixel = 4;
        surface->pixel_mask = UINT32_MAX;
        break;
    default:
        return false;
    }

    surface->offset = (pitch_offset & 0x001fffffU) << 5;
    surface->width = pitch_groups * 8;
    surface->stride = surface->width * surface->bytes_per_pixel;
    surface->tiled = pitch_offset & R128_DST_TILE;
    return surface->stride != 0;
}

static uint32_t ati_3d_load_pixel(const ATIVGAState *s,
                                  const ATI3DSurface *surface,
                                  uint64_t address)
{
    const uint8_t *src = s->vga.vram_ptr + address;
    uint32_t value = 0;

    for (unsigned int byte = 0; byte < surface->bytes_per_pixel; byte++) {
        unsigned int shift = s->vga.big_endian_fb ?
            (surface->bytes_per_pixel - 1 - byte) * 8 : byte * 8;
        value |= (uint32_t)src[byte] << shift;
    }
    return value & surface->pixel_mask;
}

static void ati_3d_store_pixel(ATIVGAState *s, const ATI3DSurface *surface,
                               uint64_t address, uint32_t value,
                               uint32_t write_mask)
{
    uint8_t *dst = s->vga.vram_ptr + address;
    uint32_t old = ati_3d_load_pixel(s, surface, address);
    uint32_t mask = write_mask & surface->pixel_mask;
    uint32_t merged = (value & mask) | (old & ~mask);

    for (unsigned int byte = 0; byte < surface->bytes_per_pixel; byte++) {
        unsigned int shift = s->vga.big_endian_fb ?
            (surface->bytes_per_pixel - 1 - byte) * 8 : byte * 8;
        dst[byte] = merged >> shift;
    }
}

static void ati_3d_unpack_surface_color(const ATI3DSurface *surface,
                                        uint32_t value, float color[4])
{
    switch (surface->bpp) {
    case 8:
        color[0] = color[1] = color[2] = value & 0xff;
        color[3] = 255;
        break;
    case 15:
        color[0] = ((value >> 10) & 0x1f) * (255.0f / 31.0f);
        color[1] = ((value >> 5) & 0x1f) * (255.0f / 31.0f);
        color[2] = (value & 0x1f) * (255.0f / 31.0f);
        color[3] = 255;
        break;
    case 16:
        color[0] = ((value >> 11) & 0x1f) * (255.0f / 31.0f);
        color[1] = ((value >> 5) & 0x3f) * (255.0f / 63.0f);
        color[2] = (value & 0x1f) * (255.0f / 31.0f);
        color[3] = 255;
        break;
    case 24:
        color[0] = (value >> 16) & 0xff;
        color[1] = (value >> 8) & 0xff;
        color[2] = value & 0xff;
        color[3] = 255;
        break;
    case 32:
        color[0] = (value >> 16) & 0xff;
        color[1] = (value >> 8) & 0xff;
        color[2] = value & 0xff;
        color[3] = (value >> 24) & 0xff;
        break;
    default:
        memset(color, 0, 4 * sizeof(*color));
        break;
    }
}

static uint8_t ati_3d_clamp_channel(float value)
{
    if (value <= 0.0f) {
        return 0;
    }
    if (value >= 255.0f) {
        return 255;
    }
    return value + 0.5f;
}

static uint32_t ati_3d_pack_surface_color(const ATI3DSurface *surface,
                                          const float color[4])
{
    uint32_t r = ati_3d_clamp_channel(color[0]);
    uint32_t g = ati_3d_clamp_channel(color[1]);
    uint32_t b = ati_3d_clamp_channel(color[2]);
    uint32_t a = ati_3d_clamp_channel(color[3]);

    switch (surface->bpp) {
    case 8:
        return (r * 77 + g * 150 + b * 29) >> 8;
    case 15:
        return ((r >> 3) << 10) | ((g >> 3) << 5) | (b >> 3);
    case 16:
        return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3);
    case 24:
        return (r << 16) | (g << 8) | b;
    case 32:
        return (a << 24) | (r << 16) | (g << 8) | b;
    default:
        return 0;
    }
}

static bool ati_3d_texture_combine_supported(uint32_t combine)
{
    unsigned int color_function = combine & ATI_3D_COMB_COLOR_FUNC_MASK;
    unsigned int color_factor =
        (combine >> ATI_3D_COMB_COLOR_FACTOR_SHIFT) &
        ATI_3D_COMB_COLOR_FACTOR_MASK;
    unsigned int input_factor =
        (combine >> ATI_3D_COMB_INPUT_FACTOR_SHIFT) &
        ATI_3D_COMB_INPUT_FACTOR_MASK;
    unsigned int alpha_function =
        (combine >> ATI_3D_COMB_ALPHA_FUNC_SHIFT) &
        ATI_3D_COMB_ALPHA_FUNC_MASK;
    unsigned int alpha_factor =
        (combine >> ATI_3D_COMB_ALPHA_FACTOR_SHIFT) &
        ATI_3D_COMB_ALPHA_FACTOR_MASK;
    unsigned int alpha_input =
        (combine >> ATI_3D_COMB_ALPHA_INPUT_SHIFT) &
        ATI_3D_COMB_ALPHA_INPUT_MASK;

    if ((combine & ATI_3D_COMB_COLOR_FUNC_MSB) ||
        input_factor != ATI_3D_COMB_INPUT_INTERP_COLOR ||
        alpha_input != ATI_3D_COMB_INPUT_INTERP_ALPHA) {
        return false;
    }

    switch (color_function) {
    case ATI_3D_COMB_COLOR_TEXTURE:
    case ATI_3D_COMB_COLOR_INPUT:
    case ATI_3D_COMB_COLOR_BLEND_TEXTURE:
        if (color_factor != ATI_3D_COMB_COLOR_FACTOR_TEX) {
            return false;
        }
        break;
    case ATI_3D_COMB_COLOR_MODULATE:
    case ATI_3D_COMB_COLOR_ADD:
        if (color_factor < ATI_3D_COMB_COLOR_FACTOR_TEX ||
            color_factor > ATI_3D_COMB_COLOR_FACTOR_NALPHA) {
            return false;
        }
        break;
    default:
        return false;
    }

    switch (alpha_function) {
    case ATI_3D_COMB_ALPHA_TEXTURE:
    case ATI_3D_COMB_ALPHA_INPUT:
        return alpha_factor == ATI_3D_COMB_ALPHA_FACTOR_TEX;
    case ATI_3D_COMB_ALPHA_MODULATE:
    case ATI_3D_COMB_ALPHA_ADD:
        return alpha_factor == ATI_3D_COMB_ALPHA_FACTOR_TEX ||
               alpha_factor == ATI_3D_COMB_ALPHA_FACTOR_NTEX;
    default:
        return false;
    }
}

static bool ati_3d_texture_decode(ATIVGAState *s, ATI3DTexture *texture)
{
    uint32_t size_pitch = ati_3d_reg(s, TEX_SIZE_PITCH_C);
    uint64_t stride;
    uint64_t span;
    unsigned int pitch_log2 = extract32(size_pitch, 0, 4);
    unsigned int size_log2 = extract32(size_pitch, 4, 4);
    unsigned int height_log2 = extract32(size_pitch, 8, 4);
    unsigned int min_size_log2 = extract32(size_pitch, 12, 4);
    unsigned int min_filter;
    unsigned int mag_filter;

    memset(texture, 0, sizeof(*texture));
    texture->control = ati_3d_reg(s, PRIM_TEX_CNTL_C);
    texture->combine = ati_3d_reg(s, PRIM_TEX_COMBINE_CNTL_C);
    texture->offset = ati_3d_reg(s, PRIM_TEX_0_OFFSET_C);
    texture->border_color = ati_3d_reg(s, PRIM_TEXTURE_BORDER_COLOR_C);

    if (!(texture->control & ATI_3D_PRIM_TEX_MIP_MAP_DISABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 mipmapped texture sampling is not implemented\n");
        return false;
    }
    if (texture->control &
        (ATI_3D_PRIM_TEX_WRAP_S | ATI_3D_PRIM_TEX_WRAP_T)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 legacy texture wrap override is not implemented\n");
        return false;
    }

    min_filter =
        (texture->control >> ATI_3D_PRIM_TEX_MIN_FILTER_SHIFT) &
        ATI_3D_PRIM_TEX_FILTER_MASK;
    mag_filter =
        (texture->control >> ATI_3D_PRIM_TEX_MAG_FILTER_SHIFT) &
        ATI_3D_PRIM_TEX_FILTER_MASK;
    if (min_filter > ATI_3D_TEX_FILTER_LINEAR ||
        mag_filter > ATI_3D_TEX_FILTER_LINEAR ||
        min_filter != mag_filter) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 mixed or mip texture filtering is not implemented\n");
        return false;
    }
    texture->linear = min_filter == ATI_3D_TEX_FILTER_LINEAR;
    texture->wrap_s =
        extract32(texture->control, ATI_3D_PRIM_TEX_CLAMP_S_SHIFT, 2);
    texture->wrap_t =
        extract32(texture->control, ATI_3D_PRIM_TEX_CLAMP_T_SHIFT, 2);
    texture->format =
        (texture->control >> ATI_3D_PRIM_TEX_FORMAT_SHIFT) &
        ATI_3D_PRIM_TEX_FORMAT_MASK;

    if (size_log2 != MAX(pitch_log2, height_log2) ||
        min_size_log2 != size_log2) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 invalid single-level texture dimensions\n");
        return false;
    }
    texture->width = 1U << pitch_log2;
    texture->height = 1U << height_log2;

    switch (texture->format) {
    case ATI_3D_TEX_FORMAT_RGB332:
    case ATI_3D_TEX_FORMAT_Y8:
    case ATI_3D_TEX_FORMAT_RGB8:
        texture->bytes_per_pixel = 1;
        break;
    case ATI_3D_TEX_FORMAT_ARGB1555:
    case ATI_3D_TEX_FORMAT_RGB565:
    case ATI_3D_TEX_FORMAT_ARGB4444:
        texture->bytes_per_pixel = 2;
        break;
    case ATI_3D_TEX_FORMAT_RGB888:
        texture->bytes_per_pixel = 3;
        break;
    case ATI_3D_TEX_FORMAT_ARGB8888:
        texture->bytes_per_pixel = 4;
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 texture format %u is not implemented\n",
                      texture->format);
        return false;
    }

    stride = (uint64_t)texture->width * texture->bytes_per_pixel;
    span = stride * texture->height;
    if (!stride || stride > UINT32_MAX ||
        texture->offset >= s->vga.vram_size ||
        span > s->vga.vram_size - texture->offset) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 texture image exceeds VRAM\n");
        return false;
    }
    texture->stride = stride;

    if (!ati_3d_texture_combine_supported(texture->combine)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 texture combiner state is not implemented\n");
        return false;
    }
    if (ati_3d_reg(s, TEX_CNTL_C) & ATI_3D_SPEC_LIGHT_ENABLE) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 textured separate specular is not implemented\n");
        return false;
    }
    return true;
}

static uint32_t ati_3d_texture_load_raw(const ATIVGAState *s,
                                        uint64_t address,
                                        unsigned int bytes_per_pixel)
{
    const uint8_t *src = s->vga.vram_ptr + address;
    uint32_t value = 0;

    for (unsigned int byte = 0; byte < bytes_per_pixel; byte++) {
        unsigned int shift = s->vga.big_endian_fb ?
            (bytes_per_pixel - 1 - byte) * 8 : byte * 8;
        value |= (uint32_t)src[byte] << shift;
    }
    return value;
}

static bool ati_3d_texture_unpack(const ATI3DTexture *texture,
                                  uint32_t value, float color[4])
{
    switch (texture->format) {
    case ATI_3D_TEX_FORMAT_ARGB1555:
        color[0] = ((value >> 10) & 0x1f) * (255.0f / 31.0f);
        color[1] = ((value >> 5) & 0x1f) * (255.0f / 31.0f);
        color[2] = (value & 0x1f) * (255.0f / 31.0f);
        color[3] = value & 0x8000 ? 255.0f : 0.0f;
        return true;
    case ATI_3D_TEX_FORMAT_RGB565:
        color[0] = ((value >> 11) & 0x1f) * (255.0f / 31.0f);
        color[1] = ((value >> 5) & 0x3f) * (255.0f / 63.0f);
        color[2] = (value & 0x1f) * (255.0f / 31.0f);
        color[3] = 255.0f;
        return true;
    case ATI_3D_TEX_FORMAT_RGB888:
        color[0] = (value >> 16) & 0xff;
        color[1] = (value >> 8) & 0xff;
        color[2] = value & 0xff;
        color[3] = 255.0f;
        return true;
    case ATI_3D_TEX_FORMAT_ARGB8888:
        color[0] = (value >> 16) & 0xff;
        color[1] = (value >> 8) & 0xff;
        color[2] = value & 0xff;
        color[3] = (value >> 24) & 0xff;
        return true;
    case ATI_3D_TEX_FORMAT_RGB332:
    case ATI_3D_TEX_FORMAT_RGB8:
        color[0] = ((value >> 5) & 0x7) * (255.0f / 7.0f);
        color[1] = ((value >> 2) & 0x7) * (255.0f / 7.0f);
        color[2] = (value & 0x3) * (255.0f / 3.0f);
        color[3] = 255.0f;
        return true;
    case ATI_3D_TEX_FORMAT_Y8:
        color[0] = color[1] = color[2] = value & 0xff;
        color[3] = 255.0f;
        return true;
    case ATI_3D_TEX_FORMAT_ARGB4444:
        color[0] = ((value >> 8) & 0xf) * 17.0f;
        color[1] = ((value >> 4) & 0xf) * 17.0f;
        color[2] = (value & 0xf) * 17.0f;
        color[3] = ((value >> 12) & 0xf) * 17.0f;
        return true;
    default:
        return false;
    }
}

static void ati_3d_texture_border(const ATI3DTexture *texture,
                                  float color[4])
{
    color[0] = (texture->border_color >> 16) & 0xff;
    color[1] = (texture->border_color >> 8) & 0xff;
    color[2] = texture->border_color & 0xff;
    color[3] = (texture->border_color >> 24) & 0xff;
}

static float ati_3d_texture_reduce_coord(float coordinate,
                                         unsigned int wrap)
{
    switch (wrap) {
    case ATI_3D_TEX_WRAP_REPEAT:
        coordinate -= floorf(coordinate);
        return coordinate;
    case ATI_3D_TEX_WRAP_MIRROR:
        coordinate = fmodf(coordinate, 2.0f);
        return coordinate < 0.0f ? coordinate + 2.0f : coordinate;
    case ATI_3D_TEX_WRAP_CLAMP:
        return MIN(MAX(coordinate, 0.0f), 1.0f);
    case ATI_3D_TEX_WRAP_BORDER:
        return MIN(MAX(coordinate, -1.0f), 2.0f);
    default:
        return 0.0f;
    }
}

static int ati_3d_texture_resolve_index(int index, unsigned int size,
                                        unsigned int wrap, bool *border)
{
    int period;
    int resolved;

    *border = false;
    switch (wrap) {
    case ATI_3D_TEX_WRAP_REPEAT:
        resolved = index % (int)size;
        return resolved < 0 ? resolved + size : resolved;
    case ATI_3D_TEX_WRAP_MIRROR:
        period = size * 2;
        resolved = index % period;
        if (resolved < 0) {
            resolved += period;
        }
        return resolved < (int)size ? resolved : period - 1 - resolved;
    case ATI_3D_TEX_WRAP_CLAMP:
        return MIN(MAX(index, 0), (int)size - 1);
    case ATI_3D_TEX_WRAP_BORDER:
        if (index < 0 || index >= (int)size) {
            *border = true;
            return 0;
        }
        return index;
    default:
        *border = true;
        return 0;
    }
}

static bool ati_3d_texture_fetch(const ATIVGAState *s,
                                 const ATI3DTexture *texture,
                                 int x, int y, float color[4])
{
    bool border_x;
    bool border_y;
    int resolved_x = ati_3d_texture_resolve_index(
        x, texture->width, texture->wrap_s, &border_x);
    int resolved_y = ati_3d_texture_resolve_index(
        y, texture->height, texture->wrap_t, &border_y);
    uint64_t address;
    uint32_t value;

    if (border_x || border_y) {
        ati_3d_texture_border(texture, color);
        return true;
    }
    address = (uint64_t)texture->offset +
              (uint64_t)resolved_y * texture->stride +
              (uint64_t)resolved_x * texture->bytes_per_pixel;
    if (address + texture->bytes_per_pixel > s->vga.vram_size) {
        return false;
    }
    value = ati_3d_texture_load_raw(s, address, texture->bytes_per_pixel);
    return ati_3d_texture_unpack(texture, value, color);
}

static bool ati_3d_texture_sample(const ATIVGAState *s,
                                  const ATI3DTexture *texture,
                                  const float texcoord[2], float color[4])
{
    float s_coord;
    float t_coord;

    if (!texcoord || !isfinite(texcoord[0]) || !isfinite(texcoord[1])) {
        return false;
    }
    s_coord = ati_3d_texture_reduce_coord(texcoord[0], texture->wrap_s);
    t_coord = ati_3d_texture_reduce_coord(texcoord[1], texture->wrap_t);

    if (!texture->linear) {
        int x = floorf(s_coord * texture->width);
        int y = floorf(t_coord * texture->height);

        return ati_3d_texture_fetch(s, texture, x, y, color);
    } else {
        float x_position = s_coord * texture->width - 0.5f;
        float y_position = t_coord * texture->height - 0.5f;
        int x0 = floorf(x_position);
        int y0 = floorf(y_position);
        float x_fraction = x_position - x0;
        float y_fraction = y_position - y0;
        float c00[4];
        float c10[4];
        float c01[4];
        float c11[4];

        if (!ati_3d_texture_fetch(s, texture, x0, y0, c00) ||
            !ati_3d_texture_fetch(s, texture, x0 + 1, y0, c10) ||
            !ati_3d_texture_fetch(s, texture, x0, y0 + 1, c01) ||
            !ati_3d_texture_fetch(s, texture, x0 + 1, y0 + 1, c11)) {
            return false;
        }
        for (unsigned int channel = 0; channel < 4; channel++) {
            float top = c00[channel] * (1.0f - x_fraction) +
                        c10[channel] * x_fraction;
            float bottom = c01[channel] * (1.0f - x_fraction) +
                           c11[channel] * x_fraction;

            color[channel] = top * (1.0f - y_fraction) +
                             bottom * y_fraction;
        }
        return true;
    }
}

static bool ati_3d_texture_color_factor(unsigned int factor,
                                        const float texture[4],
                                        float result[3])
{
    for (unsigned int channel = 0; channel < 3; channel++) {
        switch (factor) {
        case ATI_3D_COMB_COLOR_FACTOR_TEX:
            result[channel] = texture[channel];
            break;
        case ATI_3D_COMB_COLOR_FACTOR_NTEX:
            result[channel] = 255.0f - texture[channel];
            break;
        case ATI_3D_COMB_COLOR_FACTOR_ALPHA:
            result[channel] = texture[3];
            break;
        case ATI_3D_COMB_COLOR_FACTOR_NALPHA:
            result[channel] = 255.0f - texture[3];
            break;
        default:
            return false;
        }
    }
    return true;
}

static bool ati_3d_texture_combine(const ATI3DTexture *texture,
                                   const float incoming[4],
                                   const float texel[4], float result[4])
{
    unsigned int color_function =
        texture->combine & ATI_3D_COMB_COLOR_FUNC_MASK;
    unsigned int color_factor =
        (texture->combine >> ATI_3D_COMB_COLOR_FACTOR_SHIFT) &
        ATI_3D_COMB_COLOR_FACTOR_MASK;
    unsigned int alpha_function =
        (texture->combine >> ATI_3D_COMB_ALPHA_FUNC_SHIFT) &
        ATI_3D_COMB_ALPHA_FUNC_MASK;
    unsigned int alpha_factor =
        (texture->combine >> ATI_3D_COMB_ALPHA_FACTOR_SHIFT) &
        ATI_3D_COMB_ALPHA_FACTOR_MASK;
    float factor[3];
    float alpha;

    switch (color_function) {
    case ATI_3D_COMB_COLOR_TEXTURE:
        memcpy(result, texel, 3 * sizeof(*result));
        break;
    case ATI_3D_COMB_COLOR_INPUT:
        memcpy(result, incoming, 3 * sizeof(*result));
        break;
    case ATI_3D_COMB_COLOR_MODULATE:
        if (!ati_3d_texture_color_factor(color_factor, texel, factor)) {
            return false;
        }
        for (unsigned int channel = 0; channel < 3; channel++) {
            result[channel] = incoming[channel] * factor[channel] / 255.0f;
        }
        break;
    case ATI_3D_COMB_COLOR_ADD:
        if (!ati_3d_texture_color_factor(color_factor, texel, factor)) {
            return false;
        }
        for (unsigned int channel = 0; channel < 3; channel++) {
            result[channel] = incoming[channel] + factor[channel];
        }
        break;
    case ATI_3D_COMB_COLOR_BLEND_TEXTURE:
        alpha = texel[3] / 255.0f;
        for (unsigned int channel = 0; channel < 3; channel++) {
            result[channel] = incoming[channel] * (1.0f - alpha) +
                              texel[channel] * alpha;
        }
        break;
    default:
        return false;
    }

    alpha = alpha_factor == ATI_3D_COMB_ALPHA_FACTOR_TEX ?
            texel[3] : 255.0f - texel[3];
    switch (alpha_function) {
    case ATI_3D_COMB_ALPHA_TEXTURE:
        result[3] = texel[3];
        break;
    case ATI_3D_COMB_ALPHA_INPUT:
        result[3] = incoming[3];
        break;
    case ATI_3D_COMB_ALPHA_MODULATE:
        result[3] = incoming[3] * alpha / 255.0f;
        break;
    case ATI_3D_COMB_ALPHA_ADD:
        result[3] = incoming[3] + alpha;
        break;
    default:
        return false;
    }

    for (unsigned int channel = 0; channel < 4; channel++) {
        result[channel] = MIN(MAX(result[channel], 0.0f), 255.0f);
    }
    return true;
}

static bool ati_3d_surface_address(const ATIVGAState *s,
                                   const ATI3DSurface *surface,
                                   int x, int y, uint64_t *address)
{
    uint64_t offset;

    if (x < 0 || y < 0 || (uint32_t)x >= surface->width) {
        return false;
    }
    offset = (uint64_t)surface->offset +
             (uint64_t)(unsigned int)y * surface->stride +
             (uint64_t)(unsigned int)x * surface->bytes_per_pixel;
    if (offset + surface->bytes_per_pixel > s->vga.vram_size) {
        return false;
    }
    *address = offset;
    return true;
}

static ATI3DRect ati_3d_main_scissor(const ATIVGAState *s)
{
    uint32_t top_left = ati_3d_reg(s, SC_TOP_LEFT_C);
    uint32_t bottom_right = ati_3d_reg(s, SC_BOTTOM_RIGHT_C);
    ATI3DRect rect = {
        .left = top_left & 0x1fff,
        .top = (top_left >> 16) & 0x1fff,
        .right = bottom_right & 0x1fff,
        .bottom = (bottom_right >> 16) & 0x1fff,
    };
    uint32_t re_top_left = s->accel3d.re_top_left;
    uint32_t re_width_height = ati_3d_reg(s, RE_WIDTH_HEIGHT);

    if (re_width_height) {
        int left = re_top_left & 0xffff;
        int top = (re_top_left >> 16) & 0xffff;
        int right = left + (re_width_height & 0xffff) - 1;
        int bottom = top + ((re_width_height >> 16) & 0xffff) - 1;

        rect.left = MAX(rect.left, left);
        rect.top = MAX(rect.top, top);
        rect.right = MIN(rect.right, right);
        rect.bottom = MIN(rect.bottom, bottom);
    }
    return rect;
}

static bool ati_3d_aux_scissor_pass(const ATIVGAState *s, int x, int y)
{
    uint32_t control = s->accel3d.aux_sc_cntl;
    bool any_enabled = control &
        (ATI_3D_AUX1_ENABLE | ATI_3D_AUX2_ENABLE | ATI_3D_AUX3_ENABLE);

    if (!any_enabled) {
        return true;
    }
    for (unsigned int i = 0; i < 3; i++) {
        if (!(control & BIT(i * 2))) {
            continue;
        }
        if (x >= (int)(s->accel3d.aux_sc[i][0] & 0x1fff) &&
            x <= (int)(s->accel3d.aux_sc[i][1] & 0x1fff) &&
            y >= (int)(s->accel3d.aux_sc[i][2] & 0x1fff) &&
            y <= (int)(s->accel3d.aux_sc[i][3] & 0x1fff)) {
            return true;
        }
    }
    return false;
}

bool ati_3d_surface_fill(ATIVGAState *s, uint32_t master,
                         uint32_t pitch_offset, uint32_t color,
                         uint32_t dst_xy, uint32_t width_height)
{
    ATI3DSurface surface;
    unsigned int x = dst_xy >> 16;
    unsigned int y = dst_xy & 0xffff;
    unsigned int width = width_height >> 16;
    unsigned int height = width_height & 0xffff;
    uint8_t rop = (master >> 16) & 0xff;
    uint32_t write_mask = master & R128_GMC_WR_MSK_DIS ?
                          UINT32_MAX : s->regs.dp_write_mask;
    uint64_t dirty_start = UINT64_MAX;
    uint64_t dirty_end = 0;

    if (!ati_3d_decode_surface(master, pitch_offset, &surface)) {
        return false;
    }
    /*
     * Rage 128 tiled depth is canonicalized to a linear software backing.
     * Every software 3D access and PM4 depth clear uses the same layout, so
     * this is a coherent device model rather than a one-off clear hack.
     */
    for (unsigned int row = 0; row < height; row++) {
        for (unsigned int col = 0; col < width; col++) {
            uint64_t address;
            uint32_t old;
            uint32_t result;

            if (!ati_3d_surface_address(s, &surface, x + col, y + row,
                                        &address)) {
                continue;
            }
            old = ati_3d_load_pixel(s, &surface, address);
            result = ati_rop3_eval(rop, color, 0, old);
            ati_3d_store_pixel(s, &surface, address, result, write_mask);
            dirty_start = MIN(dirty_start, address);
            dirty_end = MAX(dirty_end,
                            address + surface.bytes_per_pixel);
        }
    }
    if (dirty_start < dirty_end) {
        memory_region_set_dirty(&s->vga.vram, dirty_start,
                                dirty_end - dirty_start);
    }
    return true;
}

static unsigned int ati_3d_vertex_stride(uint32_t format)
{
    unsigned int dwords = 3;

    if (format & ATI_3D_VERTEX_RHW) {
        dwords++;
    }
    if (format & (ATI_3D_VERTEX_DIFFUSE_BGR |
                  ATI_3D_VERTEX_DIFFUSE_A |
                  ATI_3D_VERTEX_DIFFUSE_ARGB)) {
        dwords++;
    }
    if (format & (ATI_3D_VERTEX_SPEC_BGR |
                  ATI_3D_VERTEX_SPEC_F |
                  ATI_3D_VERTEX_SPEC_FRGB)) {
        dwords++;
    }
    if (format & ATI_3D_VERTEX_ST) {
        dwords += 2;
    }
    if (format & ATI_3D_VERTEX_S2T2) {
        dwords += 2;
    }
    if (format & ATI_3D_VERTEX_RHW2) {
        dwords++;
    }
    return dwords;
}

static float ati_3d_u32_to_float(uint32_t value)
{
    float result;

    memcpy(&result, &value, sizeof(result));
    return result;
}

static void ati_3d_unpack_vertex_color(uint32_t packed, float color[4])
{
    /* Mesa's little-endian r128 vertices store bytes R,G,B,A/F. */
    color[0] = packed & 0xff;
    color[1] = (packed >> 8) & 0xff;
    color[2] = (packed >> 16) & 0xff;
    color[3] = (packed >> 24) & 0xff;
}

static bool ati_3d_read_vertex(ATIVGAState *s, dma_addr_t address,
                               uint32_t format, unsigned int stride,
                               ATI3DVertex *vertex)
{
    uint32_t words[12];
    unsigned int index = 3;
    uint32_t diffuse = UINT32_C(0xffffffff);
    uint32_t specular = 0;

    if (stride > G_N_ELEMENTS(words) ||
        !ati_pm4_read_guest(s, address, words, stride * sizeof(uint32_t))) {
        return false;
    }
    for (unsigned int i = 0; i < stride; i++) {
        words[i] = le32_to_cpu(words[i]);
    }
    vertex->x = ati_3d_u32_to_float(words[0]);
    vertex->y = ati_3d_u32_to_float(words[1]);
    vertex->z = ati_3d_u32_to_float(words[2]);
    vertex->rhw = 1.0f;
    if (format & ATI_3D_VERTEX_RHW) {
        vertex->rhw = ati_3d_u32_to_float(words[index++]);
    }
    if (format & (ATI_3D_VERTEX_DIFFUSE_BGR |
                  ATI_3D_VERTEX_DIFFUSE_A |
                  ATI_3D_VERTEX_DIFFUSE_ARGB)) {
        diffuse = words[index++];
    }

    ati_3d_unpack_vertex_color(diffuse, vertex->color);

    if (format & (ATI_3D_VERTEX_SPEC_BGR |
                  ATI_3D_VERTEX_SPEC_F |
                  ATI_3D_VERTEX_SPEC_FRGB)) {
        specular = words[index++];
    }
    ati_3d_unpack_vertex_color(specular, vertex->specular);
    memset(vertex->texcoord, 0, sizeof(vertex->texcoord));
    if (format & ATI_3D_VERTEX_ST) {
        vertex->texcoord[0][0] = ati_3d_u32_to_float(words[index++]);
        vertex->texcoord[0][1] = ati_3d_u32_to_float(words[index++]);
    }
    if (format & ATI_3D_VERTEX_S2T2) {
        vertex->texcoord[1][0] = ati_3d_u32_to_float(words[index++]);
        vertex->texcoord[1][1] = ati_3d_u32_to_float(words[index++]);
    }
    if (format & ATI_3D_VERTEX_RHW2) {
        if (!isfinite(ati_3d_u32_to_float(words[index++]))) {
            return false;
        }
    }
    return index == stride &&
           isfinite(vertex->x) && isfinite(vertex->y) &&
           isfinite(vertex->z) && isfinite(vertex->rhw) &&
           isfinite(vertex->texcoord[0][0]) &&
           isfinite(vertex->texcoord[0][1]) &&
           isfinite(vertex->texcoord[1][0]) &&
           isfinite(vertex->texcoord[1][1]);
}

static float ati_3d_edge(const ATI3DVertex *a, const ATI3DVertex *b,
                         float x, float y)
{
    return (x - a->x) * (b->y - a->y) -
           (y - a->y) * (b->x - a->x);
}

static bool ati_3d_alpha_compare(unsigned int function,
                                 uint8_t source, uint8_t reference)
{
    switch (function) {
    case 0:
        return false;
    case 1:
        return source < reference;
    case 2:
        return source <= reference;
    case 3:
        return source == reference;
    case 4:
        return source >= reference;
    case 5:
        return source > reference;
    case 6:
        return source != reference;
    case 7:
        return true;
    default:
        return false;
    }
}

static float ati_3d_blend_factor(unsigned int factor, unsigned int channel,
                                 const float src[4], const float dst[4])
{
    switch (factor) {
    case 0:
        return 0.0f;
    case 1:
        return 1.0f;
    case 2:
        return src[channel] / 255.0f;
    case 3:
        return 1.0f - src[channel] / 255.0f;
    case 4:
        return src[3] / 255.0f;
    case 5:
        return 1.0f - src[3] / 255.0f;
    case 6:
        return dst[3] / 255.0f;
    case 7:
        return 1.0f - dst[3] / 255.0f;
    case 8:
        return dst[channel] / 255.0f;
    case 9:
        return 1.0f - dst[channel] / 255.0f;
    case 10:
        return channel == 3 ? 1.0f :
               MIN(src[3], 255.0f - dst[3]) / 255.0f;
    default:
        return 0.0f;
    }
}

static bool ati_3d_blend(uint32_t misc, const float src[4],
                         const float dst[4], float result[4])
{
    unsigned int src_factor = (misc >> ATI_3D_ALPHA_SRC_SHIFT) & 0xf;
    unsigned int dst_factor = (misc >> ATI_3D_ALPHA_DST_SHIFT) & 0xf;

    if (src_factor > 10 || dst_factor > 10 ||
        (misc & ATI_3D_ALPHA_COMB_MASK)) {
        return false;
    }
    for (unsigned int channel = 0; channel < 4; channel++) {
        float sf = ati_3d_blend_factor(src_factor, channel, src, dst);
        float df = ati_3d_blend_factor(dst_factor, channel, src, dst);

        result[channel] = src[channel] * sf + dst[channel] * df;
    }
    return true;
}

static bool ati_3d_depth_compare(uint32_t function,
                                 uint32_t source, uint32_t destination)
{
    switch (function) {
    case 0:
        return false;
    case 1:
        return source < destination;
    case 2:
        return source <= destination;
    case 3:
        return source == destination;
    case 4:
        return source >= destination;
    case 5:
        return source > destination;
    case 6:
        return source != destination;
    case 7:
        return true;
    default:
        return false;
    }
}

static bool ati_3d_depth_surface(const ATIVGAState *s,
                                 ATI3DSurface *surface,
                                 uint32_t *depth_mask,
                                 uint32_t *depth_function)
{
    uint32_t z_offset = ati_3d_reg(s, Z_OFFSET_C);
    uint32_t z_pitch = ati_3d_reg(s, Z_PITCH_C);
    uint32_t z_control = ati_3d_reg(s, Z_STEN_CNTL_C);
    unsigned int bytes;

    memset(surface, 0, sizeof(*surface));
    switch (z_control & ATI_3D_Z_PIX_WIDTH_MASK) {
    case 0U << 1:
        surface->bpp = 16;
        bytes = 2;
        *depth_mask = 0xffff;
        break;
    case 1U << 1:
        surface->bpp = 24;
        bytes = 4;
        *depth_mask = 0x00ffffff;
        break;
    case 2U << 1:
        surface->bpp = 32;
        bytes = 4;
        *depth_mask = UINT32_MAX;
        break;
    default:
        return false;
    }
    surface->bytes_per_pixel = bytes;
    surface->pixel_mask = *depth_mask;
    surface->offset = z_offset & 0x07ffffff;
    surface->width = (z_pitch & 0xffff) * 8;
    surface->stride = surface->width * bytes;
    surface->tiled = z_pitch & ATI_3D_Z_TILE;
    *depth_function = (z_control & ATI_3D_Z_TEST_MASK) >> 4;
    return surface->stride != 0;
}

static uint32_t ati_3d_float_depth(float value, uint32_t mask)
{
    if (value <= 0.0f) {
        return 0;
    }
    if (value >= mask) {
        return mask;
    }
    return value + 0.5f;
}

static unsigned int ati_3d_face_mode(const ATIVGAState *s,
                                     float signed_area)
{
    uint32_t setup = s->pm4.vc_fpu_setup;
    bool ccw_front = setup & ATI_3D_FRONT_DIR_CCW;
    bool ccw = signed_area < 0.0f;
    bool front = ccw == ccw_front;

    return front ? (setup & ATI_3D_FRONTFACE_MASK) >> 3 :
                   (setup & ATI_3D_BACKFACE_MASK) >> 1;
}

static void ati_3d_shade_state_init(const ATIVGAState *s,
                                    ATI3DShadeState *shade)
{
    shade->mode = s->pm4.vc_fpu_setup & ATI_3D_COLOR_MODE_MASK;
    shade->flat_last = s->pm4.vc_fpu_setup &
                       ATI_3D_FLAT_SHADE_VERTEX_OGL;
    shade->specular_enabled = ati_3d_reg(s, TEX_CNTL_C) &
                              ATI_3D_SPEC_LIGHT_ENABLE;
    ati_3d_unpack_vertex_color(ati_3d_reg(s, SOLID_COLOR), shade->solid);
}

static void ati_3d_shade_sample(const ATI3DShadeState *shade,
                                const ATI3DVertex *vertices,
                                const float *weights, unsigned int count,
                                float result[4])
{
    float specular[4] = { 0 };

    if (shade->mode == ATI_3D_COLOR_SOLID) {
        memcpy(result, shade->solid, sizeof(shade->solid));
    } else if (shade->mode == ATI_3D_COLOR_FLAT) {
        unsigned int index = shade->flat_last ? count - 1 : 0;

        memcpy(result, vertices[index].color, sizeof(vertices[index].color));
        memcpy(specular, vertices[index].specular,
               sizeof(vertices[index].specular));
    } else {
        memset(result, 0, 4 * sizeof(*result));
        for (unsigned int i = 0; i < count; i++) {
            for (unsigned int channel = 0; channel < 4; channel++) {
                result[channel] += vertices[i].color[channel] * weights[i];
                specular[channel] +=
                    vertices[i].specular[channel] * weights[i];
            }
        }
    }

    if (shade->specular_enabled && shade->mode != ATI_3D_COLOR_SOLID) {
        for (unsigned int channel = 0; channel < 3; channel++) {
            result[channel] = MIN(result[channel] + specular[channel],
                                  255.0f);
        }
    }
}

static bool ati_3d_texture_coord(const ATI3DVertex *vertices,
                                 const float *weights, unsigned int count,
                                 float result[2])
{
    double denominator = 0.0;
    double numerator_s = 0.0;
    double numerator_t = 0.0;

    for (unsigned int i = 0; i < count; i++) {
        double weighted_rhw = (double)weights[i] * vertices[i].rhw;

        denominator += weighted_rhw;
        numerator_s += weighted_rhw * vertices[i].texcoord[0][0];
        numerator_t += weighted_rhw * vertices[i].texcoord[0][1];
    }
    if (!isfinite(denominator) || fabs(denominator) < 1.0e-20) {
        return false;
    }
    result[0] = numerator_s / denominator;
    result[1] = numerator_t / denominator;
    return isfinite(result[0]) && isfinite(result[1]);
}

static bool ati_3d_edge_is_top_left(const ATI3DVertex *a,
                                    const ATI3DVertex *b)
{
    float dx = b->x - a->x;
    float dy = b->y - a->y;

    /* Screen-space Y grows downward and inside edges evaluate positive. */
    return dy > 0.0f || (dy == 0.0f && dx < 0.0f);
}

static bool ati_3d_edge_accept(float value, bool top_left)
{
    return value > 0.0f || (value == 0.0f && top_left);
}

static bool ati_3d_fragment_context_init(ATI3DFragmentContext *ctx,
                                         ATIVGAState *s,
                                         ATI3DSurface *surface)
{
    memset(ctx, 0, sizeof(*ctx));
    ctx->s = s;
    ctx->color_surface = surface;
    ctx->tex_control = ati_3d_reg(s, TEX_CNTL_C);
    ctx->misc = ati_3d_reg(s, MISC_3D_STATE_CNTL_REG);
    ctx->write_mask = ati_3d_reg(s, PLANE_3D_MASK_C) &
                      s->regs.dp_write_mask;
    ctx->color_dirty_start = UINT64_MAX;
    ctx->depth_dirty_start = UINT64_MAX;
    ctx->texture_enabled = ctx->tex_control & ATI_3D_TEXMAP_ENABLE;
    ctx->depth_enabled = ctx->tex_control & ATI_3D_TEX_Z_ENABLE;

    if (ctx->texture_enabled && !ati_3d_texture_decode(s, &ctx->texture)) {
        return false;
    }
    if (ctx->tex_control & ATI_3D_ALPHA_ENABLE) {
        unsigned int src_factor =
            (ctx->misc >> ATI_3D_ALPHA_SRC_SHIFT) & 0xf;
        unsigned int dst_factor =
            (ctx->misc >> ATI_3D_ALPHA_DST_SHIFT) & 0xf;

        if (src_factor > 10 || dst_factor > 10 ||
            (ctx->misc & ATI_3D_ALPHA_COMB_MASK)) {
            qemu_log_mask(LOG_UNIMP,
                          "ATI Rage 128 3D unsupported blend equation/factor\n");
            return false;
        }
    }
    if (ctx->depth_enabled &&
        !ati_3d_depth_surface(s, &ctx->depth_surface, &ctx->depth_mask,
                              &ctx->depth_function)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D: invalid depth surface\n");
        return false;
    }
    return true;
}

static bool ati_3d_fragment(ATI3DFragmentContext *ctx, int x, int y,
                            const float src[4], float z,
                            const float texcoord[2])
{
    ATIVGAState *s = ctx->s;
    float textured[4];
    const float *fragment = src;
    uint64_t color_address;
    uint32_t color_pixel;

    if (!ati_3d_surface_address(s, ctx->color_surface, x, y,
                                &color_address)) {
        return true;
    }
    if (ctx->texture_enabled) {
        float texel[4];

        if (!ati_3d_texture_sample(s, &ctx->texture, texcoord, texel) ||
            !ati_3d_texture_combine(&ctx->texture, src, texel, textured)) {
            return false;
        }
        fragment = textured;
    }
    if ((ctx->tex_control & ATI_3D_ALPHA_TEST_ENABLE) &&
        !ati_3d_alpha_compare((ctx->misc >> ATI_3D_ALPHA_TEST_SHIFT) & 7,
                              ati_3d_clamp_channel(fragment[3]),
                              ctx->misc & 0xff)) {
        return true;
    }

    if (ctx->depth_enabled) {
        uint64_t depth_address;
        uint32_t source_depth;
        uint32_t destination_depth;

        if (!ati_3d_surface_address(s, &ctx->depth_surface, x, y,
                                    &depth_address)) {
            return true;
        }
        source_depth = ati_3d_float_depth(z, ctx->depth_mask);
        destination_depth = ati_3d_load_pixel(s, &ctx->depth_surface,
                                              depth_address) &
                            ctx->depth_mask;
        if (!ati_3d_depth_compare(ctx->depth_function, source_depth,
                                  destination_depth)) {
            return true;
        }
        if (ctx->tex_control & ATI_3D_TEX_Z_WRITE_ENABLE) {
            ati_3d_store_pixel(s, &ctx->depth_surface, depth_address,
                               source_depth, ctx->depth_mask);
            ctx->depth_dirty_start = MIN(ctx->depth_dirty_start,
                                         depth_address);
            ctx->depth_dirty_end = MAX(ctx->depth_dirty_end,
                                       depth_address +
                                       ctx->depth_surface.bytes_per_pixel);
        }
    }

    color_pixel = ati_3d_load_pixel(s, ctx->color_surface, color_address);
    if (ctx->tex_control & ATI_3D_ALPHA_ENABLE) {
        float dst[4];
        float blended[4];

        ati_3d_unpack_surface_color(ctx->color_surface, color_pixel, dst);
        if (!ati_3d_blend(ctx->misc, fragment, dst, blended)) {
            qemu_log_mask(LOG_UNIMP,
                          "ATI Rage 128 3D blend equation/factor is not implemented\n");
            return false;
        }
        color_pixel = ati_3d_pack_surface_color(ctx->color_surface, blended);
    } else {
        color_pixel = ati_3d_pack_surface_color(ctx->color_surface, fragment);
    }
    ati_3d_store_pixel(s, ctx->color_surface, color_address, color_pixel,
                       ctx->write_mask);
    ctx->color_dirty_start = MIN(ctx->color_dirty_start, color_address);
    ctx->color_dirty_end = MAX(ctx->color_dirty_end,
                               color_address +
                               ctx->color_surface->bytes_per_pixel);
    return true;
}

static void ati_3d_fragment_context_finish(ATI3DFragmentContext *ctx)
{
    if (ctx->color_dirty_start < ctx->color_dirty_end) {
        memory_region_set_dirty(&ctx->s->vga.vram, ctx->color_dirty_start,
                                ctx->color_dirty_end -
                                ctx->color_dirty_start);
    }
    if (ctx->depth_dirty_start < ctx->depth_dirty_end) {
        memory_region_set_dirty(&ctx->s->vga.vram, ctx->depth_dirty_start,
                                ctx->depth_dirty_end -
                                ctx->depth_dirty_start);
    }
}

static int ati_3d_clamp_floor_to_int(float value, int lower, int upper)
{
    if (value <= lower) {
        return lower;
    }
    if (value >= upper) {
        return upper;
    }
    return floorf(value);
}

static int ati_3d_clamp_ceil_to_int(float value, int lower, int upper)
{
    if (value <= lower) {
        return lower;
    }
    if (value >= upper) {
        return upper;
    }
    return ceilf(value);
}

static bool ati_3d_clip_test(float p, float q, float *first, float *last)
{
    float ratio;

    if (p == 0.0f) {
        return q >= 0.0f;
    }
    ratio = q / p;
    if (p < 0.0f) {
        if (ratio > *last) {
            return false;
        }
        *first = MAX(*first, ratio);
    } else {
        if (ratio < *first) {
            return false;
        }
        *last = MIN(*last, ratio);
    }
    return true;
}

static bool ati_3d_clip_line(const ATI3DRect *rect,
                             const ATI3DVertex *a,
                             const ATI3DVertex *b,
                             float *first, float *last)
{
    float dx = b->x - a->x;
    float dy = b->y - a->y;

    *first = 0.0f;
    *last = 1.0f;
    return ati_3d_clip_test(-dx, a->x - rect->left, first, last) &&
           ati_3d_clip_test(dx, rect->right - a->x, first, last) &&
           ati_3d_clip_test(-dy, a->y - rect->top, first, last) &&
           ati_3d_clip_test(dy, rect->bottom - a->y, first, last);
}

static bool ati_3d_draw_line(ATIVGAState *s, ATI3DSurface *surface,
                             const ATI3DVertex *a, const ATI3DVertex *b);
static bool ati_3d_draw_point(ATIVGAState *s, ATI3DSurface *surface,
                              const ATI3DVertex *vertex);

static bool ati_3d_draw_triangle(ATIVGAState *s, ATI3DSurface *surface,
                                 ATI3DVertex v0, ATI3DVertex v1,
                                 ATI3DVertex v2)
{
    ATI3DRect scissor = ati_3d_main_scissor(s);
    ATI3DFragmentContext fragments;
    ATI3DShadeState shade;
    ATI3DVertex original_vertices[3] = { v0, v1, v2 };
    ATI3DVertex vertices[3];
    bool result = false;
    float area = ati_3d_edge(&v0, &v1, v2.x, v2.y);
    float min_vertex_x;
    float min_vertex_y;
    float max_vertex_x;
    float max_vertex_y;
    int min_x;
    int min_y;
    int max_x;
    int max_y;

    if (!isfinite(area)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D triangle area overflowed\n");
        return false;
    }
    if (fabsf(area) < 1.0e-8f) {
        return true;
    }
    ati_3d_shade_state_init(s, &shade);
    if (shade.mode == ATI_3D_COLOR_FLAT) {
        const ATI3DVertex *provoking =
            &original_vertices[shade.flat_last ? 2 : 0];

        for (unsigned int i = 0; i < 3; i++) {
            memcpy(original_vertices[i].color, provoking->color,
                   sizeof(provoking->color));
            memcpy(original_vertices[i].specular, provoking->specular,
                   sizeof(provoking->specular));
        }
        v0 = original_vertices[0];
        v1 = original_vertices[1];
        v2 = original_vertices[2];
    }
    switch (ati_3d_face_mode(s, area)) {
    case ATI_3D_FACE_CULL:
        return true;
    case ATI_3D_FACE_POINTS:
        return ati_3d_draw_point(s, surface, &v0) &&
               ati_3d_draw_point(s, surface, &v1) &&
               ati_3d_draw_point(s, surface, &v2);
    case ATI_3D_FACE_LINES:
        return ati_3d_draw_line(s, surface, &v0, &v1) &&
               ati_3d_draw_line(s, surface, &v1, &v2) &&
               ati_3d_draw_line(s, surface, &v2, &v0);
    case ATI_3D_FACE_SOLID:
        break;
    default:
        return false;
    }
    if (area < 0.0f) {
        ATI3DVertex tmp = v1;
        v1 = v2;
        v2 = tmp;
        area = -area;
    }
    vertices[0] = v0;
    vertices[1] = v1;
    vertices[2] = v2;

    min_vertex_x = MIN(v0.x, MIN(v1.x, v2.x));
    min_vertex_y = MIN(v0.y, MIN(v1.y, v2.y));
    max_vertex_x = MAX(v0.x, MAX(v1.x, v2.x));
    max_vertex_y = MAX(v0.y, MAX(v1.y, v2.y));
    if (max_vertex_x < scissor.left || max_vertex_y < scissor.top ||
        min_vertex_x > scissor.right || min_vertex_y > scissor.bottom) {
        return true;
    }
    min_x = ati_3d_clamp_floor_to_int(min_vertex_x,
                                      scissor.left, scissor.right);
    min_y = ati_3d_clamp_floor_to_int(min_vertex_y,
                                      scissor.top, scissor.bottom);
    max_x = ati_3d_clamp_ceil_to_int(max_vertex_x,
                                     scissor.left, scissor.right);
    max_y = ati_3d_clamp_ceil_to_int(max_vertex_y,
                                     scissor.top, scissor.bottom);
    if ((uint64_t)(max_x - min_x + 1) *
        (uint64_t)(max_y - min_y + 1) > ATI_3D_MAX_RASTER_PIXELS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D triangle raster area is too large\n");
        return false;
    }

    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    bool edge0_top_left = ati_3d_edge_is_top_left(&v1, &v2);
    bool edge1_top_left = ati_3d_edge_is_top_left(&v2, &v0);
    bool edge2_top_left = ati_3d_edge_is_top_left(&v0, &v1);

    for (int y = min_y; y <= max_y; y++) {
        for (int x = min_x; x <= max_x; x++) {
            float sample_x = x + 0.5f;
            float sample_y = y + 0.5f;
            float w0 = ati_3d_edge(&v1, &v2, sample_x, sample_y);
            float w1 = ati_3d_edge(&v2, &v0, sample_x, sample_y);
            float w2 = ati_3d_edge(&v0, &v1, sample_x, sample_y);
            float src[4];
            float texcoord[2];
            const float *texture_coord = NULL;
            float z;

            if (!isfinite(w0) || !isfinite(w1) || !isfinite(w2)) {
                qemu_log_mask(LOG_GUEST_ERROR,
                              "ATI Rage 128 3D barycentric overflow\n");
                goto out;
            }
            if (!ati_3d_edge_accept(w0, edge0_top_left) ||
                !ati_3d_edge_accept(w1, edge1_top_left) ||
                !ati_3d_edge_accept(w2, edge2_top_left) ||
                !ati_3d_aux_scissor_pass(s, x, y)) {
                continue;
            }
            w0 /= area;
            w1 /= area;
            w2 /= area;
            {
                const float weights[3] = { w0, w1, w2 };

                ati_3d_shade_sample(&shade,
                                    shade.mode == ATI_3D_COLOR_FLAT ?
                                    original_vertices : vertices,
                                    weights, 3, src);
                if (fragments.texture_enabled) {
                    if (!ati_3d_texture_coord(vertices, weights, 3,
                                              texcoord)) {
                        goto out;
                    }
                    texture_coord = texcoord;
                }
            }
            z = v0.z * w0 + v1.z * w1 + v2.z * w2;
            if (!ati_3d_fragment(&fragments, x, y, src, z,
                                 texture_coord)) {
                goto out;
            }
        }
    }
    result = true;

out:
    ati_3d_fragment_context_finish(&fragments);
    if (result) {
        s->pm4.primitives_drawn++;
    }
    return result;
}

static bool ati_3d_draw_line(ATIVGAState *s, ATI3DSurface *surface,
                             const ATI3DVertex *a, const ATI3DVertex *b)
{
    ATI3DRect scissor = ati_3d_main_scissor(s);
    ATI3DFragmentContext fragments;
    ATI3DShadeState shade;
    ATI3DVertex vertices[2] = { *a, *b };
    bool result = false;
    float dx = b->x - a->x;
    float dy = b->y - a->y;
    float first;
    float last;
    float clipped_dx;
    float clipped_dy;
    float span;
    unsigned int steps;

    if (!isfinite(dx) || !isfinite(dy)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D line delta overflowed\n");
        return false;
    }
    if (!ati_3d_clip_line(&scissor, a, b, &first, &last)) {
        return true;
    }
    clipped_dx = dx * (last - first);
    clipped_dy = dy * (last - first);
    span = MAX(fabsf(clipped_dx), fabsf(clipped_dy));
    if (!isfinite(span) ||
        span > (float)ATI_3D_MAX_RASTER_PIXELS) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D line is too large\n");
        return false;
    }
    steps = MAX((unsigned int)ceilf(span), 1U);
    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    ati_3d_shade_state_init(s, &shade);
    for (unsigned int i = 0; i <= steps; i++) {
        float local = (float)i / steps;
        float t = first + (last - first) * local;
        float sample_x = a->x + dx * t;
        float sample_y = a->y + dy * t;
        int x = sample_x + 0.5f;
        int y = sample_y + 0.5f;
        float color[4];
        float texcoord[2];
        const float *texture_coord = NULL;
        float z;

        if (!ati_3d_aux_scissor_pass(s, x, y)) {
            continue;
        }
        {
            const float weights[2] = { 1.0f - t, t };

            ati_3d_shade_sample(&shade, vertices, weights, 2, color);
            if (fragments.texture_enabled) {
                if (!ati_3d_texture_coord(vertices, weights, 2, texcoord)) {
                    goto out;
                }
                texture_coord = texcoord;
            }
        }
        z = a->z * (1.0f - t) + b->z * t;
        if (!ati_3d_fragment(&fragments, x, y, color, z,
                             texture_coord)) {
            goto out;
        }
    }
    result = true;

out:
    ati_3d_fragment_context_finish(&fragments);
    if (result) {
        s->pm4.primitives_drawn++;
    }
    return result;
}

static bool ati_3d_draw_point(ATIVGAState *s, ATI3DSurface *surface,
                              const ATI3DVertex *vertex)
{
    ATI3DRect scissor = ati_3d_main_scissor(s);
    ATI3DFragmentContext fragments;
    ATI3DShadeState shade;
    const float weight = 1.0f;
    float color[4];
    bool result;
    int x;
    int y;

    if (vertex->x < scissor.left || vertex->x > scissor.right ||
        vertex->y < scissor.top || vertex->y > scissor.bottom) {
        return true;
    }
    x = vertex->x + 0.5f;
    y = vertex->y + 0.5f;
    if (!ati_3d_aux_scissor_pass(s, x, y)) {
        return true;
    }
    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    ati_3d_shade_state_init(s, &shade);
    ati_3d_shade_sample(&shade, vertex, &weight, 1, color);
    result = ati_3d_fragment(
        &fragments, x, y, color, vertex->z,
        fragments.texture_enabled ? vertex->texcoord[0] : NULL);
    ati_3d_fragment_context_finish(&fragments);
    if (result) {
        s->pm4.primitives_drawn++;
    }
    return result;
}

bool ati_3d_draw_indexed(ATIVGAState *s, uint32_t address,
                         uint32_t size, uint32_t format,
                         uint32_t vc_cntl,
                         const uint32_t *index_words,
                         unsigned int index_dwords)
{
    uint32_t tex_control = ati_3d_reg(s, TEX_CNTL_C);
    uint32_t master = ati_3d_reg(s, DP_GUI_MASTER_CNTL_C);
    uint32_t pitch_offset = ati_3d_reg(s, DST_PITCH_OFFSET_C);
    unsigned int primitive = vc_cntl & ATI_3D_VC_PRIM_MASK;
    unsigned int walk = vc_cntl & ATI_3D_VC_WALK_MASK;
    unsigned int count = vc_cntl >> ATI_3D_VC_COUNT_SHIFT;
    unsigned int stride = ati_3d_vertex_stride(format);
    uint32_t window_offset = ati_3d_reg(s, WINDOW_XY_OFFSET);
    int window_x = sextract32(window_offset, 20, 12);
    int window_y = sextract32(window_offset, 4, 12);
    ATI3DSurface surface;
    ATI3DVertex *vertices = NULL;
    bool result = false;

    if (walk != ATI_3D_WALK_LIST && walk != ATI_3D_WALK_IND) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 3D vertex walk mode 0x%x is not implemented\n",
                      walk);
        return false;
    }
    if (!count || count > ATI_3D_MAX_VERTICES || !stride) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D invalid vertex count/format\n");
        return false;
    }
    if (walk == ATI_3D_WALK_LIST && size && size < count) {
        /* The historical list packet carries the vertex count twice. */
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D vertex packet count is inconsistent\n");
        return false;
    }
    if (walk == ATI_3D_WALK_IND &&
        (!index_words || index_dwords < (count + 1U) / 2U)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D indexed packet is truncated\n");
        return false;
    }
    if (tex_control & (ATI_3D_SEC_TEXMAP_ENABLE |
                       ATI_3D_FOG_ENABLE |
                       ATI_3D_TEX_STENCIL_ENABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 secondary texture, fog, or stencil 3D is not implemented\n");
        return false;
    }
    if (tex_control & ATI_3D_TEXMAP_ENABLE) {
        ATI3DTexture texture;

        if (!(format & ATI_3D_VERTEX_RHW) ||
            !(format & ATI_3D_VERTEX_ST)) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "ATI Rage 128 textured vertices require RHW and ST\n");
            return false;
        }
        if (!ati_3d_texture_decode(s, &texture)) {
            return false;
        }
    }
    if (!ati_3d_decode_surface(master, pitch_offset, &surface)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D invalid destination surface\n");
        return false;
    }
    if (surface.bpp == 8) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 8-bpp color-indexed 3D is not implemented\n");
        return false;
    }
    if ((tex_control & ATI_3D_ALPHA_ENABLE) &&
        ((ati_3d_reg(s, MISC_3D_STATE_CNTL_REG) & ATI_3D_ALPHA_COMB_MASK) ||
         ((ati_3d_reg(s, MISC_3D_STATE_CNTL_REG) >>
           ATI_3D_ALPHA_SRC_SHIFT) & 0xf) > 10 ||
         ((ati_3d_reg(s, MISC_3D_STATE_CNTL_REG) >>
           ATI_3D_ALPHA_DST_SHIFT) & 0xf) > 10)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 3D blend state is not implemented\n");
        return false;
    }

    vertices = g_new0(ATI3DVertex, count);
    for (unsigned int i = 0; i < count; i++) {
        unsigned int vertex_index = i;

        if (walk == ATI_3D_WALK_IND) {
            uint32_t packed = index_words[i / 2];

            vertex_index = (i & 1) ? packed >> 16 : packed & 0xffff;
            if (size && vertex_index >= size) {
                qemu_log_mask(LOG_GUEST_ERROR,
                              "ATI Rage 128 3D vertex index %u exceeds %u\n",
                              vertex_index, size);
                goto out;
            }
        }
        if (!ati_3d_read_vertex(s,
                                address + (dma_addr_t)vertex_index * stride * 4,
                                format, stride, &vertices[i])) {
            goto out;
        }
        if ((tex_control & ATI_3D_TEXMAP_ENABLE) &&
            vertices[i].rhw <= 0.0f) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "ATI Rage 128 textured vertex has non-positive RHW\n");
            goto out;
        }
        vertices[i].x += window_x;
        vertices[i].y += window_y;
    }

    switch (primitive) {
    case ATI_3D_PRIM_POINT:
        for (unsigned int i = 0; i < count; i++) {
            if (!ati_3d_draw_point(s, &surface, &vertices[i])) {
                goto out;
            }
        }
        break;
    case ATI_3D_PRIM_LINE:
    case ATI_3D_PRIM_POLYLINE:
        for (unsigned int i = 1; i < count; i +=
             primitive == ATI_3D_PRIM_LINE ? 2 : 1) {
            if (!ati_3d_draw_line(s, &surface, &vertices[i - 1],
                                  &vertices[i])) {
                goto out;
            }
        }
        break;
    case ATI_3D_PRIM_TRI_LIST:
    case ATI_3D_PRIM_TRI_TYPE2:
        for (unsigned int i = 2; i < count; i += 3) {
            if (!ati_3d_draw_triangle(s, &surface, vertices[i - 2],
                                      vertices[i - 1], vertices[i])) {
                goto out;
            }
        }
        break;
    case ATI_3D_PRIM_TRI_FAN:
        for (unsigned int i = 2; i < count; i++) {
            if (!ati_3d_draw_triangle(s, &surface, vertices[0],
                                      vertices[i - 1], vertices[i])) {
                goto out;
            }
        }
        break;
    case ATI_3D_PRIM_TRI_STRIP:
        for (unsigned int i = 2; i < count; i++) {
            ATI3DVertex a = vertices[i - 2];
            ATI3DVertex b = vertices[i - 1];

            if (i & 1) {
                ATI3DVertex tmp = a;
                a = b;
                b = tmp;
            }
            if (!ati_3d_draw_triangle(s, &surface, a, b, vertices[i])) {
                goto out;
            }
        }
        break;
    default:
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 3D primitive type %u is not implemented\n",
                      primitive);
        goto out;
    }
    result = true;

out:
    g_free(vertices);
    return result;
}
