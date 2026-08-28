/*
 * ATI Rage 128 PowerPC CCE/PM4 endian qtest
 *
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#include "qemu/osdep.h"
#include "libqtest.h"
#include "libqos/libqos-spapr.h"
#include "libqos/pci.h"
#include "hw/pci/pci_regs.h"
#include "qemu/bswap.h"
#include "qemu/units.h"

#define ATI_VENDOR_ID                   0x1002
#define ATI_RAGE128_RE_DEVICE_ID        0x5245

#define R128_BUS_CNTL                   0x0030
#define R128_PCI_GART_PAGE              0x017c
#define R128_PM4_BUFFER_OFFSET          0x0700
#define R128_PM4_BUFFER_CNTL            0x0704
#define R128_PM4_BUFFER_WM_CNTL         0x0708
#define R128_PM4_BUFFER_DL_RPTR_ADDR    0x070c
#define R128_PM4_BUFFER_DL_RPTR         0x0710
#define R128_PM4_BUFFER_DL_WPTR         0x0714
#define R128_PM4_VC_FPU_SETUP           0x071c
#define R128_PM4_STAT                   0x07b8
#define R128_PM4_MICROCODE_ADDR         0x07d4
#define R128_PM4_MICROCODE_DATAH        0x07dc
#define R128_PM4_MICROCODE_DATAL        0x07e0
#define R128_PM4_MICRO_CNTL             0x07fc
#define R128_PM4_FIFO_DATA_EVEN         0x1000
#define R128_PM4_FIFO_DATA_ODD          0x1004
#define R128_GUI_SCRATCH_REG0           0x15e0
#define R128_DP_CNTL                    0x16c0
#define R128_DP_WRITE_MASK              0x16cc
#define R128_DST_PITCH_OFFSET_C         0x1c80
#define R128_DP_GUI_MASTER_CNTL_C       0x1c84
#define R128_SC_TOP_LEFT_C              0x1c88
#define R128_SC_BOTTOM_RIGHT_C          0x1c8c
#define R128_Z_OFFSET_C                 0x1c90
#define R128_Z_PITCH_C                  0x1c94
#define R128_Z_STEN_CNTL_C              0x1c98
#define R128_TEX_CNTL_C                 0x1c9c
#define R128_MISC_3D_STATE_CNTL_REG     0x1ca0
#define R128_PLANE_3D_MASK_C            0x1d44

#define R128_PM4_PACKET0                0x00000000U
#define R128_PM4_PACKET2                0x80000000U
#define R128_PM4_PACKET3                0xc0000000U
#define R128_PM4_3D_RNDR_GEN_INDX_PRIM 0x00002300U

#define R128_GMC_DST_32BPP              0x00000600U
#define R128_PM4_192PIO                 (1U << 28)
#define R128_PM4_192BM                  (2U << 28)
#define R128_PM4_BUFFER_CNTL_NOUPDATE   (1U << 27)
#define R128_PM4_MICRO_FREERUN          (1U << 30)
#define R128_PM4_BUSY                   (1U << 16)
#define R128_PM4_GUI_ACTIVE             (1U << 31)
#define R128_ALPHA_TEST_ALWAYS          (7U << 24)
#define R128_Z_PIX_WIDTH_32             (2U << 1)
#define R128_Z_TEST_LESS                (1U << 4)
#define R128_VC_FRMT_DIFFUSE_ARGB       0x00000008U
#define R128_VC_PRIM_TRI_LIST           0x00000004U
#define R128_VC_PRIM_WALK_LIST          0x00000020U

#define GART_VIRT                       0x02000000U
#define RING_DWORDS                     1024U
#define PAGE_SIZE                       4096U

typedef struct PpcRage128Test {
    QOSState *qs;
    QPCIDevice *dev;
    QPCIBar framebuffer;
    QPCIBar mmio;
    uint64_t framebuffer_size;
    uint64_t mmio_size;
    uint32_t ring_phys;
    uint32_t vertex_phys;
    uint32_t rptr_phys;
    uint32_t indirect_phys;
    uint32_t gart_phys;
} PpcRage128Test;

typedef struct RingBuilder {
    uint32_t words[RING_DWORDS];
    unsigned int count;
} RingBuilder;

static uint32_t alloc_page(PpcRage128Test *test)
{
    uint64_t address = qmalloc(test->qs, PAGE_SIZE);

    g_assert_cmphex(address, <=, UINT32_MAX);
    g_assert_cmphex(address & (PAGE_SIZE - 1), ==, 0);
    return address;
}

static PpcRage128Test *ppc_rage128_start(void)
{
    PpcRage128Test *test = g_new0(PpcRage128Test, 1);
    uint16_t command;

    test->qs = qtest_spapr_boot(
        "-machine pseries -m 256M -nodefaults -display none "
        "-device ati-vga,model=rage128re,addr=04.0,rombar=0");
    test->dev = qpci_device_find(test->qs->pcibus, QPCI_DEVFN(4, 0));
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

    test->ring_phys = alloc_page(test);
    test->vertex_phys = alloc_page(test);
    test->rptr_phys = alloc_page(test);
    test->indirect_phys = alloc_page(test);
    test->gart_phys = alloc_page(test);
    return test;
}

static void ppc_rage128_stop(PpcRage128Test *test)
{
    qpci_iounmap(test->dev, test->mmio);
    qpci_iounmap(test->dev, test->framebuffer);
    g_free(test->dev);
    qfree(test->qs, test->gart_phys);
    qfree(test->qs, test->indirect_phys);
    qfree(test->qs, test->rptr_phys);
    qfree(test->qs, test->vertex_phys);
    qfree(test->qs, test->ring_phys);
    qtest_spapr_shutdown(test->qs);
    g_free(test);
}

static void mmio_write(PpcRage128Test *test, uint32_t reg, uint32_t value)
{
    qpci_io_writel(test->dev, test->mmio, reg, value);
}

static uint32_t mmio_read(PpcRage128Test *test, uint32_t reg)
{
    return qpci_io_readl(test->dev, test->mmio, reg);
}

static uint32_t float_bits(float value)
{
    uint32_t bits;

    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static void ring_emit(RingBuilder *ring, uint32_t value)
{
    g_assert_cmpuint(ring->count, <, RING_DWORDS);
    ring->words[ring->count++] = value;
}

static void ring_packet0_one(RingBuilder *ring, uint32_t reg, uint32_t value)
{
    ring_emit(ring, R128_PM4_PACKET0 | (reg >> 2));
    ring_emit(ring, value);
}

static void ring_packet3(RingBuilder *ring, uint32_t opcode,
                         const uint32_t *payload, unsigned int count)
{
    g_assert_cmpuint(count, >, 0);
    ring_emit(ring, R128_PM4_PACKET3 | opcode | ((count - 1U) << 16));
    for (unsigned int i = 0; i < count; i++) {
        ring_emit(ring, payload[i]);
    }
}

static uint32_t surface_pitch_offset(uint32_t offset)
{
    return (8U << 21) | (offset >> 5);
}

static void load_microcode(PpcRage128Test *test)
{
    mmio_write(test, R128_PM4_MICROCODE_ADDR, 0);
    for (unsigned int i = 0; i < 256; i++) {
        mmio_write(test, R128_PM4_MICROCODE_DATAH,
                   UINT32_C(0x5a000000) | i);
        mmio_write(test, R128_PM4_MICROCODE_DATAL,
                   UINT32_C(0xa5000000) | (255 - i));
    }
}

static void setup_gart(PpcRage128Test *test)
{
    uint32_t entries[4] = {
        cpu_to_le32(test->ring_phys),
        cpu_to_le32(test->vertex_phys),
        cpu_to_le32(test->rptr_phys),
        cpu_to_le32(test->indirect_phys),
    };
    uint32_t zero = 0;

    qtest_memwrite(test->qs->qts, test->gart_phys,
                   entries, sizeof(entries));
    qtest_memwrite(test->qs->qts, test->rptr_phys,
                   &zero, sizeof(zero));
    mmio_write(test, R128_PCI_GART_PAGE, test->gart_phys);
}

static void write_vertices(PpcRage128Test *test)
{
    const float xyz[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    uint32_t words[12];

    for (unsigned int i = 0; i < 3; i++) {
        words[i * 4 + 0] = cpu_to_le32(float_bits(xyz[i][0]));
        words[i * 4 + 1] = cpu_to_le32(float_bits(xyz[i][1]));
        words[i * 4 + 2] = cpu_to_le32(float_bits(xyz[i][2]));
        /* Mesa's little-endian vertex byte order is R, G, B, A. */
        words[i * 4 + 3] = cpu_to_le32(UINT32_C(0xff0000ff));
    }
    qtest_memwrite(test->qs->qts, test->vertex_phys,
                   words, sizeof(words));
}

