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
    '''        if ((tex_control & ATI_3D_TEXMAP_ENABLE) &&
            fabsf(vertices[i].rhw) < 1.0e-20f) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "ATI Rage 128 textured vertex has zero RHW\\n");
            goto out;
        }
''',
    '''        if ((tex_control & ATI_3D_TEXMAP_ENABLE) &&
            vertices[i].rhw <= 0.0f) {
            qemu_log_mask(LOG_GUEST_ERROR,
                          "ATI Rage 128 textured vertex has non-positive RHW\\n");
            goto out;
        }
''',
)


test = Path("tests/qtest/ati-rage128-pm4-test.c")
replace_once(
    test,
    '''#define R128_PRIM_TEX_MIN_LINEAR       (1U << 1)
#define R128_PRIM_TEX_MAG_LINEAR       (1U << 4)
#define R128_PRIM_TEX_MIP_MAP_DISABLE  (1U << 7)
#define R128_PRIM_TEX_CLAMP_S_BORDER   (3U << 8)
#define R128_PRIM_TEX_FORMAT_RGB565    (4U << 16)
#define R128_PRIM_TEX_FORMAT_ARGB8888  (6U << 16)

#define R128_TEX_COMB_INPUT_INTERP     ((4U << 10) | (2U << 25))
#define R128_TEX_COMB_REPLACE_RGBA     ((4U << 4) | (6U << 18) |                                         R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_MODULATE_RGBA    ((3U << 0) | (4U << 4) |                                         (3U << 14) | (6U << 18) |                                         R128_TEX_COMB_INPUT_INTERP)
''',
    r'''#define R128_PRIM_TEX_MIN_LINEAR       (1U << 1)
#define R128_PRIM_TEX_MAG_LINEAR       (1U << 4)
#define R128_PRIM_TEX_MIP_MAP_DISABLE  (1U << 7)
#define R128_PRIM_TEX_CLAMP_S_MIRROR   (1U << 8)
#define R128_PRIM_TEX_CLAMP_S_CLAMP    (2U << 8)
#define R128_PRIM_TEX_CLAMP_S_BORDER   (3U << 8)
#define R128_PRIM_TEX_CLAMP_T_MIRROR   (1U << 11)
#define R128_PRIM_TEX_FORMAT_ARGB1555  (3U << 16)
#define R128_PRIM_TEX_FORMAT_RGB565    (4U << 16)
#define R128_PRIM_TEX_FORMAT_ARGB8888  (6U << 16)
#define R128_PRIM_TEX_FORMAT_RGB8      (9U << 16)
#define R128_PRIM_TEX_FORMAT_ARGB4444  (15U << 16)

#define R128_TEX_COMB_INPUT_INTERP \
    ((4U << 10) | (2U << 25))
#define R128_TEX_COMB_REPLACE_RGBA \
    ((4U << 4) | (6U << 18) | R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_MODULATE_RGBA \
    ((3U << 0) | (4U << 4) | (3U << 14) | (6U << 18) | \
     R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_DECAL_RGBA \
    ((9U << 0) | (4U << 4) | (2U << 14) | (6U << 18) | \
     R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_ADD_RGBA \
    ((6U << 0) | (4U << 4) | (3U << 14) | (6U << 18) | \
     R128_TEX_COMB_INPUT_INTERP)
''',
)

