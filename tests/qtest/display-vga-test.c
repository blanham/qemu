/*
 * QTest testcase for VGA cards
 *
 * Copyright (c) 2014 Red Hat, Inc
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the terms of the GNU GPL, version 2 or later.
 * See the COPYING file in the top-level directory.
 */

#include "qemu/osdep.h"
#include "libqtest.h"
#include "libqos/pci.h"
#include "libqos/pci-pc.h"
#include "hw/pci/pci_regs.h"
#include "hw/pci/pci_ids.h"
#include "qemu/units.h"

#define ATI_VENDOR_ID                       0x1002
#define ATI_RAGE128_PF_DEVICE_ID            0x5046
#define ATI_RAGE128_RE_DEVICE_ID            0x5245
#define ATI_RAGE128_PF_SUBSYSTEM_ID_DEFAULT 0x0018
#define ATI_RAGE128_RE_SUBSYSTEM_ID_DEFAULT 0x0008
#define ATI_RAGE128_RE_SUBSYSTEM_ID_AI_WONDER 0x0068
#define R128_AGP_CAP_OFFSET                 0x50
#define R128_PF_PM_CAP_OFFSET               0x5c
#define R128_RE_PM_CAP_OFFSET               0x50
#define R128_AGP_STATUS_VALUE               0x1f000207
#define R128_AGP_COMMAND_RESET              0x00000200
#define R128_AGP_COMMAND_MASK               0x1f000307
#define R128_PM_CAPABILITIES                \
    (PCI_PM_CAP_VER_1_1 | PCI_PM_CAP_D1)

#define R128_MM_INDEX                       0x0000
#define R128_MM_DATA                        0x0004
#define R128_GPIO_MONID                     0x0068
#define R128_GPIO_MONID_Y_1                 (1U << 9)
#define R128_GPIO_MONID_Y_2                 (1U << 10)
#define R128_GPIO_MONID_EN_1                (1U << 17)
#define R128_GPIO_MONID_EN_2                (1U << 18)
#define R128_GPIO_MONID_MASK_1              (1U << 25)
#define R128_GPIO_MONID_MASK_2              (1U << 26)
#define R128_GEN_RESET_CNTL                 0x00f0
#define R128_CNFG_MEMSIZE                   0x00f8
#define R128_PC_NGUI_CTLSTAT                0x0184
#define R128_DST_OFFSET                     0x1404
#define R128_DST_PITCH                      0x1408
#define R128_SRC_X                          0x1414
#define R128_SRC_Y                          0x1418
#define R128_DST_X                          0x141c
#define R128_DST_Y                          0x1420
#define R128_DST_HEIGHT_WIDTH               0x143c
#define R128_DST_Y_X                        0x1438
#define R128_DP_GUI_MASTER_CNTL             0x146c
#define R128_DP_BRUSH_FRGD_CLR              0x147c
#define R128_SRC_OFFSET                     0x15ac
#define R128_SRC_PITCH                      0x15b0
#define R128_DP_SRC_FRGD_CLR                0x15d8
#define R128_DP_SRC_BKGD_CLR                0x15dc
#define R128_DST_BRES_ERR                    0x1628
#define R128_DST_BRES_INC                    0x162c
#define R128_DST_BRES_DEC                    0x1630
#define R128_DST_BRES_LNTH                   0x1634
#define R128_DP_CNTL                        0x16c0
#define R128_DP_WRITE_MASK                  0x16cc
#define R128_DP_CNTL_XDIR_YDIR_YMAJOR       0x16d0
#define R128_SC_TOP_LEFT                    0x16ec
#define R128_SC_BOTTOM_RIGHT                0x16f0
#define R128_GUI_STAT                       0x1740
#define R128_HOST_DATA_LAST                 0x17e0

#define R128_GMC_SRC_PITCH_OFFSET_CNTL      0x00000001
#define R128_GMC_DST_PITCH_OFFSET_CNTL      0x00000002
#define R128_GMC_DST_CLIPPING               0x00000008
#define R128_GMC_BRUSH_SOLIDCOLOR           0x000000d0
#define R128_GMC_BRUSH_NONE                 0x000000f0
#define R128_GMC_DST_8BPP                   0x00000200
#define R128_GMC_DST_24BPP                  0x00000500
#define R128_GMC_SRC_DATATYPE_MONO_FG_BG    0x00000000
#define R128_GMC_SRC_DATATYPE_MONO_FG       0x00001000
#define R128_GMC_SRC_DATATYPE_COLOR         0x00003000
#define R128_GMC_BYTE_LSB_TO_MSB            0x00004000
#define R128_ROP3_SRCCOPY                    0x00cc0000
#define R128_ROP3_PATCOPY                    0x00f00000
#define R128_DP_SRC_SOURCE_MEMORY            0x02000000
#define R128_DP_SRC_SOURCE_HOST_DATA         0x03000000
#define R128_DST_X_LEFT_TO_RIGHT             0x00000001
#define R128_DST_Y_TOP_TO_BOTTOM             0x00000002
#define R128_SOFT_RESET_GUI                  0x00000001
#define R128_PC_FLUSH_ALL                    0x000000ff
#define R128_PC_BUSY                         0x80000000
#define R128_GUI_ACTIVE                      0x80000000
#define R128_LINE_Y_MAJOR                    0x00000004
#define R128_LINE_Y_TOP_TO_BOTTOM            0x00008000
#define R128_LINE_X_LEFT_TO_RIGHT            0x80000000