static void upload_ring(PpcRage128Test *test, const RingBuilder *ring)
{
    uint32_t raw[RING_DWORDS] = { 0 };

    for (unsigned int i = 0; i < ring->count; i++) {
        raw[i] = cpu_to_le32(ring->words[i]);
    }
    qtest_memwrite(test->qs->qts, test->ring_phys,
                   raw, ring->count * sizeof(raw[0]));
}

static void execute_ring(PpcRage128Test *test, const RingBuilder *ring)
{
    uint32_t shadow;

    upload_ring(test, ring);
    mmio_write(test, R128_PM4_MICRO_CNTL, 0);
    mmio_write(test, R128_BUS_CNTL, 0);
    mmio_write(test, R128_PM4_BUFFER_OFFSET, GART_VIRT);
    mmio_write(test, R128_PM4_BUFFER_WM_CNTL, 0x02020204);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR_ADDR,
               GART_VIRT + 0x2000U);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR, 0);
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, 0);
    mmio_write(test, R128_PM4_BUFFER_CNTL,
               R128_PM4_192BM | R128_PM4_BUFFER_CNTL_NOUPDATE | 9U);
    mmio_write(test, R128_PM4_MICRO_CNTL, R128_PM4_MICRO_FREERUN);
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, ring->count);

    g_assert_cmpuint(mmio_read(test, R128_PM4_BUFFER_DL_RPTR), ==,
                     ring->count);
    g_assert_cmphex(mmio_read(test, R128_PM4_STAT) &
                    (R128_PM4_BUSY | R128_PM4_GUI_ACTIVE), ==, 0);
    qtest_memread(test->qs->qts, test->rptr_phys,
                  &shadow, sizeof(shadow));
    g_assert_cmpuint(le32_to_cpu(shadow), ==, ring->count);
}

