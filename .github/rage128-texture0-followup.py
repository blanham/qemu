#!/usr/bin/env python3

from pathlib import Path


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


def replace_token(path: Path, old: str, new: str, expected: int) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0 and text.count(new) >= expected:
        return
    if count != expected:
        raise SystemExit(
            f"{path}: expected {expected} token matches, found {count}: {old!r}"
        )
    path.write_text(text.replace(old, new), encoding="utf-8")


def insert_before_once(path: Path, marker: str, insertion: str,
                       done_marker: str) -> None:
    text = path.read_text(encoding="utf-8")
    if done_marker in text:
        return
    count = text.count(marker)
    if count != 1:
        raise SystemExit(
            f"{path}: expected one insertion marker, found {count}: {marker!r}"
        )
    path.write_text(text.replace(marker, insertion + marker, 1),
                    encoding="utf-8")


header = Path("hw/display/ati_int.h")
replace_once(
    header,
    """#define ATI_PM4_PACKET_MAX_DWORDS 4096
#define ATI_3D_REG_DWORDS ((0x1e00 - 0x1800) / sizeof(uint32_t))
""",
    """#define ATI_PM4_PACKET_MAX_DWORDS 4096
#define ATI_RAGE128_GART_VIRT_BASE UINT32_C(0x02000000)
#define ATI_RAGE128_GART_VIRT_END  UINT32_C(0x04000000)
#define ATI_3D_REG_DWORDS ((0x1e00 - 0x1800) / sizeof(uint32_t))
""",
)

pm4 = Path("hw/display/ati_pm4.c")
replace_once(
    pm4,
    """#define ATI_PM4_MAX_INDIRECT_DEPTH 4
#define ATI_PM4_AGP_VIRT_BASE UINT32_C(0x02000000)
#define ATI_PM4_AGP_VIRT_END  UINT32_C(0x04000000)
""",
    """#define ATI_PM4_MAX_INDIRECT_DEPTH 4
""",
)
replace_token(pm4, "ATI_PM4_AGP_VIRT_BASE",
              "ATI_RAGE128_GART_VIRT_BASE", 2)
replace_token(pm4, "ATI_PM4_AGP_VIRT_END",
              "ATI_RAGE128_GART_VIRT_END", 1)