additional_sampling = r'''
    /* Repeat, mirrored repeat, and clamp-to-edge resolve outside ST. */
    {
        static const struct {
            uint32_t clamp;
            float s;
            float t;
            uint32_t expected;
        } cases[] = {
            { 0, 1.25f, 0.25f, 0xffff0000 },
            { R128_PRIM_TEX_CLAMP_S_MIRROR,
              1.25f, 0.25f, 0xff00ff00 },
            { R128_PRIM_TEX_CLAMP_S_CLAMP,
              -0.25f, 0.25f, 0xffff0000 },
            { R128_PRIM_TEX_CLAMP_T_MIRROR,
              0.25f, 1.25f, 0xff0000ff },
        };

        vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
        vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
        vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
        vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);
        for (unsigned int i = 0; i < G_N_ELEMENTS(cases); i++) {
            RingBuilder ring = { 0 };
            float outside[3][2] = {
                { cases[i].s, cases[i].t },
                { cases[i].s, cases[i].t },
                { cases[i].s, cases[i].t },
            };

            write_textured_vertices(test, 0, triangle, one, white,
                                    outside, 3);
            ring_clear_surface(&ring, 0, false, 0xff000000);
            ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                              R128_ALPHA_TEST_ALWAYS, UINT32_MAX,
                              vc_setup);
            ring_set_texture0_state(
                &ring,
                R128_PRIM_TEX_MIP_MAP_DISABLE | cases[i].clamp |
                R128_PRIM_TEX_FORMAT_ARGB8888,
                R128_TEX_COMB_REPLACE_RGBA,
                texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
            ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                             vertex_format);
            execute_ring(test, &ring);
            g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                            cases[i].expected);
        }
    }

    /* Packed 1555, 4444, and the RGB8/332 mode share the sampler. */
    {
        static const struct {
            uint32_t format;
            uint32_t texel;
            uint32_t expected;
        } cases[] = {
            { R128_PRIM_TEX_FORMAT_ARGB1555, 0x0000fc00, 0xffff0000 },
            { R128_PRIM_TEX_FORMAT_ARGB4444, 0x0000f0f0, 0xff00ff00 },
            { R128_PRIM_TEX_FORMAT_RGB8, 0x000000e3, 0xffff00ff },
        };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        for (unsigned int i = 0; i < G_N_ELEMENTS(cases); i++) {
            RingBuilder ring = { 0 };

            vram_write32(test, TEXTURE_OFFSET, cases[i].texel);
            write_textured_vertices(test, 0, triangle, one, white,
                                    centered, 3);
            ring_clear_surface(&ring, 0, false, 0xff000000);
            ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                              R128_ALPHA_TEST_ALWAYS, UINT32_MAX,
                              vc_setup);
            ring_set_texture0_state(
                &ring,
                R128_PRIM_TEX_MIP_MAP_DISABLE | cases[i].format,
                R128_TEX_COMB_REPLACE_RGBA,
                texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
            ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                             vertex_format);
            execute_ring(test, &ring);
            g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                            cases[i].expected);
        }
    }

    /* Mesa's GL_ADD and GL_DECAL unit-zero combiner programs. */
    {
        RingBuilder ring = { 0 };
        const uint32_t incoming[3] = {
            0xff302010, 0xff302010, 0xff302010,
        };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        vram_write32(test, TEXTURE_OFFSET, 0xff010203);
        write_textured_vertices(test, 0, triangle, one, incoming,
                                centered, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_ADD_RGBA,
            texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff112233);
    }
    {
        RingBuilder ring = { 0 };
        const uint32_t incoming[3] = {
            0xff0000ff, 0xff0000ff, 0xff0000ff,
        };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        vram_write32(test, TEXTURE_OFFSET, 0x8000ff00);
        write_textured_vertices(test, 0, triangle, one, incoming,
                                centered, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_DECAL_RGBA,
            texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff7f8000);
    }
'''

replace_once(
    test,
    '''    rage128_pm4_stop(test);
}

static void test_pm4_texture0_mipmap_fault(void)
''',
    additional_sampling +
    '''
    rage128_pm4_stop(test);
}

static void test_pm4_texture0_mipmap_fault(void)
''',
)

invalid_rhw_test = r'''
static void test_pm4_texture0_invalid_rhw_fault(void)
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
    const float invalid_rhw[3] = { 1.0f, -1.0f, 1.0f };
    const float mapped[3][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
    };
    unsigned int draw_start;

    load_microcode(test);
    setup_gart(test);
    vram_write32(test, TEXTURE_OFFSET, 0xffffffff);
    write_textured_vertices(test, 0, triangle, invalid_rhw, white,
                            mapped, 3);
    mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
    ring_set_texture0_state(
        &ring,
        R128_PRIM_TEX_MIP_MAP_DISABLE |
        R128_PRIM_TEX_FORMAT_ARGB8888,
        R128_TEX_COMB_REPLACE_RGBA,
        texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
    draw_start = ring.count;
    ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                     vertex_format);
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);

    execute_faulting_ring(test, &ring, draw_start + 4);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
    rage128_pm4_stop(test);
}

'''

replace_once(
    test,
    '''static void test_pm4_texture0_mipmap_fault(void)
''',
    invalid_rhw_test +
    '''static void test_pm4_texture0_mipmap_fault(void)
''',
)

replace_once(
    test,
    '''    g_test_add_func("/ati/rage128/pm4-texture0-mipmap-fault",
                    test_pm4_texture0_mipmap_fault);
''',
    '''    g_test_add_func("/ati/rage128/pm4-texture0-mipmap-fault",
                    test_pm4_texture0_mipmap_fault);
    g_test_add_func("/ati/rage128/pm4-texture0-invalid-rhw-fault",
                    test_pm4_texture0_invalid_rhw_fault);
''',
)
