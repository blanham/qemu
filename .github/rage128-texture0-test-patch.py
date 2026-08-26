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


test = Path("tests/qtest/ati-rage128-pm4-test.c")

replace_once(
    test,
    """#define R128_TEX_CNTL_C                0x1c9c
#define R128_MISC_3D_STATE_CNTL_REG    0x1ca0
#define R128_PLANE_3D_MASK_C           0x1d44
""",
    """#define R128_TEX_CNTL_C                0x1c9c
#define R128_MISC_3D_STATE_CNTL_REG    0x1ca0
#define R128_PRIM_TEX_CNTL_C           0x1cb0
#define R128_PRIM_TEX_COMBINE_CNTL_C   0x1cb4
#define R128_TEX_SIZE_PITCH_C          0x1cb8
#define R128_PRIM_TEX_0_OFFSET_C       0x1cbc
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
#define R128_VC_FRMT_S_T               0x00000080U
""",
)

replace_once(
    test,
    """#define R128_TEX_Z_ENABLE              (1U << 0)
#define R128_TEX_Z_WRITE_ENABLE        (1U << 1)
#define R128_TEX_ALPHA_ENABLE          (1U << 9)
""",
    """#define R128_TEX_Z_ENABLE              (1U << 0)
#define R128_TEX_Z_WRITE_ENABLE        (1U << 1)
#define R128_TEXMAP_ENABLE             (1U << 4)
#define R128_TEX_ALPHA_ENABLE          (1U << 9)
""",
)

replace_once(
    test,
    """#define R128_ALPHA_TEST_GREATER        (5U << 24)
#define R128_ALPHA_TEST_ALWAYS         (7U << 24)

#define RING_PHYS                      0x00100000U
""",
    """#define R128_ALPHA_TEST_GREATER        (5U << 24)
#define R128_ALPHA_TEST_ALWAYS         (7U << 24)

#define R128_PRIM_TEX_MIN_LINEAR       (1U << 1)
#define R128_PRIM_TEX_MAG_LINEAR       (1U << 4)
#define R128_PRIM_TEX_MIP_MAP_DISABLE  (1U << 7)
#define R128_PRIM_TEX_CLAMP_S_BORDER   (3U << 8)
#define R128_PRIM_TEX_FORMAT_RGB565    (4U << 16)
#define R128_PRIM_TEX_FORMAT_ARGB8888  (6U << 16)

#define R128_TEX_COMB_INPUT_INTERP     ((4U << 10) | (2U << 25))
#define R128_TEX_COMB_REPLACE_RGBA     ((4U << 4) | (6U << 18) | \
                                        R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_MODULATE_RGBA    ((3U << 0) | (4U << 4) | \
                                        (3U << 14) | (6U << 18) | \
                                        R128_TEX_COMB_INPUT_INTERP)

#define RING_PHYS                      0x00100000U
""",
)

replace_once(
    test,
    """#define DEPTH_OFFSET                   0x00010000U
#define RING_DWORDS                    1024U
""",
    """#define DEPTH_OFFSET                   0x00010000U
#define TEXTURE_OFFSET                 0x00020000U
#define RING_DWORDS                    1024U
""",
)

replace_once(
    test,
    """static uint32_t vram_read32(Rage128PM4Test *test, uint32_t offset)
{
    return qpci_io_readl(test->dev, test->framebuffer, offset);
}

static uint32_t float_bits(float value)
""",
    """static uint32_t vram_read32(Rage128PM4Test *test, uint32_t offset)
{
    return qpci_io_readl(test->dev, test->framebuffer, offset);
}

static void vram_write32(Rage128PM4Test *test, uint32_t offset,
                         uint32_t value)
{
    qpci_io_writel(test->dev, test->framebuffer, offset, value);
}

static uint32_t float_bits(float value)
""",
)

replace_once(
    test,
    """static uint32_t surface_pitch_offset(uint32_t offset, bool tiled)
{
    return (8U << 21) | (offset >> 5) | (tiled ? (1U << 31) : 0);
}

static void ring_clear_surface(RingBuilder *ring, uint32_t offset,
""",
    """static uint32_t surface_pitch_offset(uint32_t offset, bool tiled)
{
    return (8U << 21) | (offset >> 5) | (tiled ? (1U << 31) : 0);
}

static uint32_t texture_size_pitch(unsigned int width_log2,
                                   unsigned int height_log2)
{
    unsigned int size_log2 = MAX(width_log2, height_log2);

    return width_log2 | (size_log2 << 4) | (height_log2 << 8) |
           (size_log2 << 12);
}

static void ring_clear_surface(RingBuilder *ring, uint32_t offset,
""",
)

replace_once(
    test,
    """    ring_packet0_one(ring, R128_PM4_VC_FPU_SETUP, vc_setup);
}

static void ring_draw_format(RingBuilder *ring, uint32_t vertex_offset,
""",
    """    ring_packet0_one(ring, R128_PM4_VC_FPU_SETUP, vc_setup);
}

static void ring_set_texture0_state(RingBuilder *ring, uint32_t control,
                                    uint32_t combine, uint32_t size_pitch,
                                    uint32_t offset, uint32_t border_color)
{
    ring_packet0_one(ring, R128_PRIM_TEX_CNTL_C, control);
    ring_packet0_one(ring, R128_PRIM_TEX_COMBINE_CNTL_C, combine);
    ring_packet0_one(ring, R128_TEX_SIZE_PITCH_C, size_pitch);
    ring_packet0_one(ring, R128_PRIM_TEX_0_OFFSET_C, offset);
    ring_packet0_one(ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, border_color);
}

static void ring_draw_format(RingBuilder *ring, uint32_t vertex_offset,
""",
)