source = Path("hw/display/ati_3d.c")
replace_once(
    source,
    """#define ATI_3D_TEX_FORMAT_RGB332      7U
#define ATI_3D_TEX_FORMAT_ARGB4444    15U
""",
    """#define ATI_3D_TEX_FORMAT_RGB332      7U
#define ATI_3D_TEX_FORMAT_RGB8        9U
#define ATI_3D_TEX_FORMAT_ARGB4444    15U
""",
)
replace_once(
    source,
    """#define ATI_3D_COMB_ADD               6U
""",
    """#define ATI_3D_COMB_ADD               6U
#define ATI_3D_COMB_BLEND_TEXTURE     9U
""",
)
replace_once(
    source,
    """    unsigned int format;
    unsigned int bytes_per_pixel;
} ATI3DTexture;
""",
    """    unsigned int format;
    unsigned int bytes_per_pixel;
    bool gart;
} ATI3DTexture;
""",
)
replace_once(
    source,
    """    case ATI_3D_TEX_FORMAT_RGB332:
        color[0] = ((value >> 5) & 7) * (255.0f / 7.0f);
""",
    """    case ATI_3D_TEX_FORMAT_RGB332:
    case ATI_3D_TEX_FORMAT_RGB8:
        color[0] = ((value >> 5) & 7) * (255.0f / 7.0f);
""",
)
replace_once(
    source,
    """    case ATI_3D_TEX_FORMAT_RGB332:
        texture->bytes_per_pixel = 1;
        break;
""",
    """    case ATI_3D_TEX_FORMAT_RGB332:
    case ATI_3D_TEX_FORMAT_RGB8:
        texture->bytes_per_pixel = 1;
        break;
""",
)
replace_once(
    source,
    """    texture->offset &= ~ATI_3D_TEX_TILE_MASK;
    texture->width = 1U << pitch_log2;
    texture->height = 1U << height_log2;
    texture->stride = texture->width * texture->bytes_per_pixel;
    end = (uint64_t)texture->offset +
          (uint64_t)texture->height * texture->stride;
    if (!texture->stride || end > ctx->s->vga.vram_size) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 texture surface exceeds VRAM\\n");
        return false;
    }
    return true;
}
""",
    """    texture->offset &= ~ATI_3D_TEX_TILE_MASK;
    texture->width = 1U << pitch_log2;
    texture->height = 1U << height_log2;
    texture->stride = texture->width * texture->bytes_per_pixel;
    end = (uint64_t)texture->offset +
          (uint64_t)texture->height * texture->stride;
    if (!texture->stride) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 texture has invalid dimensions\\n");
        return false;
    }
    if (texture->offset >= ATI_RAGE128_GART_VIRT_BASE &&
        end <= ATI_RAGE128_GART_VIRT_END) {
        texture->gart = true;
    } else if (end <= ctx->s->vga.vram_size) {
        texture->gart = false;
    } else {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "ATI Rage 128 texture surface exceeds local VRAM/GART\\n");
        return false;
    }
    return true;
}
""",
)
replace_once(
    source,
    """static bool ati_3d_texture_fetch(const ATI3DFragmentContext *ctx,
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
""",
    """static bool ati_3d_texture_fetch(const ATI3DFragmentContext *ctx,
                                 int x, int y, float color[4])
{
    const ATI3DTexture *texture = &ctx->texture;
    unsigned int wrap_s = extract32(texture->control,
                                    ATI_3D_TEX_CLAMP_S_SHIFT, 2);
    unsigned int wrap_t = extract32(texture->control,
                                    ATI_3D_TEX_CLAMP_T_SHIFT, 2);
    uint8_t raw[4];
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
    if (texture->gart) {
        if (address + texture->bytes_per_pixel >
                ATI_RAGE128_GART_VIRT_END ||
            !ati_pm4_read_guest(ctx->s, address, raw,
                                texture->bytes_per_pixel)) {
            return false;
        }
    } else {
        if (address + texture->bytes_per_pixel > ctx->s->vga.vram_size) {
            return false;
        }
        memcpy(raw, ctx->s->vga.vram_ptr + address,
               texture->bytes_per_pixel);
    }
    for (unsigned int byte = 0; byte < texture->bytes_per_pixel; byte++) {
        unsigned int shift = ctx->s->vga.big_endian_fb ?
            (texture->bytes_per_pixel - 1 - byte) * 8 : byte * 8;

        value |= (uint32_t)raw[byte] << shift;
    }
    ati_3d_unpack_texture_color(texture, value, color);
    return true;
}
""",
)
replace_once(
    source,
    """        if (!ati_3d_combine_value(color_op, input[channel], factor[channel],
                                  &output[channel])) {
            return false;
        }
""",
    """        if (color_op == ATI_3D_COMB_BLEND_TEXTURE) {
            float alpha;

            if (color_factor != ATI_3D_COLOR_FACTOR_TEXTURE) {
                return false;
            }
            alpha = texture_color[3] / 255.0f;
            output[channel] = input[channel] * (1.0f - alpha) +
                              texture_color[channel] * alpha;
        } else if (!ati_3d_combine_value(color_op, input[channel],
                                         factor[channel],
                                         &output[channel])) {
            return false;
        }
""",
)
replace_once(
    source,
    """    if (!ati_3d_combine_value(alpha_op, input[3], factor[3], &output[3])) {
        return false;
    }
    memcpy(fragment, output, sizeof(output));
    return true;
}
""",
    """    if (!ati_3d_combine_value(alpha_op, input[3], factor[3], &output[3])) {
        return false;
    }
    for (unsigned int channel = 0; channel < 4; channel++) {
        output[channel] = ati_3d_clamp_channel(output[channel]);
    }
    memcpy(fragment, output, sizeof(output));
    return true;
}
""",
)

