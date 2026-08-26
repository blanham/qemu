#!/usr/bin/env python3

from pathlib import Path
import re


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text and old not in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one exact match, found {count}: {old[:120]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_regex_once(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    new_text, count = re.subn(pattern, replacement, text, count=1,
                              flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one regex match, found {count}: {pattern[:120]!r}"
        )
    path.write_text(new_text, encoding="utf-8")


regs = Path("hw/display/ati_regs.h")
replace_once(
    regs,
    """#define SC_BOTTOM_RIGHT_C                       0x1c8c

#define CLR_CMP_MASK_3D                         0x1A28
""",
    """#define SC_BOTTOM_RIGHT_C                       0x1c8c
#define PRIM_TEX_CNTL_C                         0x1cb0
#define PRIM_TEXTURE_COMBINE_CNTL_C             0x1cb4
#define TEX_SIZE_PITCH_C                        0x1cb8
#define PRIM_TEX_0_OFFSET_C                     0x1cbc
#define PRIM_TEX_1_OFFSET_C                     0x1cc0
#define PRIM_TEX_2_OFFSET_C                     0x1cc4
#define PRIM_TEX_3_OFFSET_C                     0x1cc8
#define PRIM_TEX_4_OFFSET_C                     0x1ccc
#define PRIM_TEX_5_OFFSET_C                     0x1cd0
#define PRIM_TEX_6_OFFSET_C                     0x1cd4
#define PRIM_TEX_7_OFFSET_C                     0x1cd8
#define PRIM_TEX_8_OFFSET_C                     0x1cdc
#define PRIM_TEX_9_OFFSET_C                     0x1ce0
#define PRIM_TEX_10_OFFSET_C                    0x1ce4
#define SEC_TEX_CNTL_C                          0x1d00
#define SEC_TEX_COMBINE_CNTL_C                  0x1d04
#define CONSTANT_COLOR_C                        0x1d34
#define PRIM_TEXTURE_BORDER_COLOR_C             0x1d38
#define SEC_TEXTURE_BORDER_COLOR_C              0x1d3c

#define CLR_CMP_MASK_3D                         0x1A28
""",
)

source = Path("hw/display/ati_3d.c")
replace_once(
    source,
    "#define ATI_3D_SPEC_LIGHT_ENABLE     BIT(11)\n",
    """#define ATI_3D_SPEC_LIGHT_ENABLE     BIT(11)

#define ATI_3D_TEX_MIN_FILTER_SHIFT   1
#define ATI_3D_TEX_MIN_FILTER_MASK    (7U << ATI_3D_TEX_MIN_FILTER_SHIFT)
#define ATI_3D_TEX_MAG_FILTER_SHIFT   4
#define ATI_3D_TEX_MAG_FILTER_MASK    (7U << ATI_3D_TEX_MAG_FILTER_SHIFT)
#define ATI_3D_TEX_MIP_MAP_DISABLE    BIT(7)
#define ATI_3D_TEX_CLAMP_S_SHIFT      8
#define ATI_3D_TEX_CLAMP_T_SHIFT      11
#define ATI_3D_TEX_CLAMP_MASK         3U
#define ATI_3D_TEX_PERSPECTIVE_DISABLE BIT(14)
#define ATI_3D_TEX_FORMAT_SHIFT       16
#define ATI_3D_TEX_FORMAT_MASK        0xfU
#define ATI_3D_TEX_TILE_MASK          (3U << 30)

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
#define ATI_3D_TEX_FORMAT_ARGB4444    15U

#define ATI_3D_COMB_COLOR_OP_MASK     0xfU
#define ATI_3D_COMB_COLOR_FACTOR_SHIFT 4
#define ATI_3D_COMB_COLOR_FACTOR_MASK 0xfU
#define ATI_3D_COMB_FCN_MSB           BIT(8)
#define ATI_3D_COMB_INPUT_SHIFT       10
#define ATI_3D_COMB_INPUT_MASK        0xfU
#define ATI_3D_COMB_ALPHA_OP_SHIFT    14
#define ATI_3D_COMB_ALPHA_OP_MASK     0xfU
#define ATI_3D_COMB_ALPHA_FACTOR_SHIFT 18
#define ATI_3D_COMB_ALPHA_FACTOR_MASK 0xfU
#define ATI_3D_COMB_ALPHA_INPUT_SHIFT 25
#define ATI_3D_COMB_ALPHA_INPUT_MASK  0x7U

#define ATI_3D_COMB_DISABLE           0U
#define ATI_3D_COMB_COPY              1U
#define ATI_3D_COMB_COPY_INPUT        2U
#define ATI_3D_COMB_MODULATE          3U
#define ATI_3D_COMB_MODULATE2X        4U
#define ATI_3D_COMB_MODULATE4X        5U
#define ATI_3D_COMB_ADD               6U

#define ATI_3D_COLOR_FACTOR_CONST     0U
#define ATI_3D_COLOR_FACTOR_NCONST    1U
#define ATI_3D_COLOR_FACTOR_TEXTURE   4U
#define ATI_3D_COLOR_FACTOR_NTEXTURE  5U
#define ATI_3D_COLOR_FACTOR_ALPHA     6U
#define ATI_3D_COLOR_FACTOR_NALPHA    7U

#define ATI_3D_INPUT_CONST_COLOR      2U
#define ATI_3D_INPUT_CONST_ALPHA      3U
#define ATI_3D_INPUT_INTERP_COLOR     4U
#define ATI_3D_INPUT_INTERP_ALPHA     5U

#define ATI_3D_ALPHA_FACTOR_TEXTURE   6U
#define ATI_3D_ALPHA_FACTOR_NTEXTURE  7U
#define ATI_3D_ALPHA_INPUT_CONST      1U
#define ATI_3D_ALPHA_INPUT_INTERP     2U

#define ATI_3D_MAX_TEXTURE_COORD      1048576.0f
""",
)

replace_once(
    source,
    """typedef struct ATI3DVertex {
    float x;
    float y;
    float z;
    float rhw;
    float color[4];    /* red, green, blue, alpha */
    float specular[4]; /* red, green, blue, fog */
} ATI3DVertex;

""",
    """typedef struct ATI3DVertex {
    float x;
    float y;
    float z;
    float rhw;
    float color[4];    /* red, green, blue, alpha */
    float specular[4]; /* red, green, blue, fog */
    float texcoord[2];
    float texcoord2[2];
    float rhw2;
} ATI3DVertex;

typedef struct ATI3DTexture {
    uint32_t offset;
    uint32_t width;
    uint32_t height;
    uint32_t stride;
    uint32_t control;
    uint32_t combine;
    uint32_t border;
    uint32_t constant;
    unsigned int format;
    unsigned int bytes_per_pixel;
} ATI3DTexture;

""",
)

replace_once(
    source,
    """    uint64_t depth_dirty_start;
    uint64_t depth_dirty_end;
    bool depth_enabled;
} ATI3DFragmentContext;
""",
    """    uint64_t depth_dirty_start;
    uint64_t depth_dirty_end;
    ATI3DTexture texture;
    bool depth_enabled;
    bool texture_enabled;
    bool texture_minify;
} ATI3DFragmentContext;
""",
)

texture_helpers = r'''
static void ati_3d_unpack_argb8888(uint32_t value, float color[4])
{
    color[0] = (value >> 16) & 0xff;
    color[1] = (value >> 8) & 0xff;
    color[2] = value & 0xff;
    color[3] = (value >> 24) & 0xff;
}

static void ati_3d_unpack_texture_color(const ATI3DTexture *texture,
                                        uint32_t value, float color[4])
{
    switch (texture->format) {
    case ATI_3D_TEX_FORMAT_ARGB1555:
        color[0] = ((value >> 10) & 0x1f) * (255.0f / 31.0f);
        color[1] = ((value >> 5) & 0x1f) * (255.0f / 31.0f);
        color[2] = (value & 0x1f) * (255.0f / 31.0f);
        color[3] = value & 0x8000 ? 255.0f : 0.0f;
        break;
    case ATI_3D_TEX_FORMAT_RGB565:
        color[0] = ((value >> 11) & 0x1f) * (255.0f / 31.0f);
        color[1] = ((value >> 5) & 0x3f) * (255.0f / 63.0f);
        color[2] = (value & 0x1f) * (255.0f / 31.0f);
        color[3] = 255.0f;
        break;
    case ATI_3D_TEX_FORMAT_RGB888:
        color[0] = (value >> 16) & 0xff;
        color[1] = (value >> 8) & 0xff;
        color[2] = value & 0xff;
        color[3] = 255.0f;
        break;
    case ATI_3D_TEX_FORMAT_ARGB8888:
        ati_3d_unpack_argb8888(value, color);
        break;
    case ATI_3D_TEX_FORMAT_RGB332:
        color[0] = ((value >> 5) & 7) * (255.0f / 7.0f);
        color[1] = ((value >> 2) & 7) * (255.0f / 7.0f);
        color[2] = (value & 3) * (255.0f / 3.0f);
        color[3] = 255.0f;
        break;
    case ATI_3D_TEX_FORMAT_ARGB4444:
        color[0] = ((value >> 8) & 0xf) * 17.0f;
        color[1] = ((value >> 4) & 0xf) * 17.0f;
        color[2] = (value & 0xf) * 17.0f;
        color[3] = ((value >> 12) & 0xf) * 17.0f;
        break;
    default:
        memset(color, 0, 4 * sizeof(*color));
        break;
    }
}

static bool ati_3d_texture_init(ATI3DFragmentContext *ctx)
{
    ATI3DTexture *texture = &ctx->texture;
    uint32_t size_pitch = ati_3d_reg(ctx->s, TEX_SIZE_PITCH_C);
    unsigned int pitch_log2 = extract32(size_pitch, 0, 4);
    unsigned int height_log2 = extract32(size_pitch, 8, 4);
    unsigned int min_filter;
    unsigned int mag_filter;
    uint64_t end;

    memset(texture, 0, sizeof(*texture));
    texture->control = ati_3d_reg(ctx->s, PRIM_TEX_CNTL_C);
    texture->combine = ati_3d_reg(ctx->s, PRIM_TEXTURE_COMBINE_CNTL_C);
    texture->border = ati_3d_reg(ctx->s, PRIM_TEXTURE_BORDER_COLOR_C);
    texture->constant = ati_3d_reg(ctx->s, CONSTANT_COLOR_C);
    texture->offset = ati_3d_reg(ctx->s, PRIM_TEX_0_OFFSET_C);
    texture->format = extract32(texture->control,
                                ATI_3D_TEX_FORMAT_SHIFT, 4);

    if (!(texture->control & ATI_3D_TEX_MIP_MAP_DISABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 mipmapped textures are not implemented\n");
        return false;
    }
    if (texture->offset & ATI_3D_TEX_TILE_MASK) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 tiled textures are not implemented\n");
        return false;
    }
    min_filter = (texture->control & ATI_3D_TEX_MIN_FILTER_MASK) >>
                 ATI_3D_TEX_MIN_FILTER_SHIFT;
    mag_filter = (texture->control & ATI_3D_TEX_MAG_FILTER_MASK) >>
                 ATI_3D_TEX_MAG_FILTER_SHIFT;
    if (min_filter > ATI_3D_TEX_FILTER_LINEAR ||
        mag_filter > ATI_3D_TEX_FILTER_LINEAR) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 texture filter is not implemented\n");
        return false;
    }

    switch (texture->format) {
    case ATI_3D_TEX_FORMAT_RGB332:
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

    texture->offset &= ~ATI_3D_TEX_TILE_MASK;
    texture->width = 1U << pitch_log2;
    texture->height = 1U << height_log2;
    texture->stride = texture->width * texture->bytes_per_pixel;
    end = (uint64_t)texture->offset +
          (uint64_t)texture->height * texture->stride;
    if (!texture->stride || end > ctx->s->vga.vram_size) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 texture surface exceeds VRAM\n");
        return false;
    }
    return true;
}

static int ati_3d_positive_mod(int value, int divisor)
{
    int result = value % divisor;

    return result < 0 ? result + divisor : result;
}

static bool ati_3d_texture_index(unsigned int mode, int coordinate,
                                 unsigned int size, int *index)
{
    switch (mode) {
    case ATI_3D_TEX_WRAP_REPEAT:
        *index = ati_3d_positive_mod(coordinate, size);
        return true;
    case ATI_3D_TEX_WRAP_MIRROR:
    {
        int period = size * 2;
        int mirrored = ati_3d_positive_mod(coordinate, period);

        *index = mirrored < (int)size ? mirrored : period - 1 - mirrored;
        return true;
    }
    case ATI_3D_TEX_WRAP_CLAMP:
        *index = CLAMP(coordinate, 0, (int)size - 1);
        return true;
    case ATI_3D_TEX_WRAP_BORDER:
        if (coordinate < 0 || coordinate >= (int)size) {
            return false;
        }
        *index = coordinate;
        return true;
    default:
        g_assert_not_reached();
    }
}

static bool ati_3d_texture_fetch(const ATI3DFragmentContext *ctx,
                                 int x, int y, float color[4])
{
    const ATI3DTexture *texture = &ctx->texture;
    unsigned int wrap_s = extract32(texture->control,
                                    ATI_3D_TEX_CLAMP_S_SHIFT, 2);
    unsigned int wrap_t = extract32(texture->control,
                                    ATI_3D_TEX_CLAMP_T_SHIFT, 2);
    uint64_t address;
    uint32_t value = 0;
    int tex_x;
    int tex_y;

    if (!ati_3d_texture_index(wrap_s, x, texture->width, &tex_x) ||
        !ati_3d_texture_index(wrap_t, y, texture->height, &tex_y)) {
        ati_3d_unpack_argb8888(texture->border, color);
        return true;
    }
    address = (uint64_t)texture->offset +
              (uint64_t)tex_y * texture->stride +
              (uint64_t)tex_x * texture->bytes_per_pixel;
    if (address + texture->bytes_per_pixel > ctx->s->vga.vram_size) {
        return false;
    }
    for (unsigned int byte = 0; byte < texture->bytes_per_pixel; byte++) {
        unsigned int shift = ctx->s->vga.big_endian_fb ?
            (texture->bytes_per_pixel - 1 - byte) * 8 : byte * 8;

        value |= (uint32_t)ctx->s->vga.vram_ptr[address + byte] << shift;
    }
    ati_3d_unpack_texture_color(texture, value, color);
    return true;
}

static bool ati_3d_texture_sample(const ATI3DFragmentContext *ctx,
                                  float s_coord, float t_coord,
                                  float color[4])
{
    const ATI3DTexture *texture = &ctx->texture;
    unsigned int filter = ctx->texture_minify ?
        (texture->control & ATI_3D_TEX_MIN_FILTER_MASK) >>
        ATI_3D_TEX_MIN_FILTER_SHIFT :
        (texture->control & ATI_3D_TEX_MAG_FILTER_MASK) >>
        ATI_3D_TEX_MAG_FILTER_SHIFT;
    float u;
    float v;

    if (!isfinite(s_coord) || !isfinite(t_coord) ||
        fabsf(s_coord) > ATI_3D_MAX_TEXTURE_COORD ||
        fabsf(t_coord) > ATI_3D_MAX_TEXTURE_COORD) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 texture coordinate is invalid\n");
        return false;
    }

    if (filter == ATI_3D_TEX_FILTER_NEAREST) {
        int x;
        int y;

        u = s_coord * texture->width;
        v = t_coord * texture->height;
        if (u < INT_MIN || u > INT_MAX || v < INT_MIN || v > INT_MAX) {
            return false;
        }
        x = floorf(u);
        y = floorf(v);
        return ati_3d_texture_fetch(ctx, x, y, color);
    }
    if (filter == ATI_3D_TEX_FILTER_LINEAR) {
        float samples[4][4];
        float fx;
        float fy;
        int x0;
        int y0;

        u = s_coord * texture->width - 0.5f;
        v = t_coord * texture->height - 0.5f;
        if (u < INT_MIN || u > INT_MAX || v < INT_MIN || v > INT_MAX) {
            return false;
        }
        x0 = floorf(u);
        y0 = floorf(v);
        fx = u - x0;
        fy = v - y0;
        if (!ati_3d_texture_fetch(ctx, x0, y0, samples[0]) ||
            !ati_3d_texture_fetch(ctx, x0 + 1, y0, samples[1]) ||
            !ati_3d_texture_fetch(ctx, x0, y0 + 1, samples[2]) ||
            !ati_3d_texture_fetch(ctx, x0 + 1, y0 + 1, samples[3])) {
            return false;
        }
        for (unsigned int channel = 0; channel < 4; channel++) {
            float top = samples[0][channel] * (1.0f - fx) +
                        samples[1][channel] * fx;
            float bottom = samples[2][channel] * (1.0f - fx) +
                           samples[3][channel] * fx;

            color[channel] = top * (1.0f - fy) + bottom * fy;
        }
        return true;
    }
    return false;
}

static bool ati_3d_combine_value(unsigned int operation,
                                 float input, float factor, float *result)
{
    switch (operation) {
    case ATI_3D_COMB_DISABLE:
    case ATI_3D_COMB_COPY:
        *result = factor;
        return true;
    case ATI_3D_COMB_COPY_INPUT:
        *result = input;
        return true;
    case ATI_3D_COMB_MODULATE:
        *result = input * factor / 255.0f;
        return true;
    case ATI_3D_COMB_MODULATE2X:
        *result = input * factor * (2.0f / 255.0f);
        return true;
    case ATI_3D_COMB_MODULATE4X:
        *result = input * factor * (4.0f / 255.0f);
        return true;
    case ATI_3D_COMB_ADD:
        *result = input + factor;
        return true;
    default:
        return false;
    }
}

static bool ati_3d_texture_combine(const ATI3DFragmentContext *ctx,
                                   const float texture_color[4],
                                   float fragment[4])
{
    const ATI3DTexture *texture = &ctx->texture;
    uint32_t combine = texture->combine;
    float constant[4];
    float input[4];
    float factor[4];
    float output[4];
    unsigned int color_op = combine & ATI_3D_COMB_COLOR_OP_MASK;
    unsigned int color_factor = extract32(
        combine, ATI_3D_COMB_COLOR_FACTOR_SHIFT, 4);
    unsigned int color_input = extract32(
        combine, ATI_3D_COMB_INPUT_SHIFT, 4);
    unsigned int alpha_op = extract32(
        combine, ATI_3D_COMB_ALPHA_OP_SHIFT, 4);
    unsigned int alpha_factor = extract32(
        combine, ATI_3D_COMB_ALPHA_FACTOR_SHIFT, 4);
    unsigned int alpha_input = extract32(
        combine, ATI_3D_COMB_ALPHA_INPUT_SHIFT, 3);

    if (combine & ATI_3D_COMB_FCN_MSB) {
        return false;
    }
    ati_3d_unpack_argb8888(texture->constant, constant);
    for (unsigned int channel = 0; channel < 3; channel++) {
        switch (color_input) {
        case ATI_3D_INPUT_CONST_COLOR:
            input[channel] = constant[channel];
            break;
        case ATI_3D_INPUT_CONST_ALPHA:
            input[channel] = constant[3];
            break;
        case ATI_3D_INPUT_INTERP_COLOR:
            input[channel] = fragment[channel];
            break;
        case ATI_3D_INPUT_INTERP_ALPHA:
            input[channel] = fragment[3];
            break;
        default:
            return false;
        }
        switch (color_factor) {
        case ATI_3D_COLOR_FACTOR_CONST:
            factor[channel] = constant[channel];
            break;
        case ATI_3D_COLOR_FACTOR_NCONST:
            factor[channel] = 255.0f - constant[channel];
            break;
        case ATI_3D_COLOR_FACTOR_TEXTURE:
            factor[channel] = texture_color[channel];
            break;
        case ATI_3D_COLOR_FACTOR_NTEXTURE:
            factor[channel] = 255.0f - texture_color[channel];
            break;
        case ATI_3D_COLOR_FACTOR_ALPHA:
            factor[channel] = texture_color[3];
            break;
        case ATI_3D_COLOR_FACTOR_NALPHA:
            factor[channel] = 255.0f - texture_color[3];
            break;
        default:
            return false;
        }
        if (!ati_3d_combine_value(color_op, input[channel], factor[channel],
                                  &output[channel])) {
            return false;
        }
    }

    switch (alpha_input) {
    case ATI_3D_ALPHA_INPUT_CONST:
        input[3] = constant[3];
        break;
    case ATI_3D_ALPHA_INPUT_INTERP:
        input[3] = fragment[3];
        break;
    default:
        return false;
    }
    switch (alpha_factor) {
    case ATI_3D_ALPHA_FACTOR_TEXTURE:
        factor[3] = texture_color[3];
        break;
    case ATI_3D_ALPHA_FACTOR_NTEXTURE:
        factor[3] = 255.0f - texture_color[3];
        break;
    default:
        return false;
    }
    if (!ati_3d_combine_value(alpha_op, input[3], factor[3], &output[3])) {
        return false;
    }
    memcpy(fragment, output, sizeof(output));
    return true;
}

static bool ati_3d_texture_coordinates(const ATI3DFragmentContext *ctx,
                                       const ATI3DVertex *vertices,
                                       const float *weights,
                                       unsigned int count,
                                       float *s_coord, float *t_coord)
{
    float denominator = 0.0f;

    *s_coord = 0.0f;
    *t_coord = 0.0f;
    if (ctx->texture.control & ATI_3D_TEX_PERSPECTIVE_DISABLE) {
        for (unsigned int i = 0; i < count; i++) {
            *s_coord += vertices[i].texcoord[0] * weights[i];
            *t_coord += vertices[i].texcoord[1] * weights[i];
        }
    } else {
        for (unsigned int i = 0; i < count; i++) {
            float weighted_rhw = weights[i] * vertices[i].rhw;

            denominator += weighted_rhw;
            *s_coord += vertices[i].texcoord[0] * weighted_rhw;
            *t_coord += vertices[i].texcoord[1] * weighted_rhw;
        }
        if (!isfinite(denominator) || fabsf(denominator) < 1.0e-20f) {
            return false;
        }
        *s_coord /= denominator;
        *t_coord /= denominator;
    }
    return isfinite(*s_coord) && isfinite(*t_coord);
}

static bool ati_3d_texture_apply(ATI3DFragmentContext *ctx,
                                 const ATI3DVertex *vertices,
                                 const float *weights, unsigned int count,
                                 float fragment[4])
{
    float texture_color[4];
    float s_coord;
    float t_coord;

    if (!ctx->texture_enabled) {
        return true;
    }
    if (!ati_3d_texture_coordinates(ctx, vertices, weights, count,
                                    &s_coord, &t_coord) ||
        !ati_3d_texture_sample(ctx, s_coord, t_coord, texture_color) ||
        !ati_3d_texture_combine(ctx, texture_color, fragment)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 primary texture state is not implemented\n");
        return false;
    }
    return true;
}

static bool ati_3d_texture_triangle_minify(const ATI3DFragmentContext *ctx,
                                           const ATI3DVertex *v0,
                                           const ATI3DVertex *v1,
                                           const ATI3DVertex *v2)
{
    float dx1;
    float dy1;
    float dx2;
    float dy2;
    float determinant;
    float dsdx;
    float dsdy;
    float dtdx;
    float dtdy;
    float rho_x;
    float rho_y;

    if (!ctx->texture_enabled) {
        return false;
    }
    dx1 = v1->x - v0->x;
    dy1 = v1->y - v0->y;
    dx2 = v2->x - v0->x;
    dy2 = v2->y - v0->y;
    determinant = dx1 * dy2 - dx2 * dy1;
    if (!isfinite(determinant) || fabsf(determinant) < 1.0e-20f) {
        return false;
    }
    dsdx = ((v1->texcoord[0] - v0->texcoord[0]) * dy2 -
            (v2->texcoord[0] - v0->texcoord[0]) * dy1) / determinant;
    dsdy = (dx1 * (v2->texcoord[0] - v0->texcoord[0]) -
            dx2 * (v1->texcoord[0] - v0->texcoord[0])) / determinant;
    dtdx = ((v1->texcoord[1] - v0->texcoord[1]) * dy2 -
            (v2->texcoord[1] - v0->texcoord[1]) * dy1) / determinant;
    dtdy = (dx1 * (v2->texcoord[1] - v0->texcoord[1]) -
            dx2 * (v1->texcoord[1] - v0->texcoord[1])) / determinant;
    rho_x = hypotf(dsdx * ctx->texture.width,
                   dtdx * ctx->texture.height);
    rho_y = hypotf(dsdy * ctx->texture.width,
                   dtdy * ctx->texture.height);
    return MAX(rho_x, rho_y) > 1.0f;
}

static bool ati_3d_texture_line_minify(const ATI3DFragmentContext *ctx,
                                       const ATI3DVertex *a,
                                       const ATI3DVertex *b)
{
    float screen_span;
    float texture_span;

    if (!ctx->texture_enabled) {
        return false;
    }
    screen_span = hypotf(b->x - a->x, b->y - a->y);
    texture_span = hypotf((b->texcoord[0] - a->texcoord[0]) *
                          ctx->texture.width,
                          (b->texcoord[1] - a->texcoord[1]) *
                          ctx->texture.height);
    return isfinite(screen_span) && isfinite(texture_span) &&
           texture_span > MAX(screen_span, 1.0f);
}
'''

replace_once(
    source,
    """static bool ati_3d_surface_address(const ATIVGAState *s,
                                   const ATI3DSurface *surface,
""",
    texture_helpers + "\nstatic bool ati_3d_surface_address(const ATIVGAState *s,\n                                   const ATI3DSurface *surface,\n",
)

replace_regex_once(
    source,
    r"static bool ati_3d_read_vertex\(ATIVGAState \*s, dma_addr_t address,.*?\n\}\n\nstatic float ati_3d_edge",
    r'''static bool ati_3d_read_vertex(ATIVGAState *s, dma_addr_t address,
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
    memset(vertex, 0, sizeof(*vertex));
    vertex->x = ati_3d_u32_to_float(words[0]);
    vertex->y = ati_3d_u32_to_float(words[1]);
    vertex->z = ati_3d_u32_to_float(words[2]);
    vertex->rhw = 1.0f;
    vertex->rhw2 = 1.0f;
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
    if (format & ATI_3D_VERTEX_ST) {
        vertex->texcoord[0] = ati_3d_u32_to_float(words[index++]);
        vertex->texcoord[1] = ati_3d_u32_to_float(words[index++]);
    }
    if (format & ATI_3D_VERTEX_S2T2) {
        vertex->texcoord2[0] = ati_3d_u32_to_float(words[index++]);
        vertex->texcoord2[1] = ati_3d_u32_to_float(words[index++]);
    }
    if (format & ATI_3D_VERTEX_RHW2) {
        vertex->rhw2 = ati_3d_u32_to_float(words[index++]);
    }
    return index == stride && isfinite(vertex->x) &&
           isfinite(vertex->y) && isfinite(vertex->z) &&
           isfinite(vertex->rhw) && isfinite(vertex->rhw2) &&
           isfinite(vertex->texcoord[0]) &&
           isfinite(vertex->texcoord[1]) &&
           isfinite(vertex->texcoord2[0]) &&
           isfinite(vertex->texcoord2[1]);
}

static float ati_3d_edge''',
)

replace_regex_once(
    source,
    r"static void ati_3d_shade_sample\(.*?\n\}\n\nstatic bool ati_3d_edge_is_top_left",
    r'''static void ati_3d_shade_sample(const ATI3DShadeState *shade,
                                const ATI3DVertex *vertices,
                                const float *weights, unsigned int count,
                                float result[4], float specular[4])
{
    memset(specular, 0, 4 * sizeof(*specular));
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
}

static void ati_3d_apply_specular(const ATI3DShadeState *shade,
                                  const float specular[4], float color[4])
{
    if (shade->specular_enabled && shade->mode != ATI_3D_COLOR_SOLID) {
        for (unsigned int channel = 0; channel < 3; channel++) {
            color[channel] = MIN(color[channel] + specular[channel], 255.0f);
        }
    }
}

static bool ati_3d_edge_is_top_left''',
)

replace_once(
    source,
    """    if (ctx->depth_enabled &&
        !ati_3d_depth_surface(s, &ctx->depth_surface, &ctx->depth_mask,
                              &ctx->depth_function)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D: invalid depth surface\n");
        return false;
    }
    return true;
}
""",
    """    if (ctx->depth_enabled &&
        !ati_3d_depth_surface(s, &ctx->depth_surface, &ctx->depth_mask,
                              &ctx->depth_function)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 3D: invalid depth surface\n");
        return false;
    }
    ctx->texture_enabled = ctx->tex_control & ATI_3D_TEXMAP_ENABLE;
    if (ctx->texture_enabled && !ati_3d_texture_init(ctx)) {
        return false;
    }
    return true;
}
""",
)

replace_once(
    source,
    """    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    bool edge0_top_left = ati_3d_edge_is_top_left(&v1, &v2);
""",
    """    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    fragments.texture_minify =
        ati_3d_texture_triangle_minify(&fragments, &v0, &v1, &v2);
    bool edge0_top_left = ati_3d_edge_is_top_left(&v1, &v2);
""",
)

replace_once(
    source,
    """            float src[4];
            float z;
""",
    """            float src[4];
            float specular[4];
            float z;
""",
)

replace_once(
    source,
    """                ati_3d_shade_sample(&shade,
                                    shade.mode == ATI_3D_COLOR_FLAT ?
                                    original_vertices : vertices,
                                    weights, 3, src);
            }
            z = v0.z * w0 + v1.z * w1 + v2.z * w2;
""",
    """                ati_3d_shade_sample(&shade,
                                    shade.mode == ATI_3D_COLOR_FLAT ?
                                    original_vertices : vertices,
                                    weights, 3, src, specular);
                if (!ati_3d_texture_apply(&fragments, vertices,
                                          weights, 3, src)) {
                    goto out;
                }
            }
            ati_3d_apply_specular(&shade, specular, src);
            z = v0.z * w0 + v1.z * w1 + v2.z * w2;
""",
)

replace_once(
    source,
    """    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    ati_3d_shade_state_init(s, &shade);
    for (unsigned int i = 0; i <= steps; i++) {
""",
    """    if (!ati_3d_fragment_context_init(&fragments, s, surface)) {
        return false;
    }
    fragments.texture_minify =
        ati_3d_texture_line_minify(&fragments, a, b);
    ati_3d_shade_state_init(s, &shade);
    for (unsigned int i = 0; i <= steps; i++) {
""",
)

replace_once(
    source,
    """        float color[4];
        float z;

        if (!ati_3d_aux_scissor_pass(s, x, y)) {
""",
    """        float color[4];
        float specular[4];
        float z;

        if (!ati_3d_aux_scissor_pass(s, x, y)) {
""",
)

replace_once(
    source,
    """            ati_3d_shade_sample(&shade, vertices, weights, 2, color);
        }
        z = a->z * (1.0f - t) + b->z * t;
""",
    """            ati_3d_shade_sample(&shade, vertices, weights, 2,
                                color, specular);
            if (!ati_3d_texture_apply(&fragments, vertices,
                                      weights, 2, color)) {
                goto out;
            }
        }
        ati_3d_apply_specular(&shade, specular, color);
        z = a->z * (1.0f - t) + b->z * t;
""",
)

replace_once(
    source,
    """    const float weight = 1.0f;
    float color[4];
    bool result;
""",
    """    const float weight = 1.0f;
    float color[4];
    float specular[4];
    bool result;
""",
)

replace_once(
    source,
    """    ati_3d_shade_state_init(s, &shade);
    ati_3d_shade_sample(&shade, vertex, &weight, 1, color);
    result = ati_3d_fragment(&fragments, x, y, color, vertex->z);
""",
    """    ati_3d_shade_state_init(s, &shade);
    ati_3d_shade_sample(&shade, vertex, &weight, 1, color, specular);
    if (!ati_3d_texture_apply(&fragments, vertex, &weight, 1, color)) {
        result = false;
    } else {
        ati_3d_apply_specular(&shade, specular, color);
        result = ati_3d_fragment(&fragments, x, y, color, vertex->z);
    }
""",
)

replace_once(
    source,
    """    if (tex_control & (ATI_3D_TEXMAP_ENABLE |
                       ATI_3D_SEC_TEXMAP_ENABLE |
                       ATI_3D_FOG_ENABLE |
                       ATI_3D_TEX_STENCIL_ENABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 textured, fogged, or stencil 3D is not implemented\n");
        return false;
    }
""",
    """    if (tex_control & (ATI_3D_SEC_TEXMAP_ENABLE |
                       ATI_3D_FOG_ENABLE |
                       ATI_3D_TEX_STENCIL_ENABLE)) {
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 secondary texture, fog, or stencil 3D is not implemented\n");
        return false;
    }
    if ((tex_control & ATI_3D_TEXMAP_ENABLE) &&
        !(format & ATI_3D_VERTEX_ST)) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 textured draw is missing S/T coordinates\n");
        return false;
    }
""",
)

text = source.read_text(encoding="utf-8")
if re.search(r"ati_3d_shade_sample\([^;]*,[^;]*\);", text, re.DOTALL):
    for match in re.finditer(r"ati_3d_shade_sample\([^;]*\);", text,
                             re.DOTALL):
        call = match.group(0)
        if "specular" not in call:
            raise SystemExit(f"{source}: stale shade call remains: {call}")


test = Path("tests/qtest/ati-rage128-pm4-test.c")
replace_once(
    test,
    """#define R128_MISC_3D_STATE_CNTL_REG    0x1ca0
#define R128_PLANE_3D_MASK_C           0x1d44
""",
    """#define R128_MISC_3D_STATE_CNTL_REG    0x1ca0
#define R128_PRIM_TEX_CNTL_C           0x1cb0
#define R128_PRIM_TEXTURE_COMBINE_CNTL_C 0x1cb4
#define R128_TEX_SIZE_PITCH_C          0x1cb8
#define R128_PRIM_TEX_0_OFFSET_C       0x1cbc
#define R128_CONSTANT_COLOR_C          0x1d34
#define R128_PRIM_TEXTURE_BORDER_COLOR_C 0x1d38
#define R128_PLANE_3D_MASK_C           0x1d44
""",
)
replace_once(
    test,
    """#define R128_VC_FRMT_DIFFUSE_ARGB      0x00000008U
#define R128_VC_FRMT_SPEC_FRGB         0x00000040U
""",
    """#define R128_VC_FRMT_RHW               0x00000001U
#define R128_VC_FRMT_DIFFUSE_ARGB      0x00000008U
#define R128_VC_FRMT_SPEC_FRGB         0x00000040U
#define R128_VC_FRMT_ST                0x00000080U
""",
)
replace_once(
    test,
    """#define R128_TEX_Z_ENABLE              (1U << 0)
#define R128_TEX_Z_WRITE_ENABLE        (1U << 1)
""",
    """#define R128_TEX_Z_ENABLE              (1U << 0)
#define R128_TEX_Z_WRITE_ENABLE        (1U << 1)
#define R128_TEXMAP_ENABLE             (1U << 4)
""",
)
replace_once(
    test,
    """#define R128_ALPHA_TEST_ALWAYS         (7U << 24)

#define RING_PHYS""",
    """#define R128_ALPHA_TEST_ALWAYS         (7U << 24)

#define R128_TEX_FILTER_LINEAR         (1U << 1)
#define R128_TEX_MAG_FILTER_LINEAR     (1U << 4)
#define R128_TEX_MIP_MAP_DISABLE       (1U << 7)
#define R128_TEX_WRAP_S_MIRROR         (1U << 8)
#define R128_TEX_WRAP_S_CLAMP          (2U << 8)
#define R128_TEX_WRAP_S_BORDER         (3U << 8)
#define R128_TEX_WRAP_T_CLAMP          (2U << 11)
#define R128_TEX_PERSPECTIVE_DISABLE   (1U << 14)
#define R128_TEX_FORMAT_RGB565         (4U << 16)
#define R128_TEX_FORMAT_ARGB8888       (6U << 16)

#define R128_COMB_DISABLE              0U
#define R128_COMB_MODULATE             3U
#define R128_COLOR_FACTOR_TEX          (4U << 4)
#define R128_INPUT_FACTOR_INT_COLOR    (4U << 10)
#define R128_COMB_ALPHA_DISABLE        (0U << 14)
#define R128_COMB_ALPHA_MODULATE       (3U << 14)
#define R128_ALPHA_FACTOR_TEX          (6U << 18)
#define R128_INPUT_FACTOR_INT_ALPHA    (2U << 25)
#define R128_COMB_REPLACE_RGBA         (R128_COMB_DISABLE | \
                                        R128_COLOR_FACTOR_TEX | \
                                        R128_INPUT_FACTOR_INT_COLOR | \
                                        R128_COMB_ALPHA_DISABLE | \
                                        R128_ALPHA_FACTOR_TEX | \
                                        R128_INPUT_FACTOR_INT_ALPHA)
#define R128_COMB_MODULATE_RGBA        (R128_COMB_MODULATE | \
                                        R128_COLOR_FACTOR_TEX | \
                                        R128_INPUT_FACTOR_INT_COLOR | \
                                        R128_COMB_ALPHA_MODULATE | \
                                        R128_ALPHA_FACTOR_TEX | \
                                        R128_INPUT_FACTOR_INT_ALPHA)

#define RING_PHYS""",
)
replace_once(
    test,
    """#define DEPTH_OFFSET                   0x00010000U
#define RING_DWORDS""",
    """#define DEPTH_OFFSET                   0x00010000U
#define TEXTURE_OFFSET                 0x00020000U
#define RING_DWORDS""",
)

texture_test_helpers = r'''
static void ring_set_texture0(RingBuilder *ring, uint32_t control,
                              uint32_t combine, unsigned int pitch_log2,
                              unsigned int height_log2, uint32_t offset,
                              uint32_t border)
{
    uint32_t size_pitch = pitch_log2 |
        (MAX(pitch_log2, height_log2) << 4) |
        (height_log2 << 8) |
        (MIN(pitch_log2, height_log2) << 12);

    ring_packet0_one(ring, R128_PRIM_TEX_CNTL_C, control);
    ring_packet0_one(ring, R128_PRIM_TEXTURE_COMBINE_CNTL_C, combine);
    ring_packet0_one(ring, R128_TEX_SIZE_PITCH_C, size_pitch);
    ring_packet0_one(ring, R128_PRIM_TEX_0_OFFSET_C, offset);
    ring_packet0_one(ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
    ring_packet0_one(ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, border);
}

static void write_textured_vertices(Rage128PM4Test *test, uint32_t offset,
                                    const float (*xyz)[3],
                                    const float *rhw,
                                    const uint32_t *colors,
                                    const float (*st)[2],
                                    unsigned int count)
{
    const unsigned int stride = 7;
    uint32_t *vertices = g_new0(uint32_t, count * stride);

    for (unsigned int i = 0; i < count; i++) {
        vertices[i * stride + 0] = cpu_to_le32(float_bits(xyz[i][0]));
        vertices[i * stride + 1] = cpu_to_le32(float_bits(xyz[i][1]));
        vertices[i * stride + 2] = cpu_to_le32(float_bits(xyz[i][2]));
        vertices[i * stride + 3] = cpu_to_le32(float_bits(rhw[i]));
        vertices[i * stride + 4] = cpu_to_le32(colors[i]);
        vertices[i * stride + 5] = cpu_to_le32(float_bits(st[i][0]));
        vertices[i * stride + 6] = cpu_to_le32(float_bits(st[i][1]));
    }
    qtest_memwrite(test->qts, VERTEX_PHYS + offset, vertices,
                   count * stride * sizeof(uint32_t));
    g_free(vertices);
}

static void write_texture32(Rage128PM4Test *test, uint32_t offset,
                            const uint32_t *pixels, unsigned int count)
{
    uint32_t *raw = g_new(uint32_t, count);

    for (unsigned int i = 0; i < count; i++) {
        raw[i] = cpu_to_le32(pixels[i]);
    }
    qpci_memwrite(test->dev, test->framebuffer, offset, raw,
                  count * sizeof(*raw));
    g_free(raw);
}
'''

replace_once(
    test,
    """static void test_pm4_control_and_2d_packets(void)
""",
    texture_test_helpers + "\nstatic void test_pm4_control_and_2d_packets(void)\n",
)

texture_test = r'''
static void test_pm4_primary_texture(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t textured_format = R128_VC_FRMT_RHW |
                                     R128_VC_FRMT_DIFFUSE_ARGB |
                                     R128_VC_FRMT_ST;
    const float square[6][3] = {
        { 8.5f, 8.5f, 0.0f }, { 40.5f, 8.5f, 0.0f },
        { 8.5f, 40.5f, 0.0f }, { 40.5f, 8.5f, 0.0f },
        { 40.5f, 40.5f, 0.0f }, { 8.5f, 40.5f, 0.0f },
    };
    const float square_st[6][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
        { 1.0f, 0.0f }, { 1.0f, 1.0f }, { 0.0f, 1.0f },
    };
    const float square_rhw[6] = { 1, 1, 1, 1, 1, 1 };
    uint32_t square_colors[6];

    load_microcode(test);
    setup_gart(test);
    for (unsigned int i = 0; i < 6; i++) {
        square_colors[i] = UINT32_MAX;
    }
    write_textured_vertices(test, 0, square, square_rhw,
                            square_colors, square_st, 6);

    /* Level-zero ARGB8888 nearest sampling and REPLACE. */
    {
        RingBuilder ring = { 0 };
        uint32_t texture[16];
        const uint32_t row[4] = {
            0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff,
        };

        for (unsigned int y = 0; y < 4; y++) {
            memcpy(&texture[y * 4], row, sizeof(row));
        }
        write_texture32(test, TEXTURE_OFFSET, texture,
                        G_N_ELEMENTS(texture));
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_FORMAT_ARGB8888,
                          R128_COMB_REPLACE_RGBA, 2, 2,
                          TEXTURE_OFFSET, 0xff123456);
        ring_draw_format(&ring, 0, 6, R128_VC_PRIM_TRI_LIST,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 12, 12), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 20, 12), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 28, 12), ==, 0xff0000ff);
        g_assert_cmphex(framebuffer_read(test, 36, 12), ==, 0xffffffff);
    }

    /* MODULATE combines the texture with interpolated diffuse color. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture[16] = {
            [0 ... 15] = 0xffff0000,
        };

        for (unsigned int i = 0; i < 6; i++) {
            square_colors[i] = 0x80808080;
        }
        write_textured_vertices(test, 0, square, square_rhw,
                                square_colors, square_st, 6);
        write_texture32(test, TEXTURE_OFFSET, texture,
                        G_N_ELEMENTS(texture));
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_FORMAT_ARGB8888,
                          R128_COMB_MODULATE_RGBA, 2, 2,
                          TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 6, R128_VC_PRIM_TRI_LIST,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0x80800000);
    }

    /* Bilinear sampling averages all four texels at the texture center. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture[4] = {
            0xffff0000, 0xff00ff00,
            0xff0000ff, 0xffffffff,
        };
        const float centered_st[6][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        for (unsigned int i = 0; i < 6; i++) {
            square_colors[i] = UINT32_MAX;
        }
        write_textured_vertices(test, 0, square, square_rhw,
                                square_colors, centered_st, 6);
        write_texture32(test, TEXTURE_OFFSET, texture,
                        G_N_ELEMENTS(texture));
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_FILTER_LINEAR |
                          R128_TEX_MAG_FILTER_LINEAR |
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_FORMAT_ARGB8888,
                          R128_COMB_REPLACE_RGBA, 1, 1,
                          TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 6, R128_VC_PRIM_TRI_LIST,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff808080);
    }

    /* Repeat, mirror, clamp, and border address modes remain distinct. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture[2] = { 0xffff0000, 0xff00ff00 };
        const float points[4][3] = {
            { 10.0f, 10.0f, 0.0f }, { 12.0f, 10.0f, 0.0f },
            { 14.0f, 10.0f, 0.0f }, { 16.0f, 10.0f, 0.0f },
        };
        const float point_rhw[4] = { 1, 1, 1, 1 };
        const uint32_t point_colors[4] = {
            UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX,
        };
        const float point_st[4][2] = {
            { 1.25f, 0.5f }, { 1.25f, 0.5f },
            { 1.25f, 0.5f }, { 1.25f, 0.5f },
        };
        const uint32_t wrap[4] = {
            0, R128_TEX_WRAP_S_MIRROR,
            R128_TEX_WRAP_S_CLAMP, R128_TEX_WRAP_S_BORDER,
        };

        write_textured_vertices(test, 0, points, point_rhw,
                                point_colors, point_st, 4);
        write_texture32(test, TEXTURE_OFFSET, texture,
                        G_N_ELEMENTS(texture));
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        for (unsigned int i = 0; i < 4; i++) {
            ring_set_texture0(&ring,
                              R128_TEX_MIP_MAP_DISABLE |
                              R128_TEX_WRAP_T_CLAMP | wrap[i] |
                              R128_TEX_FORMAT_ARGB8888,
                              R128_COMB_REPLACE_RGBA, 1, 0,
                              TEXTURE_OFFSET, 0xff0000ff);
            ring_draw_format(&ring, i * 7 * sizeof(uint32_t), 1,
                             R128_VC_PRIM_POINT, textured_format);
        }
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 10, 10), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 12, 10), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 14, 10), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 16, 10), ==, 0xff0000ff);
    }

    /* RGB565 unpacking uses the same sampler and combine path. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture = 0x07e0f800;
        const float points[2][3] = {
            { 18.0f, 10.0f, 0.0f }, { 20.0f, 10.0f, 0.0f },
        };
        const float point_rhw[2] = { 1, 1 };
        const uint32_t point_colors[2] = { UINT32_MAX, UINT32_MAX };
        const float point_st[2][2] = {
            { 0.25f, 0.5f }, { 0.75f, 0.5f },
        };

        write_textured_vertices(test, 0, points, point_rhw,
                                point_colors, point_st, 2);
        qpci_io_writel(test->dev, test->framebuffer,
                       TEXTURE_OFFSET, texture);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_WRAP_T_CLAMP |
                          R128_TEX_FORMAT_RGB565,
                          R128_COMB_REPLACE_RGBA, 1, 0,
                          TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 2, R128_VC_PRIM_POINT,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 18, 10), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 20, 10), ==, 0xff00ff00);
    }

    /* RHW drives perspective-correct S/T unless explicitly disabled. */
    {
        const float triangle[3][3] = {
            { 8.0f, 8.0f, 0.0f },
            { 56.0f, 8.0f, 0.0f },
            { 8.0f, 56.0f, 0.0f },
        };
        const float triangle_rhw[3] = { 1.0f, 0.25f, 1.0f };
        const uint32_t triangle_colors[3] = {
            UINT32_MAX, UINT32_MAX, UINT32_MAX,
        };
        const float triangle_st[3][2] = {
            { 0.0f, 0.5f }, { 1.0f, 0.5f }, { 0.0f, 0.5f },
        };
        const uint32_t texture[4] = {
            0xffff0000, 0xff00ff00, 0xff0000ff, 0xffffffff,
        };

        write_textured_vertices(test, 0, triangle, triangle_rhw,
                                triangle_colors, triangle_st, 3);
        write_texture32(test, TEXTURE_OFFSET, texture,
                        G_N_ELEMENTS(texture));
        {
            RingBuilder ring = { 0 };

            ring_clear_surface(&ring, 0, false, 0xff000000);
            ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                              R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
            ring_set_texture0(&ring,
                              R128_TEX_MIP_MAP_DISABLE |
                              R128_TEX_WRAP_T_CLAMP |
                              R128_TEX_FORMAT_ARGB8888,
                              R128_COMB_REPLACE_RGBA, 2, 0,
                              TEXTURE_OFFSET, 0);
            ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                             textured_format);
            execute_ring(test, &ring);
            g_assert_cmphex(framebuffer_read(test, 32, 16), ==, 0xffff0000);
        }
        {
            RingBuilder ring = { 0 };

            ring_clear_surface(&ring, 0, false, 0xff000000);
            ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                              R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
            ring_set_texture0(&ring,
                              R128_TEX_MIP_MAP_DISABLE |
                              R128_TEX_WRAP_T_CLAMP |
                              R128_TEX_PERSPECTIVE_DISABLE |
                              R128_TEX_FORMAT_ARGB8888,
                              R128_COMB_REPLACE_RGBA, 2, 0,
                              TEXTURE_OFFSET, 0);
            ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                             textured_format);
            execute_ring(test, &ring);
            g_assert_cmphex(framebuffer_read(test, 32, 16), ==, 0xff0000ff);
        }
    }

    rage128_pm4_stop(test);
}
'''

replace_once(
    test,
    "\nstatic void test_pm4_signed_window_offset(void)\n",
    "\n" + texture_test + "\nstatic void test_pm4_signed_window_offset(void)\n",
)

replace_once(
    test,
    """    g_test_add_func("/ati/rage128/pm4-shading-and-coverage",
                    test_pm4_shading_and_coverage);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
""",
    """    g_test_add_func("/ati/rage128/pm4-shading-and-coverage",
                    test_pm4_shading_and_coverage);
    g_test_add_func("/ati/rage128/pm4-primary-texture",
                    test_pm4_primary_texture);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
""",
)