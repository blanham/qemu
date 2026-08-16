/*
 * QTest testcase for the Raspberry Pi 3B onboard USB hub topology
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "libqtest-single.h"

#define DWC2_BASE           0x3f980000
#define DWC2_HPRT0          0x440
#define HPRT0_CONNECTION    (1u << 0)
#define HPRT0_POWER         (1u << 12)
#define HPRT0_SPEED_MASK    (3u << 17)
#define HPRT0_SPEED_HIGH    (0u << 17)

static void test_onboard_hub_is_high_speed(void)
{
    uint32_t hprt0 = readl(DWC2_BASE + DWC2_HPRT0);

    g_assert_cmphex(hprt0 & HPRT0_CONNECTION, !=, 0);
    g_assert_cmphex(hprt0 & HPRT0_POWER, !=, 0);
    g_assert_cmphex(hprt0 & HPRT0_SPEED_MASK, ==, HPRT0_SPEED_HIGH);
}

int main(int argc, char **argv)
{
    int ret;

    g_test_init(&argc, &argv, NULL);
    qtest_add_func("/bcm2835/usb/onboard-hub-high-speed",
                   test_onboard_hub_is_high_speed);

    qtest_start("-M raspi3b-vc4-hetero -m 1G -smp 5 -S");
    ret = g_test_run();
    qtest_end();
    return ret;
}