replace_once(
    test,
    """static void write_vertices(Rage128PM4Test *test, uint32_t offset,
                           const float (*xyz)[3], const uint32_t *colors,
                           unsigned int count)
{
    write_vertices_format(test, offset, xyz, colors, NULL, count,
                          R128_VC_FRMT_DIFFUSE_ARGB);
}

static void test_pm4_control_and_2d_packets(void)
""",
    """static void write_vertices(Rage128PM4Test *test, uint32_t offset,
                           const float (*xyz)[3], const uint32_t *colors,
                           unsigned int count)
{
    write_vertices_format(test, offset, xyz, colors, NULL, count,
                          R128_VC_FRMT_DIFFUSE_ARGB);
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

static void test_pm4_control_and_2d_packets(void)
""",
)

texture_tests = r'''
static void test_pm4_texture0_sampling(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t vertex_format = R128_VC_FRMT_RHW |
                                   R128_VC_FRMT_DIFFUSE_ARGB |
                                   R128_VC_FRMT_S_T;
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t white[3] = {
        0xffffffff, 0xffffffff, 0xffffffff,
    };
    const float one[3] = { 1.0f, 1.0f, 1.0f };
    const float mapped[3][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
    };

    load_microcode(test);
    setup_gart(test);
    vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
    vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
    vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);

    /* Nearest sampling and the Mesa unit-zero modulate combiner. */
    {
        RingBuilder ring = { 0 };

        write_textured_vertices(test, 0, triangle, one, white, mapped, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_MODULATE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 16, 16), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 40, 16), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 16, 40), ==, 0xff0000ff);
        g_assert_cmphex(framebuffer_read(test, 40, 40), ==, 0xff000000);
    }

    /* RHW changes the nearest texel relative to affine interpolation. */
    {
        RingBuilder ring = { 0 };
        const float perspective_rhw[3] = { 1.0f, 0.125f, 1.0f };
        const float perspective_st[3][2] = {
            { 0.0f, 0.25f }, { 1.0f, 0.25f }, { 0.0f, 0.25f },
        };

        write_textured_vertices(test, 0, triangle, perspective_rhw, white,
                                perspective_st, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_REPLACE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 32, 16), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 52, 10), ==, 0xff00ff00);
    }

    /* Bilinear filtering at the center averages all four texels. */
    {
        RingBuilder ring = { 0 };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        write_textured_vertices(test, 0, triangle, one, white, centered, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIN_LINEAR |
            R128_PRIM_TEX_MAG_LINEAR |
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_REPLACE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff808080);
    }

    /* The same sampler decodes a 16-bit RGB565 image. */
    {
        RingBuilder ring = { 0 };

        vram_write32(test, TEXTURE_OFFSET + 0, 0x07e0f800);
        vram_write32(test, TEXTURE_OFFSET + 4, 0xffff001f);
        write_textured_vertices(test, 0, triangle, one, white, mapped, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_RGB565,
            R128_TEX_COMB_MODULATE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 16, 16), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 40, 16), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 16, 40), ==, 0xff0000ff);
    }

    /* GL_CLAMP's border-color state is handled by the software sampler. */
    {
        RingBuilder ring = { 0 };
        const float outside[3][2] = {
            { -0.25f, 0.25f }, { -0.25f, 0.25f },
            { -0.25f, 0.25f },
        };

        write_textured_vertices(test, 0, triangle, one, white, outside, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_CLAMP_S_BORDER |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_REPLACE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0xff204080);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff204080);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_texture0_mipmap_fault(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t vertex_format = R128_VC_FRMT_RHW |
                                   R128_VC_FRMT_DIFFUSE_ARGB |
                                   R128_VC_FRMT_S_T;
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t white[3] = {
        0xffffffff, 0xffffffff, 0xffffffff,
    };
    const float one[3] = { 1.0f, 1.0f, 1.0f };
    const float mapped[3][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
    };
    unsigned int draw_start;

    load_microcode(test);
    setup_gart(test);
    vram_write32(test, TEXTURE_OFFSET, 0xffffffff);
    write_textured_vertices(test, 0, triangle, one, white, mapped, 3);
    mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
    ring_set_texture0_state(
        &ring, R128_PRIM_TEX_FORMAT_ARGB8888,
        R128_TEX_COMB_REPLACE_RGBA,
        texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
    draw_start = ring.count;
    ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST, vertex_format);
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);

    /* Dispatch fails on the final primitive payload dword, before raster. */
    execute_faulting_ring(test, &ring, draw_start + 4);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
    rage128_pm4_stop(test);
}
'''

replace_once(
    test,
    "\nstatic void test_pm4_signed_window_offset(void)\n",
    "\n" + texture_tests +
    "\nstatic void test_pm4_signed_window_offset(void)\n",
)

replace_once(
    test,
    """    g_test_add_func("/ati/rage128/pm4-shading-and-coverage",
                    test_pm4_shading_and_coverage);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
""",
    """    g_test_add_func("/ati/rage128/pm4-shading-and-coverage",
                    test_pm4_shading_and_coverage);
    g_test_add_func("/ati/rage128/pm4-texture0-sampling",
                    test_pm4_texture0_sampling);
    g_test_add_func("/ati/rage128/pm4-texture0-mipmap-fault",
                    test_pm4_texture0_mipmap_fault);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
""",
)
