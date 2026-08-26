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


source = Path("hw/display/ati_3d.c")
replace_once(
    source,
    """    unsigned int alpha_input = extract32(
        combine, ATI_3D_COMB_ALPHA_INPUT_SHIFT, 3);

    if (combine & ATI_3D_COMB_FCN_MSB) {
        return false;
    }
""",
    """    unsigned int alpha_input = extract32(
        combine, ATI_3D_COMB_ALPHA_INPUT_SHIFT, 3);
    bool blend_constant =
        (combine & ATI_3D_COMB_FCN_MSB) &&
        color_op == ATI_3D_COMB_MODULATE2X &&
        color_factor == ATI_3D_COLOR_FACTOR_CONST &&
        ctx->s->dev_id == PCI_DEVICE_ID_ATI_RAGE128_PF &&
        !ctx->s->rage128_pci;

    if ((combine & ATI_3D_COMB_FCN_MSB) && !blend_constant) {
        return false;
    }
""",
)
replace_once(
    source,
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
""",
    """        if (blend_constant) {
            float texel = texture_color[channel] / 255.0f;

            output[channel] = input[channel] * (1.0f - texel) +
                              constant[channel] * texel;
        } else if (color_op == ATI_3D_COMB_BLEND_TEXTURE) {
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
""",
)

test = Path("tests/qtest/ati-rage128-pm4-test.c")
replace_once(
    test,
    """#define ATI_RAGE128_RE_DEVICE_ID       0x5245
""",
    """#define ATI_RAGE128_PF_DEVICE_ID       0x5046
#define ATI_RAGE128_RE_DEVICE_ID       0x5245
""",
)
replace_once(
    test,
    """#define R128_COMB_MODULATE             3U
#define R128_COMB_ADD                  6U
#define R128_COMB_BLEND_TEXTURE        9U
#define R128_COLOR_FACTOR_TEX          (4U << 4)
""",
    """#define R128_COMB_MODULATE             3U
#define R128_COMB_MODULATE2X           4U
#define R128_COMB_ADD                  6U
#define R128_COMB_BLEND_TEXTURE        9U
#define R128_COLOR_FACTOR_CONST        (0U << 4)
#define R128_COLOR_FACTOR_TEX          (4U << 4)
#define R128_COMB_FCN_MSB              (1U << 8)
""",
)
replace_once(
    test,
    """#define R128_COMB_ADD_RGBA \\
    (R128_COMB_ADD | R128_COLOR_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_COLOR | R128_COMB_ALPHA_MODULATE | \\
     R128_ALPHA_FACTOR_TEX | R128_INPUT_FACTOR_INT_ALPHA)
""",
    """#define R128_COMB_ADD_RGBA \\
    (R128_COMB_ADD | R128_COLOR_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_COLOR | R128_COMB_ALPHA_MODULATE | \\
     R128_ALPHA_FACTOR_TEX | R128_INPUT_FACTOR_INT_ALPHA)
#define R128_COMB_PRO_BLEND_RGB \\
    (R128_COMB_MODULATE2X | R128_COMB_FCN_MSB | \\
     R128_COLOR_FACTOR_CONST | R128_INPUT_FACTOR_INT_COLOR | \\
     R128_COMB_ALPHA_COPY_INPUT | R128_ALPHA_FACTOR_TEX | \\
     R128_INPUT_FACTOR_INT_ALPHA)
""",
)
replace_once(
    test,
    """static Rage128PM4Test *rage128_pm4_start(void)
{
    Rage128PM4Test *test = g_new0(Rage128PM4Test, 1);
    uint16_t command;

    test->qts = qtest_init("-machine pc -m 64M -vga none "
                           "-device ati-vga,model=rage128re,addr=04.0");
    test->bus = qpci_new_pc(test->qts, NULL);
    test->dev = qpci_device_find(test->bus, QPCI_DEVFN(4, 0));
    g_assert_nonnull(test->dev);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_VENDOR_ID), ==,
                    ATI_VENDOR_ID);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_DEVICE_ID), ==,
                    ATI_RAGE128_RE_DEVICE_ID);

    qpci_device_enable(test->dev);
    command = qpci_config_readw(test->dev, PCI_COMMAND);
    qpci_config_writew(test->dev, PCI_COMMAND,
                       command | PCI_COMMAND_MEMORY | PCI_COMMAND_MASTER);
    test->framebuffer = qpci_iomap(test->dev, 0, &test->framebuffer_size);
    test->mmio = qpci_iomap(test->dev, 2, &test->mmio_size);
    g_assert_cmpuint(test->framebuffer_size, ==, 64 * MiB);
    g_assert_cmpuint(test->mmio_size, ==, 0x4000);
    return test;
}
""",
    """static Rage128PM4Test *rage128_pm4_start_model(const char *model,
                                                       uint16_t device_id)
{
    Rage128PM4Test *test = g_new0(Rage128PM4Test, 1);
    char *args = g_strdup_printf(
        "-machine pc -m 64M -vga none "
        "-device ati-vga,model=%s,addr=04.0", model);
    uint16_t command;

    test->qts = qtest_init(args);
    g_free(args);
    test->bus = qpci_new_pc(test->qts, NULL);
    test->dev = qpci_device_find(test->bus, QPCI_DEVFN(4, 0));
    g_assert_nonnull(test->dev);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_VENDOR_ID), ==,
                    ATI_VENDOR_ID);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_DEVICE_ID), ==,
                    device_id);

    qpci_device_enable(test->dev);
    command = qpci_config_readw(test->dev, PCI_COMMAND);
    qpci_config_writew(test->dev, PCI_COMMAND,
                       command | PCI_COMMAND_MEMORY | PCI_COMMAND_MASTER);
    test->framebuffer = qpci_iomap(test->dev, 0, &test->framebuffer_size);
    test->mmio = qpci_iomap(test->dev, 2, &test->mmio_size);
    g_assert_cmpuint(test->framebuffer_size, ==, 64 * MiB);
    g_assert_cmpuint(test->mmio_size, ==, 0x4000);
    return test;
}

