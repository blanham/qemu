/*
 * QTest testcase for the BCM2835 DBUS control register
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "libqtest-single.h"

#define DBUS_CONTROL  0x3f900100u
#define DBUS_PASSWORD 0x5a000000u

static void assert_control(uint32_t expected)
{
    g_assert_cmphex(readl(DBUS_CONTROL), ==, expected);
}

static void test_control_latch(void)
{
    assert_control(0);

    writel(DBUS_CONTROL, 0x00000002);
    assert_control(0);

    writel(DBUS_CONTROL, 0xa5000002);
    assert_control(0);

    writel(DBUS_CONTROL, DBUS_PASSWORD | 0x00123456);
    assert_control(0x00123456);

    writel(DBUS_CONTROL, 0x00000007);
    assert_control(0x00123456);

    writel(DBUS_CONTROL, DBUS_PASSWORD | 0x00ffffff);
    assert_control(0x00ffffff);

    writel(DBUS_CONTROL, DBUS_PASSWORD);
    assert_control(0);
}

int main(int argc, char **argv)
{
    int ret;

    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/bcm2835/dbus/control-latch",
                    test_control_latch);

    qtest_start("-M raspi3b");
    ret = g_test_run();
    qtest_end();
    return ret;
}
