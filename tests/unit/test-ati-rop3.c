/*
 * ATI ROP3 truth-table tests
 *
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#include "qemu/osdep.h"
#include "hw/display/ati_rop3.h"

static uint32_t rop3_reference(uint8_t rop, uint32_t pat,
                               uint32_t src, uint32_t dst)
{
    uint32_t result = 0;

    for (unsigned int bit = 0; bit < 32; bit++) {
        unsigned int index = (((pat >> bit) & 1) << 2) |
                             (((src >> bit) & 1) << 1) |
                             ((dst >> bit) & 1);
        result |= ((uint32_t)((rop >> index) & 1)) << bit;
    }
    return result;
}

static bool reference_uses_pattern(uint8_t rop)
{
    for (unsigned int src = 0; src < 2; src++) {
        for (unsigned int dst = 0; dst < 2; dst++) {
            if (rop3_reference(rop, 0, src, dst) !=
                rop3_reference(rop, 1, src, dst)) {
                return true;
            }
        }
    }
    return false;
}

static bool reference_uses_source(uint8_t rop)
{
    for (unsigned int pat = 0; pat < 2; pat++) {
        for (unsigned int dst = 0; dst < 2; dst++) {
            if (rop3_reference(rop, pat, 0, dst) !=
                rop3_reference(rop, pat, 1, dst)) {
                return true;
            }
        }
    }
    return false;
}

static bool reference_uses_destination(uint8_t rop)
{
    for (unsigned int pat = 0; pat < 2; pat++) {
        for (unsigned int src = 0; src < 2; src++) {
            if (rop3_reference(rop, pat, src, 0) !=
                rop3_reference(rop, pat, src, 1)) {
                return true;
            }
        }
    }
    return false;
}

static void test_named_rops(void)
{
    const uint32_t pat = 0x12345678;
    const uint32_t src = 0x89abcdef;
    const uint32_t dst = 0x55aa00ff;

    g_assert_cmphex(ati_rop3_eval(0x00, pat, src, dst), ==, 0);
    g_assert_cmphex(ati_rop3_eval(0xff, pat, src, dst), ==, UINT32_MAX);
    g_assert_cmphex(ati_rop3_eval(0xcc, pat, src, dst), ==, src);
    g_assert_cmphex(ati_rop3_eval(0xf0, pat, src, dst), ==, pat);
    g_assert_cmphex(ati_rop3_eval(0xaa, pat, src, dst), ==, dst);
    g_assert_cmphex(ati_rop3_eval(0x66, pat, src, dst), ==, src ^ dst);
    g_assert_cmphex(ati_rop3_eval(0x5a, pat, src, dst), ==, pat ^ dst);
    g_assert_cmphex(ati_rop3_apply_mask(0xf0, pat, src, dst, 0x00ff00ff),
                    ==, (pat & 0x00ff00ff) | (dst & 0xff00ff00));
}

static void test_all_truth_tables(void)
{
    static const uint32_t values[][3] = {
        { 0, 0, 0 },
        { UINT32_MAX, UINT32_MAX, UINT32_MAX },
        { 0x01234567, 0x89abcdef, 0x55aa00ff },
        { 0xf0f0f0f0, 0xcccccccc, 0xaaaaaaaa },
    };

    for (unsigned int rop = 0; rop < 256; rop++) {
        for (unsigned int i = 0; i < G_N_ELEMENTS(values); i++) {
            uint32_t expected = rop3_reference(rop, values[i][0],
                                              values[i][1], values[i][2]);
            g_assert_cmphex(ati_rop3_eval(rop, values[i][0], values[i][1],
                                         values[i][2]), ==, expected);
        }
    }
}

static void test_dependencies(void)
{
    for (unsigned int rop = 0; rop < 256; rop++) {
        g_assert_cmpint(ati_rop3_uses_pattern(rop), ==,
                        reference_uses_pattern(rop));
        g_assert_cmpint(ati_rop3_uses_source(rop), ==,
                        reference_uses_source(rop));
        g_assert_cmpint(ati_rop3_uses_destination(rop), ==,
                        reference_uses_destination(rop));
    }
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/ati/rop3/named", test_named_rops);
    g_test_add_func("/ati/rop3/all-truth-tables", test_all_truth_tables);
    g_test_add_func("/ati/rop3/dependencies", test_dependencies);
    return g_test_run();
}