static void test_ppc_pm4_endian(void)
{
    PpcRage128Test *test = ppc_rage128_start();
    RingBuilder ring = { 0 };
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t draw[4] = {
        GART_VIRT + 0x1000U,
        3,
        R128_VC_FRMT_DIFFUSE_ARGB,
        R128_VC_PRIM_TRI_LIST | R128_VC_PRIM_WALK_LIST | (3U << 16),
    };
    uint8_t ring_bytes[4];
    uint8_t vertex_bytes[4];
    uint8_t pixel_bytes[4];

    load_microcode(test);
    setup_gart(test);
    write_vertices(test);

    ring_packet0_one(&ring, R128_DP_CNTL, 3);
    ring_packet0_one(&ring, R128_DP_WRITE_MASK, UINT32_MAX);
    ring_packet0_one(&ring, R128_DST_PITCH_OFFSET_C,
                     surface_pitch_offset(0));
    ring_packet0_one(&ring, R128_DP_GUI_MASTER_CNTL_C,
                     R128_GMC_DST_32BPP);
    ring_packet0_one(&ring, R128_SC_TOP_LEFT_C, 0);
    ring_packet0_one(&ring, R128_SC_BOTTOM_RIGHT_C,
                     (63U << 16) | 63U);
    ring_packet0_one(&ring, R128_Z_OFFSET_C, 0x00010000U);
    ring_packet0_one(&ring, R128_Z_PITCH_C, 8);
    ring_packet0_one(&ring, R128_Z_STEN_CNTL_C,
                     R128_Z_PIX_WIDTH_32 | R128_Z_TEST_LESS);
    ring_packet0_one(&ring, R128_TEX_CNTL_C, 0);
    ring_packet0_one(&ring, R128_MISC_3D_STATE_CNTL_REG,
                     R128_ALPHA_TEST_ALWAYS);
    ring_packet0_one(&ring, R128_PLANE_3D_MASK_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PM4_VC_FPU_SETUP, vc_setup);
    ring_packet3(&ring, R128_PM4_3D_RNDR_GEN_INDX_PRIM,
                 draw, G_N_ELEMENTS(draw));
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0,
                     UINT32_C(0x50504342));
    ring_emit(&ring, R128_PM4_PACKET2);
    execute_ring(test, &ring);

    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==,
                    UINT32_C(0x50504342));
    g_assert_cmphex(qpci_io_readl(test->dev, test->framebuffer,
                                 (16U * 64U + 16U) * 4U), ==,
                    UINT32_C(0xffff0000));

    qtest_memread(test->qs->qts, test->ring_phys,
                  ring_bytes, sizeof(ring_bytes));
    g_assert_cmphex(ring_bytes[0], ==, ring.words[0] & 0xffU);
    g_assert_cmphex(ring_bytes[1], ==, (ring.words[0] >> 8) & 0xffU);
    g_assert_cmphex(ring_bytes[2], ==, (ring.words[0] >> 16) & 0xffU);
    g_assert_cmphex(ring_bytes[3], ==, ring.words[0] >> 24);

    qtest_memread(test->qs->qts, test->vertex_phys,
                  vertex_bytes, sizeof(vertex_bytes));
    g_assert_cmphex(vertex_bytes[0], ==, 0x00);
    g_assert_cmphex(vertex_bytes[1], ==, 0x00);
    g_assert_cmphex(vertex_bytes[2], ==, 0x00);
    g_assert_cmphex(vertex_bytes[3], ==, 0x41); /* 8.0f, little-endian */

    qpci_memread(test->dev, test->framebuffer,
                 (16U * 64U + 16U) * 4U,
                 pixel_bytes, sizeof(pixel_bytes));
    g_assert_cmphex(pixel_bytes[0], ==, 0x00);
    g_assert_cmphex(pixel_bytes[1], ==, 0x00);
    g_assert_cmphex(pixel_bytes[2], ==, 0xff);
    g_assert_cmphex(pixel_bytes[3], ==, 0xff);

    /* PCI MMIO accessors must also deliver logical dwords from PowerPC. */
    mmio_write(test, R128_PM4_MICRO_CNTL, 0);
    mmio_write(test, R128_PM4_BUFFER_CNTL, R128_PM4_192PIO);
    mmio_write(test, R128_PM4_FIFO_DATA_EVEN,
               R128_PM4_PACKET0 | (R128_GUI_SCRATCH_REG0 >> 2));
    mmio_write(test, R128_PM4_FIFO_DATA_ODD,
               UINT32_C(0x50504321));
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==,
                    UINT32_C(0x50504321));

    ppc_rage128_stop(test);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/ati/rage128/ppc-pm4-endian",
                    test_ppc_pm4_endian);
    return g_test_run();
}