#define R128_EXPLICIT_SURFACES \
    (R128_GMC_SRC_PITCH_OFFSET_CNTL | R128_GMC_DST_PITCH_OFFSET_CNTL)
#define R128_COMMON_MASTER \
    (R128_EXPLICIT_SURFACES | R128_GMC_DST_CLIPPING)

typedef struct Rage128Test {
    QTestState *qts;
    QPCIBus *bus;
    QPCIDevice *dev;
    QPCIBar framebuffer;
    QPCIBar io;
    QPCIBar mmio;
    uint64_t framebuffer_size;
    uint64_t io_size;
    uint64_t mmio_size;
} Rage128Test;

static void pci_multihead(void)
{
    QTestState *qts;

    qts = qtest_init("-vga none -device VGA -device secondary-vga");
    qtest_quit(qts);
}

static void test_vga(gconstpointer data)
{
    QTestState *qts;

    qts = qtest_initf("-vga none -device %s", (const char *)data);
    qtest_quit(qts);
}

static bool test_arch_is_x86(void)
{
    const char *arch = qtest_get_arch();

    return !strcmp(arch, "i386") || !strcmp(arch, "x86_64");
}

static Rage128Test *rage128_test_start_model(const char *model,
                                                 const char *extra,
                                                 uint16_t device_id)
{
    Rage128Test *test = g_new0(Rage128Test, 1);
    uint32_t bar0;
    uint32_t bar1;
    uint32_t bar2;

    test->qts = qtest_initf("-machine pc -vga none "
                                "-device ati-vga,model=%s%s,addr=04.0",
                                model, extra);
    test->bus = qpci_new_pc(test->qts, NULL);
    test->dev = qpci_device_find(test->bus, QPCI_DEVFN(0x4, 0x0));
    g_assert_nonnull(test->dev);

    g_assert_cmphex(qpci_config_readw(test->dev, PCI_VENDOR_ID), ==,
                    ATI_VENDOR_ID);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_DEVICE_ID), ==,
                        device_id);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_CLASS_DEVICE), ==,
                    PCI_CLASS_DISPLAY_VGA);

    bar0 = qpci_config_readl(test->dev, PCI_BASE_ADDRESS_0);
    bar1 = qpci_config_readl(test->dev, PCI_BASE_ADDRESS_1);
    bar2 = qpci_config_readl(test->dev, PCI_BASE_ADDRESS_2);
    g_assert_cmphex(bar0 & (PCI_BASE_ADDRESS_SPACE |
                            PCI_BASE_ADDRESS_MEM_PREFETCH), ==,
                    PCI_BASE_ADDRESS_MEM_PREFETCH);
    g_assert_cmphex(bar1 & PCI_BASE_ADDRESS_SPACE, ==,
                    PCI_BASE_ADDRESS_SPACE_IO);
    g_assert_cmphex(bar2 & PCI_BASE_ADDRESS_SPACE, ==,
                    PCI_BASE_ADDRESS_SPACE_MEMORY);

    qpci_device_enable(test->dev);
    test->framebuffer = qpci_iomap(test->dev, 0, &test->framebuffer_size);
    test->io = qpci_iomap(test->dev, 1, &test->io_size);
    test->mmio = qpci_iomap(test->dev, 2, &test->mmio_size);

    g_assert_cmpuint(test->framebuffer_size, ==, 64 * MiB);
    g_assert_cmpuint(test->io_size, ==, 0x100);
    g_assert_cmpuint(test->mmio_size, ==, 0x4000);
    return test;
}

static Rage128Test *rage128_test_start(void)
{
    return rage128_test_start_model("rage128p", "",
                                    ATI_RAGE128_PF_DEVICE_ID);
}

static void rage128_test_stop(Rage128Test *test)
{
    qpci_iounmap(test->dev, test->mmio);
    qpci_iounmap(test->dev, test->io);
    qpci_iounmap(test->dev, test->framebuffer);
    g_free(test->dev);
    qpci_free_pc(test->bus);
    qtest_quit(test->qts);
    g_free(test);
}

static void rage128_write(Rage128Test *test, uint32_t reg, uint32_t value)
{
    qpci_io_writel(test->dev, test->mmio, reg, value);
}

static uint32_t rage128_read(Rage128Test *test, uint32_t reg)
{
    return qpci_io_readl(test->dev, test->mmio, reg);
}

static void rage128_ddc_set_lines(Rage128Test *test,
                                  bool clock, bool data)
{
    uint32_t value = R128_GPIO_MONID_MASK_1 | R128_GPIO_MONID_MASK_2;

    /* Rage 128 DDC is open-drain: EN plus a zero A bit drives low. */
    if (!clock) {
        value |= R128_GPIO_MONID_EN_2;
    }
    if (!data) {
        value |= R128_GPIO_MONID_EN_1;
    }
    rage128_write(test, R128_GPIO_MONID, value);
}

