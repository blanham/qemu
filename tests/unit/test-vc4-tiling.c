/*
 * VideoCore IV tiling address translation tests
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_tiling.h"

static uint64_t offset(uint8_t tiling, uint32_t stride,
                       uint32_t x, uint32_t y)
{
    uint64_t value = UINT64_MAX;

    g_assert_true(vc4_tiling_rgba8888_offset(
        tiling, stride, x, y, &value));
    return value;
}

static void test_stride(void)
{
    uint32_t stride;

    g_assert_true(vc4_tiling_rgba8888_stride(
        VC4_TILING_FORMAT_LINEAR, 63, &stride));
    g_assert_cmpuint(stride, ==, 252);
    g_assert_true(vc4_tiling_rgba8888_stride(
        VC4_TILING_FORMAT_LT, 63, &stride));
    g_assert_cmpuint(stride, ==, 256);
    g_assert_true(vc4_tiling_rgba8888_stride(
        VC4_TILING_FORMAT_T, 33, &stride));
    g_assert_cmpuint(stride, ==, 256);

    g_assert_false(vc4_tiling_rgba8888_stride(3, 64, &stride));
    g_assert_false(vc4_tiling_rgba8888_stride(
        VC4_TILING_FORMAT_T, 0, &stride));
    g_assert_false(vc4_tiling_rgba8888_stride(
        VC4_TILING_FORMAT_T, UINT32_MAX, &stride));
}

static void test_linear(void)
{
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LINEAR, 256, 0, 0), ==, 0);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LINEAR, 256, 1, 0), ==, 4);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LINEAR, 256, 0, 1), ==, 256);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LINEAR, 256, 63, 63),
                     ==, 16380);
}

static void test_lt(void)
{
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LT, 256, 0, 0), ==, 0);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LT, 256, 3, 3), ==, 60);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LT, 256, 4, 0), ==, 64);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LT, 256, 0, 4), ==, 1024);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_LT, 256, 4, 4), ==, 1088);
}

static void test_t_even_row(void)
{
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 0, 0), ==, 0);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 3, 3), ==, 60);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 4, 0), ==, 64);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 0, 4), ==, 256);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 16, 0), ==, 3072);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 0, 16), ==, 1024);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 16, 16), ==, 2048);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 32, 0), ==, 4096);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 48, 0), ==, 7168);
}

static void test_t_odd_row(void)
{
    /* The second 4 KiB tile row reverses tile order and subtile mapping. */
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 0, 32), ==, 14336);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 16, 32), ==, 13312);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 0, 48), ==, 15360);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 32, 32), ==, 10240);
    g_assert_cmpuint(offset(VC4_TILING_FORMAT_T, 256, 48, 32), ==, 9216);
}

static void test_invalid(void)
{
    uint64_t value;

    g_assert_false(vc4_tiling_rgba8888_offset(
        3, 256, 0, 0, &value));
    g_assert_false(vc4_tiling_rgba8888_offset(
        VC4_TILING_FORMAT_T, 192, 0, 0, &value));
    g_assert_false(vc4_tiling_rgba8888_offset(
        VC4_TILING_FORMAT_LINEAR, 256, 64, 0, &value));
    g_assert_false(vc4_tiling_rgba8888_offset(
        VC4_TILING_FORMAT_LT, 256, 64, 0, &value));
    g_assert_false(vc4_tiling_rgba8888_offset(
        VC4_TILING_FORMAT_T, 256, 64, 0, &value));
    g_assert_false(vc4_tiling_rgba8888_offset(
        VC4_TILING_FORMAT_LINEAR, 256, 0, 0, NULL));
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/vc4/tiling/stride", test_stride);
    g_test_add_func("/vc4/tiling/linear", test_linear);
    g_test_add_func("/vc4/tiling/lt", test_lt);
    g_test_add_func("/vc4/tiling/t-even-row", test_t_even_row);
    g_test_add_func("/vc4/tiling/t-odd-row", test_t_odd_row);
    g_test_add_func("/vc4/tiling/invalid", test_invalid);
    return g_test_run();
}
