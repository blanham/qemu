/*
 * QTest testcase for BCM2835 SDHOST transfer lifecycle semantics
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/bswap.h"
#include "libqtest-single.h"

#define GPIO_BASE       0x3f200000u
#define GPFSEL4         (GPIO_BASE + 0x10)
#define GPFSEL5         (GPIO_BASE + 0x14)

#define SDHOST_BASE     0x3f202000u
#define SDCMD           (SDHOST_BASE + 0x00)
#define SDARG           (SDHOST_BASE + 0x04)
#define SDCDIV          (SDHOST_BASE + 0x0c)
#define SDRSP0          (SDHOST_BASE + 0x10)
#define SDHSTS          (SDHOST_BASE + 0x20)
#define SDVDD           (SDHOST_BASE + 0x30)
#define SDEDM           (SDHOST_BASE + 0x34)
#define SDHCFG          (SDHOST_BASE + 0x38)
#define SDHBCT          (SDHOST_BASE + 0x3c)
#define SDDATA          (SDHOST_BASE + 0x40)
#define SDHBLC          (SDHOST_BASE + 0x50)

#define SDCMD_NEW_FLAG      0x8000u
#define SDCMD_FAIL_FLAG     0x4000u
#define SDCMD_BUSYWAIT      0x0800u
#define SDCMD_NO_RESPONSE   0x0400u
#define SDCMD_LONG_RESPONSE 0x0200u
#define SDCMD_READ_CMD      0x0040u

#define SDHSTS_DATA_FLAG    0x0001u
#define SDEDM_FSM_MASK      0x000fu
#define SDEDM_FSM_DATAMODE  0x0001u

#define TEST_IMAGE_SIZE (64 * 1024 * 1024)
#define TEST_SECTORS 8
#define WORDS_PER_SECTOR (512 / sizeof(uint32_t))

static uint32_t expected_word(unsigned sector, unsigned word)
{
    return 0xa5000000u | (sector << 16) | word;
}

static char *create_test_image(void)
{
    g_autoptr(GError) error = NULL;
    uint8_t data[TEST_SECTORS * 512];
    char *path = NULL;
    int fd;
    unsigned sector;
    unsigned word;

    fd = g_file_open_tmp("bcm2835-sdhost-XXXXXX", &path, &error);
    g_assert_no_error(error);
    g_assert_cmpint(fd, >=, 0);
    g_assert_cmpint(ftruncate(fd, TEST_IMAGE_SIZE), ==, 0);

    for (sector = 0; sector < TEST_SECTORS; sector++) {
        for (word = 0; word < WORDS_PER_SECTOR; word++) {
            stl_le_p(data + sector * 512 + word * sizeof(uint32_t),
                     expected_word(sector, word));
        }
    }
    g_assert_cmpint(pwrite(fd, data, sizeof(data), 0),
                    ==, sizeof(data));
    close(fd);
    return path;
}

static void sdhost_command(uint32_t argument, uint32_t command)
{
    uint32_t result;

    writel(SDARG, argument);
    writel(SDCMD, SDCMD_NEW_FLAG | command);
    result = readl(SDCMD);
    g_assert_cmphex(result & SDCMD_NEW_FLAG, ==, 0);
    g_assert_cmphex(result & SDCMD_FAIL_FLAG, ==, 0);
}

static uint32_t initialize_card(void)
{
    uint32_t rca;

    writel(GPFSEL4, 0x24000000);
    writel(GPFSEL5, 0x00000924);

    writel(SDCDIV, 0x148);
    writel(SDHCFG, 0x0a);
    writel(SDVDD, 1);

    sdhost_command(0, SDCMD_NO_RESPONSE | 0);
    sdhost_command(0x155, 8);
    sdhost_command(0, 55);
    sdhost_command(0x40200000, 41);
    sdhost_command(0, SDCMD_LONG_RESPONSE | 2);
    sdhost_command(0, 3);
    rca = readl(SDRSP0) & 0xffff0000u;
    g_assert_cmphex(rca, !=, 0);
    sdhost_command(rca, SDCMD_LONG_RESPONSE | 9);
    sdhost_command(rca, SDCMD_BUSYWAIT | 7);
    sdhost_command(rca, 55);
    sdhost_command(0, 42);
    sdhost_command(512, 16);
    sdhost_command(rca, 55);
    sdhost_command(2, 6);
    return rca;
}

static void read_and_check_sector(unsigned sector)
{
    unsigned word;

    g_assert_cmphex(readl(SDHSTS) & SDHSTS_DATA_FLAG,
                    ==, SDHSTS_DATA_FLAG);
    for (word = 0; word < WORDS_PER_SECTOR; word++) {
        g_assert_cmphex(readl(SDDATA), ==,
                        expected_word(sector, word));
    }
}

static void stop_transfer(void)
{
    sdhost_command(0, SDCMD_BUSYWAIT | 12);
    g_assert_cmphex(readl(SDHSTS) & SDHSTS_DATA_FLAG, ==, 0);
    g_assert_cmphex(readl(SDEDM) & SDEDM_FSM_MASK,
                    ==, SDEDM_FSM_DATAMODE);
    g_assert_cmphex(readl(SDDATA), ==, 0);
}

static void test_transfer_lifecycle(void)
{
    g_autofree char *path = create_test_image();
    g_autofree char *quoted = g_shell_quote(path);
    g_autofree char *args = g_strdup_printf(
        "-M raspi3b -drive file=%s,format=raw,if=sd", quoted);

    qtest_start(args);
    initialize_card();

    /*
     * Stock bootcode inherits the reset HBCT/HBLC values from the
     * boot ROM.  HBLC zero keeps CMD18 open until an explicit CMD12.
     */
    g_assert_cmphex(readl(SDHBCT), ==, 0x400);
    g_assert_cmphex(readl(SDHBLC), ==, 0);

    sdhost_command(0, SDCMD_READ_CMD | 18);
    read_and_check_sector(0);
    stop_transfer();

    /* A new data command must reload the internal transfer count. */
    sdhost_command(512, SDCMD_READ_CMD | 18);
    read_and_check_sector(1);
    stop_transfer();

    /*
     * DATA_FLAG is sticky status, not a live fifo-not-empty level.
     * It survives the final FIFO pop, is W1C, and CMD12 also clears
     * it while returning the controller to data mode.
     */
    writel(SDHBCT, 512);
    writel(SDHBLC, 1);
    sdhost_command(1024, SDCMD_READ_CMD | 18);
    read_and_check_sector(2);
    g_assert_cmphex(readl(SDHSTS) & SDHSTS_DATA_FLAG,
                    ==, SDHSTS_DATA_FLAG);
    g_assert_cmphex(readl(SDEDM) & SDEDM_FSM_MASK,
                    ==, SDEDM_FSM_DATAMODE);
    writel(SDHSTS, SDHSTS_DATA_FLAG);
    g_assert_cmphex(readl(SDHSTS) & SDHSTS_DATA_FLAG, ==, 0);
    stop_transfer();

    /*
     * Make the byte count deliberately tiny.  A zero HBLC must still
     * stream beyond 512 * HBCT bytes; the fifth sector distinguishes
     * open-ended hardware behavior from the old wrapped-count model.
     */
    writel(SDHBCT, 4);
    writel(SDHBLC, 0);
    sdhost_command(3 * 512, SDCMD_READ_CMD | 18);
    for (unsigned sector = 3; sector < TEST_SECTORS; sector++) {
        read_and_check_sector(sector);
    }
    stop_transfer();

    qtest_end();
    g_assert_cmpint(unlink(path), ==, 0);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/bcm2835/sdhost/transfer-lifecycle",
                    test_transfer_lifecycle);
    return g_test_run();
}