static bool rage128_ddc_get_data(Rage128Test *test)
{
    return !!(rage128_read(test, R128_GPIO_MONID) &
              R128_GPIO_MONID_Y_1);
}

static void rage128_ddc_start(Rage128Test *test)
{
    rage128_ddc_set_lines(test, true, true);
    rage128_ddc_set_lines(test, true, false);
    rage128_ddc_set_lines(test, false, false);
}

static void rage128_ddc_stop(Rage128Test *test)
{
    rage128_ddc_set_lines(test, false, false);
    rage128_ddc_set_lines(test, true, false);
    rage128_ddc_set_lines(test, true, true);
}

static bool rage128_ddc_write_byte(Rage128Test *test, uint8_t value)
{
    bool acknowledged;

    for (uint8_t mask = 0x80; mask; mask >>= 1) {
        bool bit = !!(value & mask);

        rage128_ddc_set_lines(test, false, bit);
        rage128_ddc_set_lines(test, true, bit);
        rage128_ddc_set_lines(test, false, bit);
    }

    rage128_ddc_set_lines(test, false, true);
    rage128_ddc_set_lines(test, true, true);
    acknowledged = !rage128_ddc_get_data(test);
    rage128_ddc_set_lines(test, false, true);
    return acknowledged;
}

static uint8_t rage128_ddc_read_byte(Rage128Test *test, bool acknowledge)
{
    uint8_t value = 0;

    for (unsigned int bit = 0; bit < 8; bit++) {
        rage128_ddc_set_lines(test, false, true);
        rage128_ddc_set_lines(test, true, true);
        value = (value << 1) | rage128_ddc_get_data(test);
        rage128_ddc_set_lines(test, false, true);
    }

    rage128_ddc_set_lines(test, false, !acknowledge);
    rage128_ddc_set_lines(test, true, !acknowledge);
    rage128_ddc_set_lines(test, false, !acknowledge);
    rage128_ddc_set_lines(test, false, true);
    return value;
}

static void rage128_ddc_read_edid(Rage128Test *test, uint8_t edid[128])
{
    rage128_ddc_start(test);
    g_assert_true(rage128_ddc_write_byte(test, 0xa0));
    g_assert_true(rage128_ddc_write_byte(test, 0x00));

    /* Repeated start selects the EEPROM read address. */
    rage128_ddc_start(test);
    g_assert_true(rage128_ddc_write_byte(test, 0xa1));
    for (unsigned int i = 0; i < 128; i++) {
        edid[i] = rage128_ddc_read_byte(test, i != 127);
    }
    rage128_ddc_stop(test);
}

static void rage128_ddc_check_edid(const uint8_t edid[128])
{
    static const uint8_t header[8] = {
        0x00, 0xff, 0xff, 0xff, 0xff, 0xff, 0xff, 0x00,
    };
    uint8_t checksum = 0;

    g_assert_cmpmem(edid, sizeof(header), header, sizeof(header));
    for (unsigned int i = 0; i < 128; i++) {
        checksum += edid[i];
    }
    g_assert_cmpuint(checksum, ==, 0);
    g_assert_cmpuint(edid[18], ==, 1); /* EDID major version */
}

static void test_rage128_ddc(void)
{
    uint8_t edid[128];
    Rage128Test *test = rage128_test_start();

    rage128_ddc_read_edid(test, edid);
    rage128_ddc_check_edid(edid);
    rage128_test_stop(test);

    /* The PCI RE profile uses the same physical MONID DDC pins. */
    test = rage128_test_start_model("rage128re", "",
                                    ATI_RAGE128_RE_DEVICE_ID);
    rage128_ddc_read_edid(test, edid);
    rage128_ddc_check_edid(edid);
    rage128_test_stop(test);
}

static void rage128_set_surface(Rage128Test *test, uint32_t pitch)
{
    rage128_write(test, R128_DST_OFFSET, 0);
    rage128_write(test, R128_SRC_OFFSET, 0);
    rage128_write(test, R128_DST_PITCH, pitch);
    rage128_write(test, R128_SRC_PITCH, pitch);
    rage128_write(test, R128_SC_TOP_LEFT, 0);
    rage128_write(test, R128_SC_BOTTOM_RIGHT, 0x3fff3fff);
    rage128_write(test, R128_DP_CNTL,
                  R128_DST_X_LEFT_TO_RIGHT | R128_DST_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DP_WRITE_MASK, UINT32_MAX);
}