test = Path("tests/qtest/ati-rage128-pm4-test.c")
replace_once(
    test,
    """#define R128_ALPHA_BLEND_DST_ONE       (1U << 20)
#define R128_ALPHA_BLEND_DST_INVSRCALPHA (5U << 20)
""",
    """#define R128_ALPHA_BLEND_DST_ONE       (1U << 20)
#define R128_ALPHA_BLEND_DST_SRCCOLOR  (2U << 20)
#define R128_ALPHA_BLEND_DST_INVSRCALPHA (5U << 20)
""",
)
replace_once(
    test,
    """#define R128_TEX_FORMAT_RGB565         (4U << 16)
#define R128_TEX_FORMAT_ARGB8888       (6U << 16)
""",
    """#define R128_TEX_FORMAT_RGB565         (4U << 16)
#define R128_TEX_FORMAT_ARGB8888       (6U << 16)
#define R128_TEX_FORMAT_RGB8           (9U << 16)
""",
)
replace_once(
    test,
    """#define R128_COMB_DISABLE              0U
#define R128_COMB_MODULATE             3U
#define R128_COLOR_FACTOR_TEX          (4U << 4)
#define R128_INPUT_FACTOR_INT_COLOR    (4U << 10)
#define R128_COMB_ALPHA_DISABLE        (0U << 14)
#define R128_COMB_ALPHA_MODULATE       (3U << 14)
#define R128_ALPHA_FACTOR_TEX          (6U << 18)
#define R128_INPUT_FACTOR_INT_ALPHA    (2U << 25)
#define R128_COMB_REPLACE_RGBA         (R128_COMB_DISABLE |                                         R128_COLOR_FACTOR_TEX |                                         R128_INPUT_FACTOR_INT_COLOR |                                         R128_COMB_ALPHA_DISABLE |                                         R128_ALPHA_FACTOR_TEX |                                         R128_INPUT_FACTOR_INT_ALPHA)
#define R128_COMB_MODULATE_RGBA        (R128_COMB_MODULATE |                                         R128_COLOR_FACTOR_TEX |                                         R128_INPUT_FACTOR_INT_COLOR |                                         R128_COMB_ALPHA_MODULATE |                                         R128_ALPHA_FACTOR_TEX |                                         R128_INPUT_FACTOR_INT_ALPHA)
""",
    """#define R128_COMB_DISABLE              0U
#define R128_COMB_MODULATE             3U
#define R128_COMB_ADD                  6U
#define R128_COMB_BLEND_TEXTURE        9U
#define R128_COLOR_FACTOR_TEX          (4U << 4)
#define R128_INPUT_FACTOR_INT_COLOR    (4U << 10)
#define R128_COMB_ALPHA_DISABLE        (0U << 14)
#define R128_COMB_ALPHA_COPY_INPUT     (2U << 14)
#define R128_COMB_ALPHA_MODULATE       (3U << 14)
#define R128_ALPHA_FACTOR_TEX          (6U << 18)
#define R128_INPUT_FACTOR_INT_ALPHA    (2U << 25)
#define R128_COMB_REPLACE_RGBA \\
    (R128_COMB_DISABLE | R128_COLOR_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_COLOR | R128_COMB_ALPHA_DISABLE | \\
     R128_ALPHA_FACTOR_TEX | R128_INPUT_FACTOR_INT_ALPHA)
#define R128_COMB_MODULATE_RGBA \\
    (R128_COMB_MODULATE | R128_COLOR_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_COLOR | R128_COMB_ALPHA_MODULATE | \\
     R128_ALPHA_FACTOR_TEX | R128_INPUT_FACTOR_INT_ALPHA)
#define R128_COMB_DECAL_RGBA \\
    (R128_COMB_BLEND_TEXTURE | R128_COLOR_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_COLOR | R128_COMB_ALPHA_COPY_INPUT | \\
     R128_ALPHA_FACTOR_TEX | R128_INPUT_FACTOR_INT_ALPHA)
#define R128_COMB_ADD_RGBA \\
    (R128_COMB_ADD | R128_COLOR_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_COLOR | R128_COMB_ALPHA_MODULATE | \\
     R128_ALPHA_FACTOR_TEX | R128_INPUT_FACTOR_INT_ALPHA)
""",
)
replace_once(
    test,
    """#define INDIRECT_PHYS                  0x00130000U
#define GART_PHYS                      0x00180000U
""",
    """#define INDIRECT_PHYS                  0x00130000U
#define TEXTURE_GART_PHYS              0x00140000U
#define GART_PHYS                      0x00180000U
""",
)
replace_once(
    test,
    """    uint32_t page_table[4] = {
        cpu_to_le32(RING_PHYS),
        cpu_to_le32(VERTEX_PHYS),
        cpu_to_le32(RPTR_PHYS),
        cpu_to_le32(INDIRECT_PHYS),
    };
""",
    """    uint32_t page_table[5] = {
        cpu_to_le32(RING_PHYS),
        cpu_to_le32(VERTEX_PHYS),
        cpu_to_le32(RPTR_PHYS),
        cpu_to_le32(INDIRECT_PHYS),
        cpu_to_le32(TEXTURE_GART_PHYS),
    };
""",
)
replace_once(
    test,
    """static void write_texture32(Rage128PM4Test *test, uint32_t offset,
                            const uint32_t *pixels, unsigned int count)
{
    for (unsigned int i = 0; i < count; i++) {
        qpci_io_writel(test->dev, test->framebuffer,
                       offset + i * sizeof(uint32_t), pixels[i]);
    }
}
""",
    """static void write_texture32(Rage128PM4Test *test, uint32_t offset,
                            const uint32_t *pixels, unsigned int count)
{
    for (unsigned int i = 0; i < count; i++) {
        qpci_io_writel(test->dev, test->framebuffer,
                       offset + i * sizeof(uint32_t), pixels[i]);
    }
}

static void write_texture8(Rage128PM4Test *test, uint32_t offset,
                           const uint8_t *pixels, unsigned int count)
{
    for (unsigned int i = 0; i < count; i++) {
        qpci_io_writeb(test->dev, test->framebuffer, offset + i, pixels[i]);
    }
}

static void write_gart_texture32(Rage128PM4Test *test,
                                 const uint32_t *pixels,
                                 unsigned int count)
{
    uint32_t *raw = g_new(uint32_t, count);

    for (unsigned int i = 0; i < count; i++) {
        raw[i] = cpu_to_le32(pixels[i]);
    }
    qtest_memwrite(test->qts, TEXTURE_GART_PHYS, raw,
                   count * sizeof(*raw));
    g_free(raw);
}
""",
)

