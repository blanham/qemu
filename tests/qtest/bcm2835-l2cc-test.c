/*
 * QTest testcase for the BCM2835 VideoCore L2 cache controller
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "libqtest-single.h"

#define L2CC_BASE             0x3fe01000u
#define CONTROL               (L2CC_BASE + 0x000)
#define FLUSH_START           (L2CC_BASE + 0x004)
#define FLUSH_END             (L2CC_BASE + 0x008)
#define ALIAS_EXCEPTION       (L2CC_BASE + 0x080)
#define ALIAS_EXCEPTION_ID    (L2CC_BASE + 0x084)
#define ALIAS_EXCEPTION_ADDR  (L2CC_BASE + 0x088)
#define RD_HITS               (L2CC_BASE + 0x100)
#define RD_MISSES             (L2CC_BASE + 0x104)
#define WR_HITS               (L2CC_BASE + 0x108)
#define WR_MISSES             (L2CC_BASE + 0x10c)
#define WR_BACKS              (L2CC_BASE + 0x110)
#define IN_FLIGHT             (L2CC_BASE + 0x114)
#define STALLS                (L2CC_BASE + 0x11c)
#define TAG_STALLS            (L2CC_BASE + 0x120)
#define SD_STALLS             (L2CC_BASE + 0x124)

static void test_reset_masks_and_flush_completion(void)
{
    g_assert_cmphex(readl(CONTROL), ==, 0);
    g_assert_cmphex(readl(FLUSH_START), ==, 0);
    g_assert_cmphex(readl(FLUSH_END), ==, 0x0fffffe0);
    g_assert_cmphex(readl(ALIAS_EXCEPTION), ==, 0);
    g_assert_cmphex(readl(ALIAS_EXCEPTION_ID), ==, 0);
    g_assert_cmphex(readl(ALIAS_EXCEPTION_ADDR), ==, 0);
    g_assert_cmphex(readl(RD_HITS), ==, 0);
    g_assert_cmphex(readl(RD_MISSES), ==, 0);
    g_assert_cmphex(readl(WR_HITS), ==, 0);
    g_assert_cmphex(readl(WR_MISSES), ==, 0);
    g_assert_cmphex(readl(WR_BACKS), ==, 0);
    g_assert_cmphex(readl(IN_FLIGHT), ==, 0);
    g_assert_cmphex(readl(STALLS), ==, 0);
    g_assert_cmphex(readl(TAG_STALLS), ==, 0);
    g_assert_cmphex(readl(SD_STALLS), ==, 0);

    writel(FLUSH_START, 0xffffffff);
    writel(FLUSH_END, 0x8123457f);
    g_assert_cmphex(readl(FLUSH_START), ==, 0x0fffffe0);
    g_assert_cmphex(readl(FLUSH_END), ==, 0x01234560);

    /* FLUSH completes synchronously; persistent fields survive. */
    writel(CONTROL, 0xffffffff);
    g_assert_cmphex(readl(CONTROL), ==, 0x00ff0c3b);
    writel(CONTROL, 0x00000014);
    g_assert_cmphex(readl(CONTROL), ==, 0x00000010);

    writel(ALIAS_EXCEPTION, 0xdeadbeef);
    g_assert_cmphex(readl(ALIAS_EXCEPTION), ==, 0xdeadbeef);
    writel(RD_HITS, 0x12345678);
    g_assert_cmphex(readl(RD_HITS), ==, 0x12345678);
}

int main(int argc, char **argv)
{
    int ret;

    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/bcm2835/l2cc/reset-masks-flush-completion",
                    test_reset_masks_and_flush_completion);
    qtest_start("-M raspi3b");
    ret = g_test_run();
    qtest_end();
    return ret;
}