static void test_rage128_pci_and_apertures(void)
{
    Rage128Test *test = rage128_test_start();
    uint32_t pattern = 0x5aa55aa5;
    uint32_t replacement = 0xa55aa55a;
    uint32_t rom_mask;
    uint16_t status;
    uint8_t agp;
    uint8_t pm;

    g_assert_cmphex(qpci_config_readw(test->dev,
                                     PCI_SUBSYSTEM_VENDOR_ID), ==,
                    ATI_VENDOR_ID);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_SUBSYSTEM_ID), ==,
                    ATI_RAGE128_PF_SUBSYSTEM_ID_DEFAULT);
    g_assert_cmpuint(qpci_config_readb(test->dev, PCI_MIN_GNT), ==, 8);

    qpci_config_writel(test->dev, PCI_ROM_ADDRESS, UINT32_MAX);
    rom_mask = qpci_config_readl(test->dev, PCI_ROM_ADDRESS);
    g_assert_cmphex(rom_mask & PCI_ROM_ADDRESS_MASK, ==, 0xfffe0000);
    qpci_config_writel(test->dev, PCI_ROM_ADDRESS, 0);

    status = qpci_config_readw(test->dev, PCI_STATUS);
    g_assert_cmphex(status & (PCI_STATUS_CAP_LIST | PCI_STATUS_66MHZ |
                             PCI_STATUS_FAST_BACK), ==,
                    PCI_STATUS_CAP_LIST | PCI_STATUS_66MHZ |
                    PCI_STATUS_FAST_BACK);
    g_assert_cmphex(status & PCI_STATUS_DEVSEL_MASK, ==,
                    PCI_STATUS_DEVSEL_MEDIUM);

    qpci_config_writeb(test->dev, PCI_CACHE_LINE_SIZE, 8);
    qpci_config_writeb(test->dev, PCI_LATENCY_TIMER, 32);
    g_assert_cmpuint(qpci_config_readb(test->dev,
                                      PCI_CACHE_LINE_SIZE), ==, 8);
    g_assert_cmpuint(qpci_config_readb(test->dev,
                                      PCI_LATENCY_TIMER), ==, 32);

    agp = qpci_find_capability(test->dev, PCI_CAP_ID_AGP, 0);
    pm = qpci_find_capability(test->dev, PCI_CAP_ID_PM, 0);
    g_assert_cmpuint(agp, ==, R128_AGP_CAP_OFFSET);
    g_assert_cmpuint(pm, ==, R128_PF_PM_CAP_OFFSET);
    g_assert_cmpuint(qpci_config_readb(test->dev,
                                      agp + PCI_CAP_LIST_NEXT), ==, pm);
    g_assert_cmpuint(qpci_config_readb(test->dev,
                                      pm + PCI_CAP_LIST_NEXT), ==, 0);
    g_assert_cmpuint(qpci_config_readb(test->dev,
                                      agp + PCI_AGP_VERSION), ==, 0x20);
    g_assert_cmphex(qpci_config_readl(test->dev,
                                     agp + PCI_AGP_STATUS), ==,
                    R128_AGP_STATUS_VALUE);
    g_assert_cmphex(qpci_config_readl(test->dev,
                                     agp + PCI_AGP_COMMAND), ==,
                    R128_AGP_COMMAND_RESET);
    qpci_config_writel(test->dev, agp + PCI_AGP_COMMAND, UINT32_MAX);
    g_assert_cmphex(qpci_config_readl(test->dev,
                                     agp + PCI_AGP_COMMAND), ==,
                    R128_AGP_COMMAND_MASK);
    qpci_config_writel(test->dev, agp + PCI_AGP_COMMAND,
                       R128_AGP_COMMAND_RESET);
    g_assert_cmphex(qpci_config_readw(test->dev,
                                     pm + PCI_PM_PMC), ==,
                    R128_PM_CAPABILITIES);

    g_assert_cmphex(rage128_read(test, R128_CNFG_MEMSIZE), ==, 16 * MiB);
    g_assert_cmpuint(rage128_read(test, R128_GUI_STAT), ==, 64);

    qpci_io_writel(test->dev, test->io, R128_MM_INDEX,
                   R128_CNFG_MEMSIZE);
    g_assert_cmphex(qpci_io_readl(test->dev, test->io, R128_MM_DATA), ==,
                    16 * MiB);

    qpci_io_writel(test->dev, test->framebuffer, 0x1234, pattern);
    g_assert_cmphex(qpci_io_readl(test->dev, test->framebuffer, 0x1234),
                    ==, pattern);

    qpci_config_writew(test->dev, pm + PCI_PM_CTRL, 1);
    g_assert_cmphex(qpci_config_readw(test->dev, pm + PCI_PM_CTRL) &
                    PCI_PM_CTRL_STATE_MASK, ==, 1);
    qpci_io_writel(test->dev, test->framebuffer, 0x1234, replacement);
    qpci_config_writew(test->dev, pm + PCI_PM_CTRL, 0);
    g_assert_cmphex(qpci_io_readl(test->dev, test->framebuffer, 0x1234),
                    ==, pattern);

    qpci_config_writew(test->dev, pm + PCI_PM_CTRL, 2);
    g_assert_cmphex(qpci_config_readw(test->dev, pm + PCI_PM_CTRL) &
                    PCI_PM_CTRL_STATE_MASK, ==, 0);

    qpci_config_writew(test->dev, pm + PCI_PM_CTRL, 3);
    g_assert_cmphex(qpci_config_readw(test->dev, pm + PCI_PM_CTRL) &
                    PCI_PM_CTRL_STATE_MASK, ==, 3);
    qpci_io_writel(test->dev, test->framebuffer, 0x1234, replacement);
    qpci_config_writew(test->dev, pm + PCI_PM_CTRL, 0);
    g_assert_cmphex(qpci_io_readl(test->dev, test->framebuffer, 0x1234),
                    ==, pattern);

    rage128_test_stop(test);
}

