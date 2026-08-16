/*
 * QTest testcase for the BCM2835 VideoCore L1 cache controller
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "libqtest-single.h"

#define L1CC_BASE           0x3fe02000u
#define IC0_CONTROL         (L1CC_BASE + 0x000)
#define IC0_PRIORITY        (L1CC_BASE + 0x004)
#define IC0_FLUSH_START     (L1CC_BASE + 0x008)
#define IC0_FLUSH_END       (L1CC_BASE + 0x00c)
#define IC1_CONTROL         (L1CC_BASE + 0x080)
#define IC1_PRIORITY        (L1CC_BASE + 0x084)
#define IC1_FLUSH_START     (L1CC_BASE + 0x088)
#define IC1_FLUSH_END       (L1CC_BASE + 0x08c)
#define D_CONTROL           (L1CC_BASE + 0x100)
#define D_FLUSH_START       (L1CC_BASE + 0x104)
#define D_FLUSH_END         (L1CC_BASE + 0x108)
#define D_PRIORITY          (L1CC_BASE + 0x10c)

static void test_reset_and_flush_completion(void)
{
    g_assert_cmphex(readl(IC0_CONTROL), ==, 0);
    g_assert_cmphex(readl(IC0_PRIORITY), ==, 0x34af);
    g_assert_cmphex(readl(IC0_FLUSH_START), ==, 0);
    g_assert_cmphex(readl(IC0_FLUSH_END), ==, 0xffffffff);
    g_assert_cmphex(readl(IC1_CONTROL), ==, 0);
    g_assert_cmphex(readl(IC1_PRIORITY), ==, 0x34af);
    g_assert_cmphex(readl(IC1_FLUSH_START), ==, 0);
    g_assert_cmphex(readl(IC1_FLUSH_END), ==, 0xffffffff);
    g_assert_cmphex(readl(D_CONTROL), ==, 0);
    g_assert_cmphex(readl(D_FLUSH_START), ==, 0);
    g_assert_cmphex(readl(D_FLUSH_END), ==, 0x3fffffff);
    g_assert_cmphex(readl(D_PRIORITY), ==, 0);

    writel(IC0_FLUSH_START, 0x1234567f);
    writel(IC0_FLUSH_END, 0xfedcba9f);
    g_assert_cmphex(readl(IC0_FLUSH_START), ==, 0x12345660);
    g_assert_cmphex(readl(IC0_FLUSH_END), ==, 0xfedcba80);

    /* START_FLUSH completes synchronously, other bits persist. */
    writel(IC0_CONTROL, 0x7f);
    g_assert_cmphex(readl(IC0_CONTROL), ==, 0x7d);
    writel(IC1_CONTROL, 0x03);
    g_assert_cmphex(readl(IC1_CONTROL), ==, 0x01);

    writel(IC1_PRIORITY, 0xdeadbeef);
    g_assert_cmphex(readl(IC1_PRIORITY), ==, 0xbeef);

    writel(D_FLUSH_START, 0xffffffff);
    writel(D_FLUSH_END, 0x81234567);
    g_assert_cmphex(readl(D_FLUSH_START), ==, 0x3fffffe0);
    g_assert_cmphex(readl(D_FLUSH_END), ==, 0x01234560);

    /* Both data-cache flush command bits self-clear. */
    writel(D_CONTROL, 0x0f);
    g_assert_cmphex(readl(D_CONTROL), ==, 0x09);
    writel(D_PRIORITY, 0xffffffff);
    g_assert_cmphex(readl(D_PRIORITY), ==, 0x0fff0fff);
}

int main(int argc, char **argv)
{
    int ret;

    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/bcm2835/l1cc/reset-and-flush-completion",
                    test_reset_and_flush_completion);
    qtest_start("-M raspi3b");
    ret = g_test_run();
    qtest_end();
    return ret;
}
