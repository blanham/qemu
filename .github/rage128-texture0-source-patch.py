#!/usr/bin/env python3

from pathlib import Path


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one exact match, found {count}: {old[:80]!r}"
        )
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


source = Path("hw/display/ati_3d.c")

replace_once(
    source,
    """#define ATI_3D_SPEC_LIGHT_ENABLE     BIT(11)

#define ATI_3D_Z_PIX_WIDTH_MASK      (3U << 1)
""",
    """#define ATI_3D_SPEC_LIGHT_ENABLE     BIT(11)

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
""",
)

replace_once(
    source,
    """} ATI3DSurface;

typedef struct ATI3DVertex {
""",
    """} ATI3DSurface;

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
""",
)

replace_once(
    source,
    """    float color[4];    /* red, green, blue, alpha */
    float specular[4]; /* red, green, blue, fog */
} ATI3DVertex;
""",
    """    float color[4];    /* red, green, blue, alpha */
    float specular[4]; /* red, green, blue, fog */
    float texcoord[2][2];
} ATI3DVertex;
""",
)

replace_once(
    source,
    """    ATI3DSurface *color_surface;
    ATI3DSurface depth_surface;
    uint32_t tex_control;
""",
    """    ATI3DSurface *color_surface;
    ATI3DSurface depth_surface;
    ATI3DTexture texture;
    uint32_t tex_control;
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
    bool texture_enabled;
    bool depth_enabled;
} ATI3DFragmentContext;
""",
)

texture_helpers = r'''
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
'''

replace_once(
    source,
    "\nstatic bool ati_3d_surface_address(const ATIVGAState *s,\n",
    texture_helpers + "\nstatic bool ati_3d_surface_address(const ATIVGAState *s,\n",
)

replace_once(
    source,
    """    ati_3d_unpack_vertex_color(specular, vertex->specular);
    return isfinite(vertex->x) && isfinite(vertex->y) &&
           isfinite(vertex->z) && isfinite(vertex->rhw);
""",
    """    ati_3d_unpack_vertex_color(specular, vertex->specular);
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
""",
)

interpolate_helper = r'''
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
'''

replace_once(
    source,
    "\nstatic bool ati_3d_edge_is_top_left(const ATI3DVertex *a,\n",
    interpolate_helper +
    "\nstatic bool ati_3d_edge_is_top_left(const ATI3DVertex *a,\n",
)

replace_once(
    source,
    """    ctx->depth_dirty_start = UINT64_MAX;
    ctx->depth_enabled = ctx->tex_control & ATI_3D_TEX_Z_ENABLE;

    if (ctx->tex_control & ATI_3D_ALPHA_ENABLE) {
""",
    """    ctx->depth_dirty_start = UINT64_MAX;
    ctx->texture_enabled = ctx->tex_control & ATI_3D_TEXMAP_ENABLE;
    ctx->depth_enabled = ctx->tex_control & ATI_3D_TEX_Z_ENABLE;

    if (ctx->texture_enabled && !ati_3d_texture_decode(s, &ctx->texture)) {
        return false;
    }
    if (ctx->tex_control & ATI_3D_ALPHA_ENABLE) {
""",
)

replace_once(
    source,
    """static bool ati_3d_fragment(ATI3DFragmentContext *ctx, int x, int y,
                            const float src[4], float z)
{
    ATIVGAState *s = ctx->s;
    uint64_t color_address;
    uint32_t color_pixel;

    if (!ati_3d_surface_address(s, ctx->color_surface, x, y,
                                &color_address)) {
        return true;
    }
""",
    """static bool ati_3d_fragment(ATI3DFragmentContext *ctx, int x, int y,
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
""",
)

replace_once(
    source,
    "ati_3d_clamp_channel(src[3]),",
    "ati_3d_clamp_channel(fragment[3]),",
)
replace_once(
    source,
    "ati_3d_blend(ctx->misc, src, dst, blended)",
    "ati_3d_blend(ctx->misc, fragment, dst, blended)",
)
replace_once(
    source,
    "ati_3d_pack_surface_color(ctx->color_surface, src);",
    "ati_3d_pack_surface_color(ctx->color_surface, fragment);",
)

replace_once(
    source,
    """            float src[4];
            float z;
""",
    """            float src[4];
            float texcoord[2];
            const float *texture_coord = NULL;
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
            if (!ati_3d_fragment(&fragments, x, y, src, z)) {
""",
    """                ati_3d_shade_sample(&shade,
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
""",
)

replace_once(
    source,
    """        float color[4];
        float z;
""",
    """        float color[4];
        float texcoord[2];
        const float *texture_coord = NULL;
        float z;
""",
)

replace_once(
    source,
    """            ati_3d_shade_sample(&shade, vertices, weights, 2, color);
        }
        z = a->z * (1.0f - t) + b->z * t;
        if (!ati_3d_fragment(&fragments, x, y, color, z)) {
""",
    """            ati_3d_shade_sample(&shade, vertices, weights, 2, color);
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
""",
)

replace_once(
    source,
    """    result = ati_3d_fragment(&fragments, x, y, color, vertex->z);
""",
    """    result = ati_3d_fragment(
        &fragments, x, y, color, vertex->z,
        fragments.texture_enabled ? vertex->texcoord[0] : NULL);
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
""",
)

replace_once(
    source,
    """        if (!ati_3d_read_vertex(s,
                                address + (dma_addr_t)vertex_index * stride * 4,
                                format, stride, &vertices[i])) {
            goto out;
        }
        vertices[i].x += window_x;
""",
    """        if (!ati_3d_read_vertex(s,
                                address + (dma_addr_t)vertex_index * stride * 4,
                                format, stride, &vertices[i])) {
            goto out;
        }
        if ((tex_control & ATI_3D_TEXMAP_ENABLE) &&
            fabsf(vertices[i].rhw) < 1.0e-20f) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "ATI Rage 128 textured vertex has zero RHW\n");
            goto out;
        }
        vertices[i].x += window_x;
""",
)