static void test_rage128_re_pci_profile(void)
{
    Rage128Test *test;
    uint8_t pm;
    uint32_t pattern = 0x1285245a;

    test = rage128_test_start_model("rage128re", "",
                                    ATI_RAGE128_RE_DEVICE_ID);
    g_assert_cmphex(qpci_config_readw(test->dev,
                                     PCI_SUBSYSTEM_VENDOR_ID), ==,
                    ATI_VENDOR_ID);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_SUBSYSTEM_ID), ==,
                    ATI_RAGE128_RE_SUBSYSTEM_ID_DEFAULT);
    g_assert_cmpuint(qpci_find_capability(test->dev, PCI_CAP_ID_AGP, 0),
                     ==, 0);
    pm = qpci_find_capability(test->dev, PCI_CAP_ID_PM, 0);
    g_assert_cmpuint(pm, ==, R128_RE_PM_CAP_OFFSET);
    g_assert_cmphex(qpci_config_readw(test->dev, pm + PCI_PM_PMC), ==,
                    R128_PM_CAPABILITIES);
    qpci_io_writel(test->dev, test->framebuffer, 0x2000, pattern);
    g_assert_cmphex(qpci_io_readl(test->dev, test->framebuffer, 0x2000),
                    ==, pattern);
    rage128_test_stop(test);

    test = rage128_test_start_model(
        "rage128re", ",x-subsystem-id=0x0068",
        ATI_RAGE128_RE_DEVICE_ID);
    g_assert_cmphex(qpci_config_readw(test->dev, PCI_SUBSYSTEM_ID), ==,
                    ATI_RAGE128_RE_SUBSYSTEM_ID_AI_WONDER);
    rage128_test_stop(test);
}

static void test_rage128_engine_control(void)
{
    Rage128Test *test = rage128_test_start();
    uint8_t framebuffer[8];

    g_assert_cmphex(rage128_read(test, R128_GUI_STAT) &
                    R128_GUI_ACTIVE, ==, 0);
    g_assert_cmpuint(rage128_read(test, R128_GUI_STAT) & 0x0fff,
                     ==, 64);

    rage128_write(test, R128_PC_NGUI_CTLSTAT, R128_PC_FLUSH_ALL);
    g_assert_cmphex(rage128_read(test, R128_PC_NGUI_CTLSTAT) &
                    R128_PC_BUSY, ==, 0);
    g_assert_cmphex(rage128_read(test, R128_PC_NGUI_CTLSTAT) &
                    R128_PC_FLUSH_ALL, ==, R128_PC_FLUSH_ALL);

    memset(framebuffer, 0x5a, sizeof(framebuffer));
    qpci_memwrite(test->dev, test->framebuffer, 0,
                  framebuffer, sizeof(framebuffer));

    /* Start a host upload, then prove GUI reset aborts it. */
    rage128_set_surface(test, 4);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_NONE |
                  R128_GMC_DST_8BPP |
                  R128_GMC_SRC_DATATYPE_MONO_FG_BG |
                  R128_GMC_BYTE_LSB_TO_MSB | R128_ROP3_SRCCOPY |
                  R128_DP_SRC_SOURCE_HOST_DATA);
    rage128_write(test, R128_DP_SRC_FRGD_CLR, 0xee);
    rage128_write(test, R128_DP_SRC_BKGD_CLR, 0x11);
    rage128_write(test, R128_DST_X, 0);
    rage128_write(test, R128_DST_Y, 0);
    rage128_write(test, R128_DST_HEIGHT_WIDTH, (1U << 16) | 8U);

    rage128_write(test, R128_GEN_RESET_CNTL, R128_SOFT_RESET_GUI);
    g_assert_cmphex(rage128_read(test, R128_GEN_RESET_CNTL), ==,
                    R128_SOFT_RESET_GUI);
    g_assert_cmpuint(rage128_read(test, R128_DST_PITCH), ==, 0);
    rage128_write(test, R128_GEN_RESET_CNTL, 0);
    g_assert_cmpuint(rage128_read(test, R128_GEN_RESET_CNTL), ==, 0);

    rage128_write(test, R128_HOST_DATA_LAST, UINT32_MAX);
    qpci_memread(test->dev, test->framebuffer, 0,
                 framebuffer, sizeof(framebuffer));
    for (size_t i = 0; i < sizeof(framebuffer); i++) {
        g_assert_cmpuint(framebuffer[i], ==, 0x5a);
    }

    rage128_test_stop(test);
}