texture_tests = r'''    /* Mesa uploads RGB332 through hardware datatype RGB8 (9). */
    {
        RingBuilder ring = { 0 };
        const uint8_t texture[2] = { 0xe0, 0x1c };
        const float points[2][3] = {
            { 22.0f, 10.0f, 0.0f }, { 24.0f, 10.0f, 0.0f },
        };
        const float point_rhw[2] = { 1, 1 };
        const uint32_t point_colors[2] = { UINT32_MAX, UINT32_MAX };
        const float point_st[2][2] = {
            { 0.25f, 0.5f }, { 0.75f, 0.5f },
        };

        write_textured_vertices(test, 0, points, point_rhw,
                                point_colors, point_st, 2);
        write_texture8(test, TEXTURE_OFFSET, texture,
                       G_N_ELEMENTS(texture));
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_WRAP_T_CLAMP |
                          R128_TEX_FORMAT_RGB8,
                          R128_COMB_REPLACE_RGBA, 1, 0,
                          TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 2, R128_VC_PRIM_POINT,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 22, 10), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 24, 10), ==, 0xff00ff00);
    }

    /* The PCI-GART texture heap uses the same sampler as local VRAM. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture[2] = { 0xffff0000, 0xff0000ff };
        const float points[2][3] = {
            { 26.0f, 10.0f, 0.0f }, { 28.0f, 10.0f, 0.0f },
        };
        const float point_rhw[2] = { 1, 1 };
        const uint32_t point_colors[2] = { UINT32_MAX, UINT32_MAX };
        const float point_st[2][2] = {
            { 0.25f, 0.5f }, { 0.75f, 0.5f },
        };

        write_textured_vertices(test, 0, points, point_rhw,
                                point_colors, point_st, 2);
        write_gart_texture32(test, texture, G_N_ELEMENTS(texture));
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_WRAP_T_CLAMP |
                          R128_TEX_FORMAT_ARGB8888,
                          R128_COMB_REPLACE_RGBA, 1, 0,
                          GART_VIRT + 0x4000, 0);
        ring_draw_format(&ring, 0, 2, R128_VC_PRIM_POINT,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 26, 10), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 28, 10), ==, 0xff0000ff);
    }

    /* RGBA DECAL blends texture RGB by texture alpha and preserves Af. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture = 0x80ff0000;
        const float point[1][3] = { { 30.0f, 10.0f, 0.0f } };
        const float point_rhw[1] = { 1 };
        const uint32_t point_color[1] = { 0xff00ff00 };
        const float point_st[1][2] = { { 0.5f, 0.5f } };

        write_textured_vertices(test, 0, point, point_rhw,
                                point_color, point_st, 1);
        write_texture32(test, TEXTURE_OFFSET, &texture, 1);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_FORMAT_ARGB8888,
                          R128_COMB_DECAL_RGBA, 0, 0,
                          TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 1, R128_VC_PRIM_POINT,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 30, 10), ==, 0xff807f00);
    }

    /* Combiner arithmetic saturates before framebuffer blend factors. */
    {
        RingBuilder ring = { 0 };
        const uint32_t texture = 0xffff0000;
        const float point[1][3] = { { 32.0f, 10.0f, 0.0f } };
        const float point_rhw[1] = { 1 };
        const uint32_t point_color[1] = { 0xff0000ff };
        const float point_st[1][2] = { { 0.5f, 0.5f } };

        write_textured_vertices(test, 0, point, point_rhw,
                                point_color, point_st, 1);
        write_texture32(test, TEXTURE_OFFSET, &texture, 1);
        ring_clear_surface(&ring, 0, false, 0xff400000);
        ring_set_3d_state(&ring,
                          R128_TEXMAP_ENABLE | R128_TEX_ALPHA_ENABLE,
                          R128_ALPHA_BLEND_DST_SRCCOLOR |
                          R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_setup);
        ring_set_texture0(&ring,
                          R128_TEX_MIP_MAP_DISABLE |
                          R128_TEX_FORMAT_ARGB8888,
                          R128_COMB_ADD_RGBA, 0, 0,
                          TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 1, R128_VC_PRIM_POINT,
                         textured_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 32, 10), ==, 0xff400000);
    }

'''
insert_before_once(
    test,
    "    /* RHW drives perspective-correct S/T unless explicitly disabled. */\n",
    texture_tests,
    "Mesa uploads RGB332 through hardware datatype RGB8",
)