static Rage128PM4Test *rage128_pm4_start(void)
{
    return rage128_pm4_start_model("rage128re",
                                   ATI_RAGE128_RE_DEVICE_ID);
}
""",
)

pro_test = r'''static void test_pm4_primary_texture_pro_blend(void)
{
    Rage128PM4Test *test =
        rage128_pm4_start_model("rage128p", ATI_RAGE128_PF_DEVICE_ID);
    RingBuilder ring = { 0 };
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t textured_format = R128_VC_FRMT_RHW |
                                     R128_VC_FRMT_DIFFUSE_ARGB |
                                     R128_VC_FRMT_ST;
    const float point[1][3] = { { 20.0f, 20.0f, 0.0f } };
    const float rhw[1] = { 1.0f };
    const uint32_t color[1] = { 0xff0000ff };
    const float st[1][2] = { { 0.5f, 0.5f } };
    const uint32_t texture = 0xff808080;

    load_microcode(test);
    setup_gart(test);
    write_textured_vertices(test, 0, point, rhw, color, st, 1);
    write_texture32(test, TEXTURE_OFFSET, &texture, 1);
    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
    ring_set_texture0(&ring,
                      R128_TEX_MIP_MAP_DISABLE |
                      R128_TEX_FORMAT_ARGB8888,
                      R128_COMB_PRO_BLEND_RGB, 0, 0,
                      TEXTURE_OFFSET, 0);
    ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, 0xff0000ff);
    ring_draw_format(&ring, 0, 1, R128_VC_PRIM_POINT,
                     textured_format);
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff7f0080);
    rage128_pm4_stop(test);
}

'''
insert_before_once(
    test,
    "static void test_pm4_signed_window_offset(void)\n",
    pro_test,
    "static void test_pm4_primary_texture_pro_blend(void)",
)
replace_once(
    test,
    """    g_test_add_func("/ati/rage128/pm4-primary-texture",
                    test_pm4_primary_texture);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
""",
    """    g_test_add_func("/ati/rage128/pm4-primary-texture",
                    test_pm4_primary_texture);
    g_test_add_func("/ati/rage128/pm4-primary-texture-pro-blend",
                    test_pm4_primary_texture_pro_blend);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
""",
)