static void test_rage128_lines(void)
{
    Rage128Test *test = rage128_test_start();
    uint8_t fb[32 * 12];

    memset(fb, 0x11, sizeof(fb));
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, sizeof(fb));
    rage128_set_surface(test, 4); /* 32-byte 8-bpp stride */
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_SOLIDCOLOR |
                  R128_GMC_DST_8BPP | R128_GMC_SRC_DATATYPE_COLOR |
                  R128_ROP3_PATCOPY);
    rage128_write(test, R128_DP_BRUSH_FRGD_CLR, 0x7d);

    /* Horizontal, positive direction. */
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR,
                  R128_LINE_X_LEFT_TO_RIGHT |
                  R128_LINE_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DST_Y_X, (3U << 16) | 2U);
    rage128_write(test, R128_DST_BRES_ERR, UINT32_MAX);
    rage128_write(test, R128_DST_BRES_INC, 0);
    rage128_write(test, R128_DST_BRES_DEC, 0);
    rage128_write(test, R128_DST_BRES_LNTH, 6);

    /* Vertical, positive direction. */
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR,
                  R128_LINE_Y_MAJOR | R128_LINE_X_LEFT_TO_RIGHT |
                  R128_LINE_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DST_Y_X, (1U << 16) | 12U);
    rage128_write(test, R128_DST_BRES_ERR, UINT32_MAX);
    rage128_write(test, R128_DST_BRES_INC, 0);
    rage128_write(test, R128_DST_BRES_DEC, 0);
    rage128_write(test, R128_DST_BRES_LNTH, 5);

    /* Exercise both Bresenham error branches with driver-style terms. */
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR,
                  R128_LINE_X_LEFT_TO_RIGHT |
                  R128_LINE_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DST_Y_X, (7U << 16) | 1U);
    rage128_write(test, R128_DST_BRES_ERR, 1);
    rage128_write(test, R128_DST_BRES_INC, 2);
    rage128_write(test, R128_DST_BRES_DEC, -4);
    rage128_write(test, R128_DST_BRES_LNTH, 5);

    /* Reverse both axes. */
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR, 0);
    rage128_write(test, R128_DST_Y_X, (10U << 16) | 20U);
    rage128_write(test, R128_DST_BRES_ERR, 0);
    rage128_write(test, R128_DST_BRES_INC, 0);
    rage128_write(test, R128_DST_BRES_DEC, 0);
    rage128_write(test, R128_DST_BRES_LNTH, 4);

    /* Destination clipping applies without changing line stepping. */
    rage128_write(test, R128_SC_TOP_LEFT, (11U << 16) | 15U);
    rage128_write(test, R128_SC_BOTTOM_RIGHT, (11U << 16) | 17U);
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR,
                  R128_LINE_X_LEFT_TO_RIGHT |
                  R128_LINE_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DST_Y_X, (11U << 16) | 12U);
    rage128_write(test, R128_DST_BRES_ERR, UINT32_MAX);
    rage128_write(test, R128_DST_BRES_INC, 0);
    rage128_write(test, R128_DST_BRES_DEC, 0);
    rage128_write(test, R128_DST_BRES_LNTH, 8);

    qpci_memread(test->dev, test->framebuffer, 0, fb, sizeof(fb));
    for (unsigned int i = 0; i < 6; i++) {
        g_assert_cmpuint(fb[3 * 32 + 2 + i], ==, 0x7d);
    }
    for (unsigned int i = 0; i < 5; i++) {
        g_assert_cmpuint(fb[(1 + i) * 32 + 12], ==, 0x7d);
    }
    g_assert_cmpuint(fb[7 * 32 + 1], ==, 0x7d);
    g_assert_cmpuint(fb[8 * 32 + 2], ==, 0x7d);
    g_assert_cmpuint(fb[8 * 32 + 3], ==, 0x7d);
    g_assert_cmpuint(fb[8 * 32 + 4], ==, 0x7d);
    g_assert_cmpuint(fb[9 * 32 + 5], ==, 0x7d);
    for (unsigned int i = 0; i < 4; i++) {
        g_assert_cmpuint(fb[(10 - i) * 32 + 20 - i], ==, 0x7d);
    }
    for (unsigned int x = 12; x < 20; x++) {
        g_assert_cmpuint(fb[11 * 32 + x], ==,
                         (x >= 15 && x <= 17) ? 0x7d : 0x11);
    }

    /* Packed 24-bpp line honors the byte planemask. */
    memset(fb, 0xaa, 48);
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, 48);
    rage128_set_surface(test, 1); /* 24-byte 24-bpp stride */
    rage128_write(test, R128_DP_WRITE_MASK, 0x0000ff00);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_SOLIDCOLOR |
                  R128_GMC_DST_24BPP | R128_GMC_SRC_DATATYPE_COLOR |
                  R128_ROP3_PATCOPY);
    rage128_write(test, R128_DP_BRUSH_FRGD_CLR, 0x00112233);
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR,
                  R128_LINE_X_LEFT_TO_RIGHT |
                  R128_LINE_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DST_Y_X, 1);
    rage128_write(test, R128_DST_BRES_ERR, UINT32_MAX);
    rage128_write(test, R128_DST_BRES_INC, 0);
    rage128_write(test, R128_DST_BRES_DEC, 0);
    rage128_write(test, R128_DST_BRES_LNTH, 3);
    qpci_memread(test->dev, test->framebuffer, 0, fb, 24);
    for (unsigned int x = 0; x < 8; x++) {
        unsigned int offset = x * 3;
        bool written = x >= 1 && x <= 3;

        g_assert_cmpuint(fb[offset], ==, 0xaa);
        g_assert_cmpuint(fb[offset + 1], ==, written ? 0x22 : 0xaa);
        g_assert_cmpuint(fb[offset + 2], ==, 0xaa);
    }

    /* Horizontal 24-bpp scissors are programmed in byte coordinates. */
    memset(fb, 0xaa, 24);
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, 24);
    rage128_set_surface(test, 1);
    rage128_write(test, R128_DP_WRITE_MASK, 0x00ffffff);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_SOLIDCOLOR |
                  R128_GMC_DST_24BPP | R128_GMC_SRC_DATATYPE_COLOR |
                  R128_ROP3_PATCOPY);
    rage128_write(test, R128_DP_BRUSH_FRGD_CLR, 0x00556677);
    rage128_write(test, R128_SC_TOP_LEFT, 2U * 3U);
    rage128_write(test, R128_SC_BOTTOM_RIGHT, 4U * 3U + 2U);
    rage128_write(test, R128_DP_CNTL_XDIR_YDIR_YMAJOR,
                  R128_LINE_X_LEFT_TO_RIGHT |
                  R128_LINE_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_DST_Y_X, 0);
    rage128_write(test, R128_DST_BRES_ERR, UINT32_MAX);
    rage128_write(test, R128_DST_BRES_INC, 0);
    rage128_write(test, R128_DST_BRES_DEC, 0);
    rage128_write(test, R128_DST_BRES_LNTH, 6);
    qpci_memread(test->dev, test->framebuffer, 0, fb, 24);
    for (unsigned int x = 0; x < 8; x++) {
        unsigned int offset = x * 3;
        bool written = x >= 2 && x <= 4;

        g_assert_cmpuint(fb[offset], ==, written ? 0x77 : 0xaa);
        g_assert_cmpuint(fb[offset + 1], ==, written ? 0x66 : 0xaa);
        g_assert_cmpuint(fb[offset + 2], ==, written ? 0x55 : 0xaa);
    }

    rage128_test_stop(test);
}

static void test_rage128_2d(void)
{
    Rage128Test *test = rage128_test_start();
    uint8_t fb[192];
    static const uint8_t fill_expected[8] = {
        0x3c, 0x3c, 0x3c, 0x3c, 0x3c, 0x3c, 0x3c, 0x3c,
    };
    static const uint8_t mono_expected[8] = {
        0xee, 0x11, 0xee, 0x11, 0x11, 0xee, 0x11, 0xee,
    };
    static const uint8_t transparent_expected[8] = {
        0xcc, 0xcc, 0xcc, 0xcc, 0x22, 0x22, 0x22, 0x22,
    };

    memset(fb, 0x5a, sizeof(fb));
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, sizeof(fb));

    /* Solid PATCOPY with a nonzero origin and three scanlines. */
    rage128_set_surface(test, 4); /* 4 groups of 8 pixels: 32-byte stride */
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_SOLIDCOLOR |
                  R128_GMC_DST_8BPP | R128_GMC_SRC_DATATYPE_COLOR |
                  R128_ROP3_PATCOPY);
    rage128_write(test, R128_DP_BRUSH_FRGD_CLR, 0x3c);
    rage128_write(test, R128_DST_X, 3);
    rage128_write(test, R128_DST_Y, 2);
    rage128_write(test, R128_DST_HEIGHT_WIDTH, (3U << 16) | 8U);

    qpci_memread(test->dev, test->framebuffer, 0, fb, sizeof(fb));
    for (unsigned int row = 0; row < 3; row++) {
        unsigned int offset = (2 + row) * 32 + 3;

        g_assert_cmpmem(&fb[offset], sizeof(fill_expected),
                        fill_expected, sizeof(fill_expected));
        g_assert_cmpuint(fb[offset - 1], ==, 0x5a);
        g_assert_cmpuint(fb[offset + sizeof(fill_expected)], ==, 0x5a);
    }

    /* Overlapping SRCCOPY proceeds right-to-left like a real driver blit. */
    for (unsigned int i = 0; i < 16; i++)
        fb[i] = i;
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, 32);
    rage128_set_surface(test, 4);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_NONE |
                  R128_GMC_DST_8BPP | R128_GMC_SRC_DATATYPE_COLOR |
                  R128_ROP3_SRCCOPY | R128_DP_SRC_SOURCE_MEMORY);
    rage128_write(test, R128_DP_CNTL, R128_DST_Y_TOP_TO_BOTTOM);
    rage128_write(test, R128_SRC_X, 7);
    rage128_write(test, R128_SRC_Y, 0);
    rage128_write(test, R128_DST_X, 11);
    rage128_write(test, R128_DST_Y, 0);
    rage128_write(test, R128_DST_HEIGHT_WIDTH, (1U << 16) | 8U);
    qpci_memread(test->dev, test->framebuffer, 0, fb, 16);
    for (unsigned int i = 0; i < 8; i++)
        g_assert_cmpuint(fb[4 + i], ==, i);

    /* Monochrome HOST_DATA expansion, LSB first. */
    memset(fb, 0, 32);
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, 32);
    rage128_set_surface(test, 4);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_NONE |
                  R128_GMC_DST_8BPP |
                  R128_GMC_SRC_DATATYPE_MONO_FG_BG |
                  R128_GMC_BYTE_LSB_TO_MSB | R128_ROP3_SRCCOPY |
                  R128_DP_SRC_SOURCE_HOST_DATA);
    rage128_write(test, R128_DP_SRC_FRGD_CLR, 0xee);
    rage128_write(test, R128_DP_SRC_BKGD_CLR, 0x11);
    rage128_write(test, R128_DST_X, 0);
    rage128_write(test, R128_DST_Y, 0);
    rage128_write(test, R128_DST_HEIGHT_WIDTH, (1U << 16) | 8U);
    rage128_write(test, R128_HOST_DATA_LAST, 0x000000a5);
    qpci_memread(test->dev, test->framebuffer, 0, fb, 8);
    g_assert_cmpmem(fb, sizeof(mono_expected), mono_expected,
                    sizeof(mono_expected));

    /* Transparent mono leaves background pixels untouched. */
    memset(fb, 0x22, 32);
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, 32);
    rage128_set_surface(test, 4);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_NONE |
                  R128_GMC_DST_8BPP | R128_GMC_SRC_DATATYPE_MONO_FG |
                  R128_GMC_BYTE_LSB_TO_MSB | R128_ROP3_SRCCOPY |
                  R128_DP_SRC_SOURCE_HOST_DATA);
    rage128_write(test, R128_DP_SRC_FRGD_CLR, 0xcc);
    rage128_write(test, R128_DST_X, 0);
    rage128_write(test, R128_DST_Y, 0);
    rage128_write(test, R128_DST_HEIGHT_WIDTH, (1U << 16) | 8U);
    rage128_write(test, R128_HOST_DATA_LAST, 0x0000000f);
    qpci_memread(test->dev, test->framebuffer, 0, fb, 8);
    g_assert_cmpmem(fb, sizeof(transparent_expected), transparent_expected,
                    sizeof(transparent_expected));

    /* Packed 24-bpp PATCOPY honors a per-byte write mask. */
    memset(fb, 0xaa, 48);
    qpci_memwrite(test->dev, test->framebuffer, 0, fb, 48);
    rage128_set_surface(test, 1); /* 8 pixels, 24-byte stride */
    rage128_write(test, R128_DP_WRITE_MASK, 0x0000ff00);
    rage128_write(test, R128_DP_GUI_MASTER_CNTL,
                  R128_COMMON_MASTER | R128_GMC_BRUSH_SOLIDCOLOR |
                  R128_GMC_DST_24BPP | R128_GMC_SRC_DATATYPE_COLOR |
                  R128_ROP3_PATCOPY);
    rage128_write(test, R128_DP_BRUSH_FRGD_CLR, 0x00112233);
    rage128_write(test, R128_DST_X, 1);
    rage128_write(test, R128_DST_Y, 0);
    rage128_write(test, R128_DST_HEIGHT_WIDTH, (2U << 16) | 4U);
    qpci_memread(test->dev, test->framebuffer, 0, fb, 48);
    for (unsigned int row = 0; row < 2; row++) {
        for (unsigned int x = 1; x < 5; x++) {
            unsigned int offset = row * 24 + x * 3;

            g_assert_cmpuint(fb[offset], ==, 0xaa);
            g_assert_cmpuint(fb[offset + 1], ==, 0x22);
            g_assert_cmpuint(fb[offset + 2], ==, 0xaa);
        }
    }

    rage128_test_stop(test);
}

int main(int argc, char **argv)
{
    static const char *devices[] = {
        "ati-vga",
        "cirrus-vga",
        "VGA",
        "secondary-vga",
        "virtio-gpu-pci",
        "virtio-vga"
    };

    g_test_init(&argc, &argv, NULL);

    for (size_t i = 0; i < ARRAY_SIZE(devices); i++) {
        if (qtest_has_device(devices[i])) {
            char *testpath = g_strdup_printf("/display/pci/%s", devices[i]);
            qtest_add_data_func(testpath, devices[i], test_vga);
            g_free(testpath);
        }
    }

    if (qtest_has_device("secondary-vga")) {
        qtest_add_func("/display/pci/multihead", pci_multihead);
    }

    if (test_arch_is_x86() && qtest_has_device("ati-vga")) {
        qtest_add_func("/display/pci/ati-vga/rage128-pci",
                       test_rage128_pci_and_apertures);
        qtest_add_func("/display/pci/ati-vga/rage128-re-pci",
                       test_rage128_re_pci_profile);
        qtest_add_func("/display/pci/ati-vga/rage128-ddc",
                       test_rage128_ddc);
        qtest_add_func("/display/pci/ati-vga/rage128-engine-control",
                       test_rage128_engine_control);
        qtest_add_func("/display/pci/ati-vga/rage128-lines",
                       test_rage128_lines);
        qtest_add_func("/display/pci/ati-vga/rage128-2d",
                       test_rage128_2d);
    }

    return g_test_run();
}
