/*
 * ATI Rage 128 CCE/PM4 and fixed-function 3D qtests
 *
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#include "qemu/osdep.h"
#include "libqtest.h"
#include "libqos/pci.h"
#include "libqos/pci-pc.h"
#include "hw/pci/pci_regs.h"
#include "qemu/bswap.h"
#include "qemu/units.h"

#define ATI_VENDOR_ID                  0x1002
#define ATI_RAGE128_RE_DEVICE_ID       0x5245

#define R128_BUS_CNTL                  0x0030
#define R128_GEN_RESET_CNTL            0x00f0
#define R128_PCI_GART_PAGE             0x017c
#define R128_PM4_BUFFER_OFFSET         0x0700
#define R128_PM4_BUFFER_CNTL           0x0704
#define R128_PM4_BUFFER_WM_CNTL        0x0708
#define R128_PM4_BUFFER_DL_RPTR_ADDR   0x070c
#define R128_PM4_BUFFER_DL_RPTR        0x0710
#define R128_PM4_BUFFER_DL_WPTR        0x0714
#define R128_PM4_VC_FPU_SETUP          0x071c
#define R128_PM4_IW_INDOFF              0x0738
#define R128_PM4_IW_INDSIZE             0x073c
#define R128_PM4_STAT                  0x07b8
#define R128_PM4_MICROCODE_ADDR        0x07d4
#define R128_PM4_MICROCODE_RADDR       0x07d8
#define R128_PM4_MICROCODE_DATAH       0x07dc
#define R128_PM4_MICROCODE_DATAL       0x07e0
#define R128_PM4_MICRO_CNTL            0x07fc
#define R128_PM4_FIFO_DATA_EVEN        0x1000
#define R128_PM4_FIFO_DATA_ODD         0x1004

#define R128_DP_CNTL                   0x16c0
#define R128_DP_WRITE_MASK             0x16cc
#define R128_GUI_SCRATCH_REG0          0x15e0
#define R128_DST_PITCH_OFFSET_C        0x1c80
#define R128_DP_GUI_MASTER_CNTL_C      0x1c84
#define R128_SC_TOP_LEFT_C             0x1c88
#define R128_SC_BOTTOM_RIGHT_C         0x1c8c
#define R128_Z_OFFSET_C                0x1c90
#define R128_Z_PITCH_C                 0x1c94
#define R128_Z_STEN_CNTL_C             0x1c98
#define R128_TEX_CNTL_C                0x1c9c
#define R128_MISC_3D_STATE_CNTL_REG    0x1ca0
#define R128_FOG_TABLE_INDEX           0x1a14
#define R128_FOG_TABLE_DATA            0x1a18
#define R128_CLR_CMP_CLR_3D            0x1a24
#define R128_CLR_CMP_MASK_3D           0x1a28
#define R128_FOG_COLOR_C                0x1cac
#define R128_SETUP_CNTL                0x1bc4
#define R128_PRIM_TEX_CNTL_C           0x1cb0
#define R128_PRIM_TEX_COMBINE_CNTL_C   0x1cb4
#define R128_TEX_SIZE_PITCH_C          0x1cb8
#define R128_PRIM_TEX_0_OFFSET_C       0x1cbc
#define R128_PRIM_TEX_1_OFFSET_C       0x1cc0
#define R128_WINDOW_XY_OFFSET           0x1bcc
#define R128_SEC_TEX_CNTL_C             0x1d00
#define R128_SEC_TEX_COMBINE_CNTL_C     0x1d04
#define R128_SEC_TEX_0_OFFSET_C         0x1d08
#define R128_CONSTANT_COLOR_C          0x1d34
#define R128_PRIM_TEXTURE_BORDER_COLOR_C 0x1d38
#define R128_SEC_TEXTURE_BORDER_COLOR_C 0x1d3c
#define R128_STEN_REF_MASK_C            0x1d40
#define R128_PLANE_3D_MASK_C           0x1d44
#define R128_SOLID_COLOR                0x1bc8
#define R128_AUX_SC_CNTL                0x1660
#define R128_AUX1_SC_LEFT               0x1664
#define R128_AUX1_SC_RIGHT              0x1668
#define R128_AUX1_SC_TOP                0x166c
#define R128_AUX1_SC_BOTTOM             0x1670

#define R128_PM4_PACKET0               0x00000000U
#define R128_PM4_PACKET0_ONE_REG_WR    0x00008000U
#define R128_PM4_PACKET2               0x80000000U
#define R128_PM4_PACKET3               0xc0000000U
#define R128_PM4_CNTL_HOSTDATA_BLT     0x00009400U
#define R128_PM4_CNTL_PAINT_MULTI      0x00009a00U
#define R128_PM4_CNTL_BITBLT_MULTI     0x00009b00U
#define R128_PM4_3D_RNDR_GEN_INDX_PRIM 0x00002300U
#define R128_PM4_3D_RNDR_GEN_PRIM      0x00002500U
#define R128_PM4_CNTL_LOAD_PALETTE      0x00002c00U

#define R128_GMC_SRC_PITCH_OFFSET_CNTL 0x00000001U
#define R128_GMC_DST_PITCH_OFFSET_CNTL 0x00000002U
#define R128_GMC_BRUSH_SOLID_COLOR     0x000000d0U
#define R128_GMC_BRUSH_NONE            0x000000f0U
#define R128_GMC_DST_16BPP             0x00000400U
#define R128_GMC_DST_32BPP             0x00000600U
#define R128_GMC_DST_Y8                0x00000800U
#define R128_GMC_SRC_DATATYPE_COLOR    0x00003000U
#define R128_ROP3_S                    0x00cc0000U
#define R128_ROP3_P                    0x00f00000U
#define R128_DP_SRC_SOURCE_MEMORY      0x02000000U
#define R128_DP_SRC_SOURCE_HOST_DATA   0x03000000U
#define R128_GMC_CLR_CMP_CNTL_DIS      0x10000000U
#define R128_GMC_AUX_CLIP_DIS          0x20000000U
#define R128_GMC_WR_MSK_DIS            0x40000000U

#define R128_PM4_192BM                 (2U << 28)
#define R128_PM4_BUFFER_CNTL_NOUPDATE  (1U << 27)
#define R128_PM4_MICRO_FREERUN         (1U << 30)
#define R128_PM4_BUSY                  (1U << 16)
#define R128_PM4_GUI_ACTIVE            (1U << 31)
#define R128_SOFT_RESET_GUI            (1U << 0)

#define R128_VC_FRMT_RHW               0x00000001U
#define R128_VC_FRMT_DIFFUSE_ARGB      0x00000008U
#define R128_VC_FRMT_SPEC_FRGB          0x00000040U
#define R128_VC_FRMT_ST                 0x00000080U
#define R128_VC_FRMT_S2T2               0x00000100U
#define R128_VC_PRIM_POINT             0x00000001U
#define R128_VC_PRIM_LINE              0x00000002U
#define R128_VC_PRIM_TRI_LIST          0x00000004U
#define R128_VC_PRIM_TRI_FAN           0x00000005U
#define R128_VC_PRIM_WALK_IND          0x00000010U
#define R128_VC_PRIM_WALK_LIST         0x00000020U
#define R128_VC_PRIM_WALK_RING         0x00000030U

#define R128_TEX_Z_ENABLE              (1U << 0)
#define R128_TEX_Z_WRITE_ENABLE        (1U << 1)
#define R128_TEX_STENCIL_ENABLE        (1U << 3)
#define R128_TEXMAP_ENABLE             (1U << 4)
#define R128_SEC_TEXMAP_ENABLE         (1U << 5)
#define R128_TEX_FOG_ENABLE            (1U << 7)
#define R128_TEX_DITHER_ENABLE         (1U << 8)
#define R128_TEX_ALPHA_ENABLE          (1U << 9)
#define R128_TEX_ALPHA_TEST_ENABLE     (1U << 10)
#define R128_TEX_SPEC_LIGHT_ENABLE     (1U << 11)
#define R128_TEX_CHROMA_KEY_ENABLE     (1U << 12)
#define R128_TEX_LIGHT_FN(n)           ((uint32_t)(n) << 14)
#define R128_ALPHA_LIGHT_FN(n)         ((uint32_t)(n) << 18)
#define R128_FOG_TABLE_ENABLE          (1U << 14)
#define R128_TEX_LOD_BIAS_ZERO         (0x3fU << 24)
#define R128_TEX_FMT_CI4               (1U << 16)
#define R128_TEX_FMT_CI8               (2U << 16)
#define R128_TEX_FMT_CI16              (10U << 16)
#define R128_TEX_PALETTE(n)            ((uint32_t)(n) << 20)
#define R128_TEX_PSEUDO_ARGB1555       (1U << 24)
#define R128_TEX_PSEUDO_ARGB4444       (2U << 24)
#define R128_COMB_FCN_MSB              (1U << 8)
#define R128_Z_PIX_WIDTH_24            (1U << 1)
#define R128_Z_PIX_WIDTH_32            (2U << 1)
#define R128_Z_TEST_LESS               (1U << 4)
#define R128_STENCIL_TEST_EQUAL        (3U << 12)
#define R128_STENCIL_S_FAIL_REPLACE    (2U << 16)
#define R128_STENCIL_Z_PASS_INCREMENT  (3U << 20)
#define R128_STENCIL_Z_FAIL_DECREMENT  (4U << 24)
#define R128_ALPHA_BLEND_SRC_ONE       (1U << 16)
#define R128_ALPHA_BLEND_SRC_SRCALPHA  (4U << 16)
#define R128_ALPHA_BLEND_DST_ONE       (1U << 20)
#define R128_ALPHA_BLEND_DST_INVSRCALPHA (5U << 20)
#define R128_ALPHA_COMB_SUB_SRC_DST_CLAMP (2U << 12)
#define R128_ALPHA_TEST_GREATER        (5U << 24)
#define R128_ALPHA_TEST_ALWAYS         (7U << 24)
#define R128_CLR_CMP_FCN_ALWAYS        (0U << 30)
#define R128_CLR_CMP_FCN_NEVER         (1U << 30)
#define R128_CLR_CMP_FCN_EQUAL         (2U << 30)
#define R128_CLR_CMP_FCN_NEQUAL        (3U << 30)

#define RING_PHYS                      0x00100000U
#define VERTEX_PHYS                    0x00110000U
#define RPTR_PHYS                      0x00120000U
#define INDIRECT_PHYS                  0x00130000U
#define GART_PHYS                      0x00180000U
#define GART_VIRT                      0x02000000U
#define DEPTH_OFFSET                   0x00010000U
#define TEXTURE_OFFSET                 0x00020000U
#define TEXTURE1_OFFSET                0x00030000U
#define RING_DWORDS                    1024U


typedef struct Rage128PM4Test {
    QTestState *qts;
    QPCIBus *bus;
    QPCIDevice *dev;
    QPCIBar framebuffer;
    QPCIBar mmio;
    uint64_t framebuffer_size;
    uint64_t mmio_size;
} Rage128PM4Test;

typedef struct RingBuilder {
    uint32_t words[RING_DWORDS];
    unsigned int count;
} RingBuilder;

static Rage128PM4Test *rage128_pm4_start(void)
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

static void rage128_pm4_stop(Rage128PM4Test *test)
{
    qpci_iounmap(test->dev, test->mmio);
    qpci_iounmap(test->dev, test->framebuffer);
    g_free(test->dev);
    qpci_free_pc(test->bus);
    qtest_quit(test->qts);
    g_free(test);
}

static void mmio_write(Rage128PM4Test *test, uint32_t reg, uint32_t value)
{
    qpci_io_writel(test->dev, test->mmio, reg, value);
}

static uint32_t mmio_read(Rage128PM4Test *test, uint32_t reg)
{
    return qpci_io_readl(test->dev, test->mmio, reg);
}

static uint32_t framebuffer_read(Rage128PM4Test *test, unsigned int x,
                                 unsigned int y)
{
    return qpci_io_readl(test->dev, test->framebuffer,
                         (y * 64 + x) * sizeof(uint32_t));
}

static uint16_t framebuffer_read16(Rage128PM4Test *test,
                                   unsigned int x, unsigned int y)
{
    return qpci_io_readw(test->dev, test->framebuffer,
                         (y * 64 + x) * sizeof(uint16_t));
}

static uint32_t vram_read32(Rage128PM4Test *test, uint32_t offset)
{
    return qpci_io_readl(test->dev, test->framebuffer, offset);
}

static uint8_t vram_read8(Rage128PM4Test *test, uint32_t offset)
{
    return qpci_io_readb(test->dev, test->framebuffer, offset);
}

static uint8_t framebuffer_read8(Rage128PM4Test *test, unsigned int x,
                                 unsigned int y)
{
    return vram_read8(test, y * 64 + x);
}

static void vram_write32(Rage128PM4Test *test, uint32_t offset,
                         uint32_t value)
{
    qpci_io_writel(test->dev, test->framebuffer, offset, value);
}

static void vram_write8(Rage128PM4Test *test, uint32_t offset,
                        uint8_t value)
{
    qpci_io_writeb(test->dev, test->framebuffer, offset, value);
}

static void vram_write16(Rage128PM4Test *test, uint32_t offset,
                         uint16_t value)
{
    qpci_io_writew(test->dev, test->framebuffer, offset, value);
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

static void ring_packet0(RingBuilder *ring, uint32_t reg,
                         const uint32_t *values, unsigned int count)
{
    g_assert_cmpuint(count, >, 0);
    ring_emit(ring, R128_PM4_PACKET0 | ((count - 1) << 16) | (reg >> 2));
    for (unsigned int i = 0; i < count; i++) {
        ring_emit(ring, values[i]);
    }
}

static void ring_packet0_repeat(RingBuilder *ring, uint32_t reg,
                                const uint32_t *values,
                                unsigned int count)
{
    g_assert_cmpuint(count, >, 0);
    ring_emit(ring, R128_PM4_PACKET0 | R128_PM4_PACKET0_ONE_REG_WR |
                    ((count - 1) << 16) | (reg >> 2));
    for (unsigned int i = 0; i < count; i++) {
        ring_emit(ring, values[i]);
    }
}

static void ring_packet0_one(RingBuilder *ring, uint32_t reg, uint32_t value)
{
    ring_packet0(ring, reg, &value, 1);
}

static void ring_packet3(RingBuilder *ring, uint32_t opcode,
                         const uint32_t *values, unsigned int count)
{
    g_assert_cmpuint(count, >, 0);
    ring_emit(ring, R128_PM4_PACKET3 | opcode | ((count - 1) << 16));
    for (unsigned int i = 0; i < count; i++) {
        ring_emit(ring, values[i]);
    }
}

static void load_microcode(Rage128PM4Test *test)
{
    mmio_write(test, R128_PM4_MICROCODE_ADDR, 0);
    for (unsigned int i = 0; i < 256; i++) {
        mmio_write(test, R128_PM4_MICROCODE_DATAH,
                   UINT32_C(0x5a000000) | i);
        mmio_write(test, R128_PM4_MICROCODE_DATAL,
                   UINT32_C(0xa5000000) | (255 - i));
    }

    mmio_write(test, R128_PM4_MICROCODE_RADDR, 37);
    g_assert_cmphex(mmio_read(test, R128_PM4_MICROCODE_DATAH), ==,
                    UINT32_C(0x5a000025));
    g_assert_cmphex(mmio_read(test, R128_PM4_MICROCODE_DATAL), ==,
                    UINT32_C(0xa50000da));
}

static void setup_gart(Rage128PM4Test *test)
{
    uint32_t page_table[4] = {
        cpu_to_le32(RING_PHYS),
        cpu_to_le32(VERTEX_PHYS),
        cpu_to_le32(RPTR_PHYS),
        cpu_to_le32(INDIRECT_PHYS),
    };
    uint32_t zero = 0;

    qtest_memwrite(test->qts, GART_PHYS, page_table, sizeof(page_table));
    qtest_memwrite(test->qts, RPTR_PHYS, &zero, sizeof(zero));
    mmio_write(test, R128_PCI_GART_PAGE, GART_PHYS);
}

static void upload_ring(Rage128PM4Test *test, const RingBuilder *ring)
{
    uint32_t raw[RING_DWORDS] = { 0 };

    for (unsigned int i = 0; i < ring->count; i++) {
        raw[i] = cpu_to_le32(ring->words[i]);
    }
    qtest_memwrite(test->qts, RING_PHYS, raw,
                   ring->count * sizeof(raw[0]));
}

static void execute_ring(Rage128PM4Test *test, const RingBuilder *ring)
{
    uint32_t read_pointer;

    upload_ring(test, ring);
    mmio_write(test, R128_PM4_MICRO_CNTL, 0);
    /* The helper uploads a fresh ring at offset zero.  Retire the old
     * tail before reenabling the CCE, or the previous WPTR can execute
     * beyond the newly uploaded command stream. */
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, 0);
    mmio_write(test, R128_BUS_CNTL, 0);
    mmio_write(test, R128_PM4_BUFFER_OFFSET, GART_VIRT);
    mmio_write(test, R128_PM4_BUFFER_WM_CNTL, 0x02020204);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR_ADDR, GART_VIRT + 0x2000);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR, 0);
    mmio_write(test, R128_PM4_BUFFER_CNTL,
               R128_PM4_192BM | R128_PM4_BUFFER_CNTL_NOUPDATE | 9);
    mmio_write(test, R128_PM4_MICRO_CNTL, R128_PM4_MICRO_FREERUN);
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, ring->count);

    g_assert_cmpuint(mmio_read(test, R128_PM4_BUFFER_DL_RPTR), ==,
                     ring->count);
    g_assert_cmphex(mmio_read(test, R128_PM4_STAT) &
                    (R128_PM4_BUSY | R128_PM4_GUI_ACTIVE), ==, 0);
    qtest_memread(test->qts, RPTR_PHYS, &read_pointer,
                  sizeof(read_pointer));
    g_assert_cmpuint(le32_to_cpu(read_pointer), ==, ring->count);
}

static void execute_faulting_ring(Rage128PM4Test *test,
                                  const RingBuilder *ring,
                                  unsigned int expected_rptr)
{
    uint32_t read_pointer;

    g_assert_cmpuint(expected_rptr, <, ring->count);
    upload_ring(test, ring);
    mmio_write(test, R128_PM4_MICRO_CNTL, 0);
    /* The helper uploads a fresh ring at offset zero.  Retire the old
     * tail before reenabling the CCE, or the previous WPTR can execute
     * beyond the newly uploaded command stream. */
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, 0);
    mmio_write(test, R128_BUS_CNTL, 0);
    mmio_write(test, R128_PM4_BUFFER_OFFSET, GART_VIRT);
    mmio_write(test, R128_PM4_BUFFER_WM_CNTL, 0x02020204);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR_ADDR, GART_VIRT + 0x2000);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR, 0);
    mmio_write(test, R128_PM4_BUFFER_CNTL,
               R128_PM4_192BM | R128_PM4_BUFFER_CNTL_NOUPDATE | 9);
    mmio_write(test, R128_PM4_MICRO_CNTL, R128_PM4_MICRO_FREERUN);
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, ring->count);

    g_assert_cmpuint(mmio_read(test, R128_PM4_BUFFER_DL_RPTR), ==,
                     expected_rptr);
    g_assert_cmphex(mmio_read(test, R128_PM4_STAT) &
                    (R128_PM4_BUSY | R128_PM4_GUI_ACTIVE), ==, 0);
    qtest_memread(test->qts, RPTR_PHYS, &read_pointer,
                  sizeof(read_pointer));
    g_assert_cmpuint(le32_to_cpu(read_pointer), ==, expected_rptr);
}

static uint32_t surface_pitch_offset(uint32_t offset, bool tiled)
{
    return (8U << 21) | (offset >> 5) | (tiled ? (1U << 31) : 0);
}

static void ring_clear_surface(RingBuilder *ring, uint32_t offset,
                               bool tiled, uint32_t color)
{
    const uint32_t master =
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_SOLID_COLOR |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_P |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t clear[] = {
        master, surface_pitch_offset(offset, tiled), color,
        0, (64U << 16) | 64U,
    };

    ring_packet3(ring, R128_PM4_CNTL_PAINT_MULTI,
                 clear, G_N_ELEMENTS(clear));
}

static void ring_set_3d_state(RingBuilder *ring, uint32_t tex_control,
                              uint32_t misc, uint32_t plane_mask,
                              uint32_t vc_setup)
{
    ring_packet0_one(ring, R128_DP_CNTL, 3);
    ring_packet0_one(ring, R128_DP_WRITE_MASK, UINT32_MAX);
    ring_packet0_one(ring, R128_DST_PITCH_OFFSET_C,
                     surface_pitch_offset(0, false));
    ring_packet0_one(ring, R128_DP_GUI_MASTER_CNTL_C,
                     R128_GMC_DST_32BPP);
    ring_packet0_one(ring, R128_SC_TOP_LEFT_C, 0);
    ring_packet0_one(ring, R128_SC_BOTTOM_RIGHT_C,
                     (63U << 16) | 63U);
    ring_packet0_one(ring, R128_Z_OFFSET_C, DEPTH_OFFSET);
    ring_packet0_one(ring, R128_Z_PITCH_C, 8);
    ring_packet0_one(ring, R128_Z_STEN_CNTL_C,
                     R128_Z_PIX_WIDTH_32 | R128_Z_TEST_LESS);
    ring_packet0_one(ring, R128_TEX_CNTL_C,
                     tex_control | R128_TEX_LOD_BIAS_ZERO);
    ring_packet0_one(ring, R128_MISC_3D_STATE_CNTL_REG, misc);
    ring_packet0_one(ring, R128_PLANE_3D_MASK_C, plane_mask);
    ring_packet0_one(ring, R128_PM4_VC_FPU_SETUP, vc_setup);
}

static void ring_draw_format(RingBuilder *ring, uint32_t vertex_offset,
                             unsigned int count, uint32_t primitive,
                             uint32_t format)
{
    const uint32_t draw[] = {
        GART_VIRT + 0x1000 + vertex_offset,
        count,
        format,
        primitive | R128_VC_PRIM_WALK_LIST | (count << 16),
    };

    ring_packet3(ring, R128_PM4_3D_RNDR_GEN_INDX_PRIM,
                 draw, G_N_ELEMENTS(draw));
}

static void ring_draw(RingBuilder *ring, uint32_t vertex_offset,
                      unsigned int count, uint32_t primitive)
{
    ring_draw_format(ring, vertex_offset, count, primitive,
                     R128_VC_FRMT_DIFFUSE_ARGB);
}

static void ring_draw_inline(RingBuilder *ring, unsigned int count,
                             uint32_t primitive, uint32_t format,
                             const uint32_t *vertices,
                             unsigned int vertex_dwords)
{
    uint32_t *draw = g_new(uint32_t, 2 + vertex_dwords);

    draw[0] = format;
    draw[1] = primitive | R128_VC_PRIM_WALK_RING | (count << 16);
    memcpy(&draw[2], vertices, vertex_dwords * sizeof(*vertices));
    ring_packet3(ring, R128_PM4_3D_RNDR_GEN_PRIM,
                 draw, 2 + vertex_dwords);
    g_free(draw);
}

static void write_vertices_format(Rage128PM4Test *test, uint32_t offset,
                                  const float (*xyz)[3],
                                  const uint32_t *colors,
                                  const uint32_t *specular,
                                  unsigned int count, uint32_t format)
{
    unsigned int stride = 4 + !!(format & R128_VC_FRMT_SPEC_FRGB);
    uint32_t *vertices = g_new0(uint32_t, count * stride);

    for (unsigned int i = 0; i < count; i++) {
        vertices[i * stride + 0] = cpu_to_le32(float_bits(xyz[i][0]));
        vertices[i * stride + 1] = cpu_to_le32(float_bits(xyz[i][1]));
        vertices[i * stride + 2] = cpu_to_le32(float_bits(xyz[i][2]));
        vertices[i * stride + 3] = cpu_to_le32(colors[i]);
        if (stride == 5) {
            vertices[i * stride + 4] = cpu_to_le32(specular ? specular[i] : 0);
        }
    }
    qtest_memwrite(test->qts, VERTEX_PHYS + offset, vertices,
                   count * stride * sizeof(uint32_t));
    g_free(vertices);
}

static void write_vertices(Rage128PM4Test *test, uint32_t offset,
                           const float (*xyz)[3], const uint32_t *colors,
                           unsigned int count)
{
    write_vertices_format(test, offset, xyz, colors, NULL, count,
                          R128_VC_FRMT_DIFFUSE_ARGB);
}

static void write_textured_vertices(Rage128PM4Test *test,
                                    uint32_t offset,
                                    const float (*xyzrhw)[4],
                                    const uint32_t *colors,
                                    const float (*st)[2],
                                    unsigned int count)
{
    const unsigned int stride = 7;
    uint32_t *vertices = g_new0(uint32_t, count * stride);

    for (unsigned int i = 0; i < count; i++) {
        vertices[i * stride + 0] = cpu_to_le32(float_bits(xyzrhw[i][0]));
        vertices[i * stride + 1] = cpu_to_le32(float_bits(xyzrhw[i][1]));
        vertices[i * stride + 2] = cpu_to_le32(float_bits(xyzrhw[i][2]));
        vertices[i * stride + 3] = cpu_to_le32(float_bits(xyzrhw[i][3]));
        vertices[i * stride + 4] = cpu_to_le32(colors[i]);
        vertices[i * stride + 5] = cpu_to_le32(float_bits(st[i][0]));
        vertices[i * stride + 6] = cpu_to_le32(float_bits(st[i][1]));
    }
    qtest_memwrite(test->qts, VERTEX_PHYS + offset, vertices,
                   count * stride * sizeof(uint32_t));
    g_free(vertices);
}

static void write_dual_textured_vertices(Rage128PM4Test *test,
                                         uint32_t offset,
                                         const float (*xyzrhw)[4],
                                         const uint32_t *colors,
                                         const float (*st0)[2],
                                         const float (*st1)[2],
                                         unsigned int count)
{
    const unsigned int stride = 9;
    uint32_t *vertices = g_new0(uint32_t, count * stride);

    for (unsigned int i = 0; i < count; i++) {
        vertices[i * stride + 0] = cpu_to_le32(float_bits(xyzrhw[i][0]));
        vertices[i * stride + 1] = cpu_to_le32(float_bits(xyzrhw[i][1]));
        vertices[i * stride + 2] = cpu_to_le32(float_bits(xyzrhw[i][2]));
        vertices[i * stride + 3] = cpu_to_le32(float_bits(xyzrhw[i][3]));
        vertices[i * stride + 4] = cpu_to_le32(colors[i]);
        vertices[i * stride + 5] = cpu_to_le32(float_bits(st0[i][0]));
        vertices[i * stride + 6] = cpu_to_le32(float_bits(st0[i][1]));
        vertices[i * stride + 7] = cpu_to_le32(float_bits(st1[i][0]));
        vertices[i * stride + 8] = cpu_to_le32(float_bits(st1[i][1]));
    }
    qtest_memwrite(test->qts, VERTEX_PHYS + offset, vertices,
                   count * stride * sizeof(uint32_t));
    g_free(vertices);
}

static void test_pm4_control_and_2d_packets(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t surface = (8U << 21);
    const uint32_t paint_master =
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_SOLID_COLOR |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_P |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t copy_master =
        R128_GMC_SRC_PITCH_OFFSET_CNTL |
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_NONE |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_S |
        R128_DP_SRC_SOURCE_MEMORY |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t fill_block[] = {
        paint_master, surface, 0x00a1b2c3,
        (0U << 16) | 60U, (4U << 16) | 4U,
    };
    const uint32_t copy_block[] = {
        copy_master, surface, surface,
        (0U << 16) | 60U, (56U << 16) | 60U,
        (4U << 16) | 4U,
    };
    const uint32_t host_master =
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_NONE |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_S |
        R128_DP_SRC_SOURCE_HOST_DATA |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t host_block[] = {
        host_master, surface, UINT32_MAX, UINT32_MAX,
        (40U << 16) | 20U, /* Y:X */
        (2U << 16) | 2U,  /* HEIGHT:WIDTH */
        4,
        0xff112233, 0xff445566,
        0xff778899, 0xffaabbcc,
    };

    /* PIO packet submission is useful for firmware and diagnostics. */
    mmio_write(test, R128_PM4_FIFO_DATA_EVEN,
               R128_PM4_PACKET0 | (R128_GUI_SCRATCH_REG0 >> 2));
    mmio_write(test, R128_PM4_FIFO_DATA_ODD, 0x13579bdf);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==,
                    0x13579bdf);

    load_microcode(test);
    setup_gart(test);
    ring_packet0_one(&ring, R128_DP_CNTL, 3);
    ring_packet0_one(&ring, R128_DP_WRITE_MASK, UINT32_MAX);
    ring_packet3(&ring, R128_PM4_CNTL_PAINT_MULTI,
                 fill_block, G_N_ELEMENTS(fill_block));
    ring_packet3(&ring, R128_PM4_CNTL_BITBLT_MULTI,
                 copy_block, G_N_ELEMENTS(copy_block));
    ring_packet3(&ring, R128_PM4_CNTL_HOSTDATA_BLT,
                 host_block, G_N_ELEMENTS(host_block));
    ring_emit(&ring, R128_PM4_PACKET2);
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 1, 61), ==, 0x00a1b2c3);
    g_assert_cmphex(framebuffer_read(test, 57, 61), ==, 0x00a1b2c3);
    g_assert_cmphex(framebuffer_read(test, 20, 40), ==, 0xff112233);
    g_assert_cmphex(framebuffer_read(test, 21, 40), ==, 0xff445566);
    g_assert_cmphex(framebuffer_read(test, 20, 41), ==, 0xff778899);
    g_assert_cmphex(framebuffer_read(test, 21, 41), ==, 0xffaabbcc);
    rage128_pm4_stop(test);
}

static void test_pm4_oversized_paint_faults(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t paint_master =
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_SOLID_COLOR |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_P |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t oversized[] = {
        paint_master, (8U << 21), 0x00a1b2c3, 0,
        (4097U << 16) | 4097U,
    };

    load_microcode(test);
    setup_gart(test);
    mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
    ring_packet3(&ring, R128_PM4_CNTL_PAINT_MULTI,
                 oversized, G_N_ELEMENTS(oversized));
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);

    /*
     * The packet header and first four payload dwords advance RPTR to five.
     * The final, oversized rectangle dword faults in place, and the following
     * scratch write must remain unexecuted.
     */
    execute_faulting_ring(test, &ring, G_N_ELEMENTS(oversized));
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    rage128_pm4_stop(test);
}

static void test_pm4_oversized_bitblt_faults(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t copy_master =
        R128_GMC_SRC_PITCH_OFFSET_CNTL |
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_NONE |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_S |
        R128_DP_SRC_SOURCE_MEMORY |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t oversized[] = {
        copy_master, (8U << 21), (8U << 21), 0, 0,
        (4097U << 16) | 4097U,
    };

    load_microcode(test);
    setup_gart(test);
    mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
    ring_packet3(&ring, R128_PM4_CNTL_BITBLT_MULTI,
                 oversized, G_N_ELEMENTS(oversized));
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x2468ace0);

    execute_faulting_ring(test, &ring, G_N_ELEMENTS(oversized));
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    rage128_pm4_stop(test);
}

static void test_pm4_soft_reset_preserves_configuration(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder first = { 0 };
    RingBuilder second = { 0 };
    uint32_t read_pointer;

    load_microcode(test);
    setup_gart(test);
    ring_packet0_one(&first, R128_GUI_SCRATCH_REG0, 0x11111111);
    execute_ring(test, &first);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==,
                    0x11111111);

    mmio_write(test, R128_GEN_RESET_CNTL, R128_SOFT_RESET_GUI);
    mmio_write(test, R128_GEN_RESET_CNTL, 0);

    /* The DRM driver programs these before its GUI reset and reuses them. */
    g_assert_cmphex(mmio_read(test, R128_BUS_CNTL), ==, 0);
    g_assert_cmphex(mmio_read(test, R128_PCI_GART_PAGE), ==, GART_PHYS);
    g_assert_cmphex(mmio_read(test, R128_PM4_BUFFER_OFFSET), ==, GART_VIRT);
    g_assert_cmphex(mmio_read(test, R128_PM4_BUFFER_WM_CNTL), ==,
                    0x02020204);
    g_assert_cmphex(mmio_read(test, R128_PM4_BUFFER_DL_RPTR_ADDR), ==,
                    GART_VIRT + 0x2000);
    g_assert_cmphex(mmio_read(test, R128_PM4_BUFFER_CNTL), ==,
                    R128_PM4_192BM | R128_PM4_BUFFER_CNTL_NOUPDATE | 9);
    g_assert_cmpuint(mmio_read(test, R128_PM4_BUFFER_DL_RPTR), ==, 0);
    g_assert_cmpuint(mmio_read(test, R128_PM4_BUFFER_DL_WPTR), ==, 0);
    g_assert_cmpuint(mmio_read(test, R128_PM4_MICRO_CNTL), ==, 0);
    g_assert_cmphex(mmio_read(test, R128_PM4_STAT) &
                    (R128_PM4_BUSY | R128_PM4_GUI_ACTIVE), ==, 0);

    qtest_memread(test->qts, RPTR_PHYS, &read_pointer,
                  sizeof(read_pointer));
    g_assert_cmpuint(le32_to_cpu(read_pointer), ==, 0);

    mmio_write(test, R128_PM4_MICROCODE_RADDR, 37);
    g_assert_cmphex(mmio_read(test, R128_PM4_MICROCODE_DATAH), ==,
                    UINT32_C(0x5a000025));
    g_assert_cmphex(mmio_read(test, R128_PM4_MICROCODE_DATAL), ==,
                    UINT32_C(0xa50000da));

    /* Execute another ring without reloading microcode or GART/ring state. */
    ring_packet0_one(&second, R128_GUI_SCRATCH_REG0, 0x22222222);
    upload_ring(test, &second);
    mmio_write(test, R128_PM4_BUFFER_DL_RPTR, 0);
    mmio_write(test, R128_PM4_MICRO_CNTL, R128_PM4_MICRO_FREERUN);
    mmio_write(test, R128_PM4_BUFFER_DL_WPTR, second.count);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==,
                    0x22222222);
    g_assert_cmpuint(mmio_read(test, R128_PM4_BUFFER_DL_RPTR), ==,
                     second.count);
    qtest_memread(test->qts, RPTR_PHYS, &read_pointer,
                  sizeof(read_pointer));
    g_assert_cmpuint(le32_to_cpu(read_pointer), ==, second.count);

    rage128_pm4_stop(test);
}

static void test_pm4_untextured_triangle(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t surface = (8U << 21);
    const uint32_t clear_master =
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_SOLID_COLOR |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_P |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t clear[] = {
        clear_master, surface, 0xff000000,
        0, (64U << 16) | 64U,
    };
    uint32_t vertices[] = {
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(0.5f)),
        cpu_to_le32(0xff0000ff), /* bytes R,G,B,A: red */
        cpu_to_le32(float_bits(56.0f)),
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(0.5f)),
        cpu_to_le32(0xff00ff00), /* green */
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(56.0f)),
        cpu_to_le32(float_bits(0.5f)),
        cpu_to_le32(0xffff0000), /* blue */
    };
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t vc_cntl =
        R128_VC_PRIM_TRI_LIST | R128_VC_PRIM_WALK_LIST | (3U << 16);
    uint32_t pixel;
    unsigned int red;
    unsigned int green;
    unsigned int blue;

    qtest_memwrite(test->qts, VERTEX_PHYS, vertices, sizeof(vertices));
    load_microcode(test);
    setup_gart(test);

    ring_packet0_one(&ring, R128_DP_CNTL, 3);
    ring_packet0_one(&ring, R128_DP_WRITE_MASK, UINT32_MAX);
    ring_packet3(&ring, R128_PM4_CNTL_PAINT_MULTI,
                 clear, G_N_ELEMENTS(clear));
    ring_packet0_one(&ring, R128_DST_PITCH_OFFSET_C, surface);
    ring_packet0_one(&ring, R128_DP_GUI_MASTER_CNTL_C,
                     R128_GMC_DST_32BPP);
    ring_packet0_one(&ring, R128_SC_TOP_LEFT_C, 0);
    ring_packet0_one(&ring, R128_SC_BOTTOM_RIGHT_C,
                     (63U << 16) | 63U);
    ring_packet0_one(&ring, R128_Z_OFFSET_C, 0);
    ring_packet0_one(&ring, R128_Z_PITCH_C, 8);
    ring_packet0_one(&ring, R128_Z_STEN_CNTL_C, 7U << 4);
    ring_packet0_one(&ring, R128_TEX_CNTL_C, 0);
    ring_packet0_one(&ring, R128_MISC_3D_STATE_CNTL_REG, 7U << 24);
    ring_packet0_one(&ring, R128_PLANE_3D_MASK_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PM4_VC_FPU_SETUP, vc_setup);
    {
        const uint32_t draw[] = {
            GART_VIRT + 0x1000,
            3,
            R128_VC_FRMT_DIFFUSE_ARGB,
            vc_cntl,
        };
        ring_packet3(&ring, R128_PM4_3D_RNDR_GEN_INDX_PRIM,
                     draw, G_N_ELEMENTS(draw));
    }
    execute_ring(test, &ring);

    pixel = framebuffer_read(test, 20, 20);
    red = (pixel >> 16) & 0xff;
    green = (pixel >> 8) & 0xff;
    blue = pixel & 0xff;
    g_assert_cmpuint(red, >, 80);
    g_assert_cmpuint(green, >, 30);
    g_assert_cmpuint(blue, >, 30);
    g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff000000);
    rage128_pm4_stop(test);
}


static void test_pm4_indexed_triangle_indirect(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t surface = (8U << 21);
    const uint32_t clear_master =
        R128_GMC_DST_PITCH_OFFSET_CNTL |
        R128_GMC_BRUSH_SOLID_COLOR |
        R128_GMC_DST_32BPP |
        R128_GMC_SRC_DATATYPE_COLOR |
        R128_ROP3_P |
        R128_GMC_CLR_CMP_CNTL_DIS |
        R128_GMC_AUX_CLIP_DIS |
        R128_GMC_WR_MSK_DIS;
    const uint32_t clear[] = {
        clear_master, surface, 0xff000000,
        0, (64U << 16) | 64U,
    };
    uint32_t vertices[] = {
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(0.25f)),
        cpu_to_le32(0xff0000ff),
        cpu_to_le32(float_bits(56.0f)),
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(0.25f)),
        cpu_to_le32(0xff00ff00),
        cpu_to_le32(float_bits(8.0f)),
        cpu_to_le32(float_bits(56.0f)),
        cpu_to_le32(float_bits(0.25f)),
        cpu_to_le32(0xffff0000),
    };
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t vc_cntl =
        R128_VC_PRIM_TRI_LIST | R128_VC_PRIM_WALK_IND | (3U << 16);
    uint32_t indirect[] = {
        cpu_to_le32(R128_PM4_PACKET3 |
                    R128_PM4_3D_RNDR_GEN_INDX_PRIM | (5U << 16)),
        cpu_to_le32(GART_VIRT + 0x1000),
        cpu_to_le32(0xffff),
        cpu_to_le32(R128_VC_FRMT_DIFFUSE_ARGB),
        cpu_to_le32(vc_cntl),
        cpu_to_le32(0x00010000), /* indices 0, 1 */
        cpu_to_le32(0x00000002), /* index 2, padding */
        cpu_to_le32(R128_PM4_PACKET2),
    };
    const uint32_t indirect_regs[] = {
        GART_VIRT + 0x3000,
        G_N_ELEMENTS(indirect),
    };
    uint32_t pixel;

    qtest_memwrite(test->qts, VERTEX_PHYS, vertices, sizeof(vertices));
    qtest_memwrite(test->qts, INDIRECT_PHYS, indirect, sizeof(indirect));
    load_microcode(test);
    setup_gart(test);

    ring_packet0_one(&ring, R128_DP_CNTL, 3);
    ring_packet0_one(&ring, R128_DP_WRITE_MASK, UINT32_MAX);
    ring_packet3(&ring, R128_PM4_CNTL_PAINT_MULTI,
                 clear, G_N_ELEMENTS(clear));
    ring_packet0_one(&ring, R128_DST_PITCH_OFFSET_C, surface);
    ring_packet0_one(&ring, R128_DP_GUI_MASTER_CNTL_C,
                     R128_GMC_DST_32BPP);
    ring_packet0_one(&ring, R128_SC_TOP_LEFT_C, 0);
    ring_packet0_one(&ring, R128_SC_BOTTOM_RIGHT_C,
                     (63U << 16) | 63U);
    ring_packet0_one(&ring, R128_Z_OFFSET_C, 0);
    ring_packet0_one(&ring, R128_Z_PITCH_C, 8);
    ring_packet0_one(&ring, R128_Z_STEN_CNTL_C, 7U << 4);
    ring_packet0_one(&ring, R128_TEX_CNTL_C, 0);
    ring_packet0_one(&ring, R128_MISC_3D_STATE_CNTL_REG, 7U << 24);
    ring_packet0_one(&ring, R128_PLANE_3D_MASK_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PM4_VC_FPU_SETUP, vc_setup);
    ring_packet0(&ring, R128_PM4_IW_INDOFF,
                 indirect_regs, G_N_ELEMENTS(indirect_regs));
    execute_ring(test, &ring);

    pixel = framebuffer_read(test, 20, 20);
    g_assert_cmpuint((pixel >> 16) & 0xff, >, 80);
    g_assert_cmpuint((pixel >> 8) & 0xff, >, 30);
    g_assert_cmpuint(pixel & 0xff, >, 30);
    g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff000000);
    rage128_pm4_stop(test);
}

static void test_pm4_fixed_function_state(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t vc_solid =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t vc_cull_front =
        (1U << 0) | (3U << 1) | (0U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    uint32_t colors[3];
    uint32_t pixel;

    load_microcode(test);
    setup_gart(test);

    /* A farther triangle fails LESS; a nearer triangle updates color and Z. */
    {
        RingBuilder ring = { 0 };
        float far_triangle[3][3];

        memcpy(far_triangle, triangle, sizeof(far_triangle));
        for (unsigned int i = 0; i < 3; i++) {
            far_triangle[i][2] = 200.0f;
            colors[i] = 0xff0000ff;
        }
        write_vertices(test, 0, far_triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_clear_surface(&ring, DEPTH_OFFSET, true, 100);
        ring_set_3d_state(&ring,
                          R128_TEX_Z_ENABLE | R128_TEX_Z_WRITE_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_solid);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
        g_assert_cmpuint(vram_read32(test, DEPTH_OFFSET +
                                    (20U * 64 + 20U) * 4), ==, 100);
    }
    {
        RingBuilder ring = { 0 };
        float near_triangle[3][3];

        memcpy(near_triangle, triangle, sizeof(near_triangle));
        for (unsigned int i = 0; i < 3; i++) {
            near_triangle[i][2] = 50.0f;
            colors[i] = 0xff00ff00;
        }
        write_vertices(test, 0, near_triangle, colors, 3);
        ring_set_3d_state(&ring,
                          R128_TEX_Z_ENABLE | R128_TEX_Z_WRITE_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_solid);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff00ff00);
        g_assert_cmpuint(vram_read32(test, DEPTH_OFFSET +
                                    (20U * 64 + 20U) * 4), ==, 50);
    }

    /* Alpha test and conventional src-alpha blending. */
    {
        RingBuilder ring = { 0 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0x400000ff;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEX_ALPHA_TEST_ENABLE,
                          R128_ALPHA_TEST_GREATER | 128U,
                          UINT32_MAX, vc_solid);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
    }
    {
        RingBuilder ring = { 0 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0x800000ff;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff0000ff);
        ring_set_3d_state(&ring, R128_TEX_ALPHA_ENABLE,
                          R128_ALPHA_BLEND_SRC_SRCALPHA |
                          R128_ALPHA_BLEND_DST_INVSRCALPHA |
                          R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_solid);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        pixel = framebuffer_read(test, 20, 20);
        g_assert_cmpuint((pixel >> 16) & 0xff, >=, 127);
        g_assert_cmpuint((pixel >> 16) & 0xff, <=, 129);
        g_assert_cmpuint(pixel & 0xff, >=, 126);
        g_assert_cmpuint(pixel & 0xff, <=, 128);
    }

    /* Plane masks and face culling are enforced by the software backend. */
    {
        RingBuilder ring = { 0 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0xffccbbaa;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff112233);
        ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                          0x00ff0000, vc_solid);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xffaa2233);
    }
    {
        RingBuilder ring = { 0 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0xff0000ff;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_cull_front);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
    }

    /* Aux clipping and non-triangle fragment processing. */
    {
        RingBuilder ring = { 0 };
        const float point[1][3] = { { 20.0f, 20.0f, 0.0f } };
        const float line[2][3] = {
            { 10.0f, 30.0f, 0.0f }, { 30.0f, 30.0f, 0.0f },
        };
        const uint32_t point_color[1] = { 0x400000ff };
        const uint32_t line_color[2] = { 0xc800ff00, 0xc800ff00 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0xff0000ff;
        }
        write_vertices(test, 0, triangle, colors, 3);
        write_vertices(test, 0x100, point, point_color, 1);
        write_vertices(test, 0x200, line, line_color, 2);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_solid);
        ring_packet0_one(&ring, R128_AUX1_SC_LEFT, 0);
        ring_packet0_one(&ring, R128_AUX1_SC_RIGHT, 15);
        ring_packet0_one(&ring, R128_AUX1_SC_TOP, 0);
        ring_packet0_one(&ring, R128_AUX1_SC_BOTTOM, 15);
        ring_packet0_one(&ring, R128_AUX_SC_CNTL, 1);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        ring_packet0_one(&ring, R128_AUX_SC_CNTL, 0);
        ring_set_3d_state(&ring, R128_TEX_ALPHA_TEST_ENABLE,
                          R128_ALPHA_TEST_GREATER | 128U,
                          UINT32_MAX, vc_solid);
        ring_draw(&ring, 0x100, 1, R128_VC_PRIM_POINT);
        ring_draw(&ring, 0x200, 2, R128_VC_PRIM_LINE);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 10, 10), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
        g_assert_cmphex(framebuffer_read(test, 20, 30), ==, 0xc800ff00);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_primary_texture(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_control =
        (6U << 16) |                 /* ARGB8888 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t texture_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const float vertices[6][4] = {
        { 8.0f,  8.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
        { 56.0f, 56.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
    };
    const float st[6][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
        { 1.0f, 0.0f }, { 1.0f, 1.0f }, { 0.0f, 1.0f },
    };
    const uint32_t colors[6] = {
        UINT32_MAX, UINT32_MAX, UINT32_MAX,
        UINT32_MAX, UINT32_MAX, UINT32_MAX,
    };

    /* Row-major 2x2 ARGB8888 texture: red, green, blue, white. */
    vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
    vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
    vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);
    write_textured_vertices(test, 0, vertices, colors, st,
                            G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
    ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
    ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
    ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, texture_control);
    ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                     texture_combine);
    ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
    ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
    ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                     R128_VC_PRIM_TRI_LIST, format);
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 18, 18), ==, 0xffff0000);
    g_assert_cmphex(framebuffer_read(test, 44, 18), ==, 0xff00ff00);
    g_assert_cmphex(framebuffer_read(test, 18, 44), ==, 0xff0000ff);
    g_assert_cmphex(framebuffer_read(test, 44, 44), ==, 0xffffffff);
    g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff000000);
    rage128_pm4_stop(test);
}

static void test_pm4_lod_bias_and_pro_blend(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t modulate =
        3U |                         /* color modulate */
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (3U << 14) |                 /* alpha modulate */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const uint32_t blend_color =
        4U |                         /* Pro/M3 extended MODULATE2X */
        R128_COMB_FCN_MSB |
        (0U << 4) |                  /* color factor: constant color */
        (4U << 10) |                 /* color input: interpolated */
        (2U << 14) |                 /* alpha copy input */
        (6U << 18) |
        (2U << 25);
    const float point[1][4] = {
        { 20.0f, 20.0f, 0.0f, 1.0f },
    };
    const float st[1][2] = {
        { 0.25f, 0.25f },
    };
    const uint32_t white[1] = { UINT32_MAX };

    load_microcode(test);
    setup_gart(test);

    /*
     * Offset 1 is the 2x2 base level and offset 0 the 1x1 tail level.
     * Neutral bias selects green level zero; Mesa's maximum positive
     * 0x80 bucket pushes the same point into the red tail level.
     */
    vram_write32(test, TEXTURE_OFFSET, 0xffff0000);
    for (unsigned int i = 0; i < 4; i++) {
        vram_write32(test, TEXTURE_OFFSET + 32 + i * 4, 0xff00ff00);
    }
    write_textured_vertices(test, 0, point, white, st, 1);
    for (unsigned int mode = 0; mode < 2; mode++) {
        RingBuilder ring = { 0 };
        uint32_t bias = mode ? 0x80U : 0x3fU;

        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_TEX_CNTL_C,
                         R128_TEXMAP_ENABLE | (bias << 24));
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x0111);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C,
                         (6U << 16) | (2U << 1));
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C, modulate);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_PRIM_TEX_1_OFFSET_C,
                         TEXTURE_OFFSET + 32);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
        ring_draw_format(&ring, 0, 1, R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                        mode ? 0xffff0000 : 0xff00ff00);
    }

    /*
     * The Pro/M3 FCN_MSB equation is Cf(1-Ct)+CcCt.  Exercise all three
     * color inputs with values chosen to make an accidental MODULATE2X
     * interpretation visibly different.
     */
    {
        RingBuilder ring = { 0 };
        const uint32_t vertex_color[1] = { 0xff3264c8 };

        vram_write32(test, TEXTURE_OFFSET, 0xff8040c0);
        write_textured_vertices(test, 0, point, vertex_color, st, 1);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C,
                         (6U << 16) | (1U << 7));
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         blend_color);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, 0xff14dc64);
        ring_draw_format(&ring, 0, 1, R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff6e8258);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_packed_yuv_textures(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const float vertices[4][4] = {
        { 10.0f, 10.0f, 0.0f, 1.0f },
        { 20.0f, 10.0f, 0.0f, 1.0f },
        { 10.0f, 20.0f, 0.0f, 1.0f },
        { 20.0f, 20.0f, 0.0f, 1.0f },
    };
    const float st[4][2] = {
        { 0.25f, 0.25f }, { 0.75f, 0.25f },
        { 0.25f, 0.75f }, { 0.75f, 0.75f },
    };
    const uint32_t colors[4] = {
        UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX,
    };
    const uint32_t controls[2] = {
        (11U << 16) | (1U << 7),    /* YVYU422, no mipmaps */
        (12U << 16) | (1U << 7),    /* VYUY422, no mipmaps */
    };
    const uint32_t packed[2][2] = {
        {
            0xeb801080, /* Cb=128,Y0=16,Cr=128,Y1=235 */
            0x51f0515a, /* Cb=90,Y0=81,Cr=240,Y1=81 */
        },
        {
            0x80eb8010, /* Y0=16,Cr=128,Y1=235,Cb=128 */
            0x5a51f051, /* Y0=81,Cr=240,Y1=81,Cb=90 */
        },
    };

    write_textured_vertices(test, 0, vertices, colors, st,
                            G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    for (unsigned int mode = 0; mode < G_N_ELEMENTS(controls); mode++) {
        RingBuilder ring = { 0 };

        vram_write32(test, TEXTURE_OFFSET + 0, packed[mode][0]);
        vram_write32(test, TEXTURE_OFFSET + 4, packed[mode][1]);
        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, controls[mode]);
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
        ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
        ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                         R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        g_assert_cmphex(framebuffer_read(test, 10, 10), ==, 0xff000000);
        g_assert_cmphex(framebuffer_read(test, 20, 10), ==, 0xffffffff);
        g_assert_cmphex(framebuffer_read(test, 10, 20), ==, 0xfffe0000);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xfffe0000);
        g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff123456);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_ayuv444_texture(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_control =
        (14U << 16) |                /* AYUV444 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t texture_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const float vertices[4][4] = {
        { 10.0f, 10.0f, 0.0f, 1.0f },
        { 20.0f, 10.0f, 0.0f, 1.0f },
        { 10.0f, 20.0f, 0.0f, 1.0f },
        { 20.0f, 20.0f, 0.0f, 1.0f },
    };
    const float st[4][2] = {
        { 0.25f, 0.25f }, { 0.75f, 0.25f },
        { 0.25f, 0.75f }, { 0.75f, 0.75f },
    };
    const uint32_t colors[4] = {
        UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX,
    };

    /* A:Y:Cb:Cr texels: black, white, red, and a blue-biased sample. */
    vram_write32(test, TEXTURE_OFFSET + 0, 0x20108080);
    vram_write32(test, TEXTURE_OFFSET + 4, 0x80eb8080);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xc0515af0);
    vram_write32(test, TEXTURE_OFFSET + 12, 0x4091f05a);
    write_textured_vertices(test, 0, vertices, colors, st,
                            G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    ring_clear_surface(&ring, 0, false, 0xff123456);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
    ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
    ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
    ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, texture_control);
    ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                     texture_combine);
    ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
    ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
    ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                     R128_VC_PRIM_POINT, format);
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 10, 10), ==, 0x20000000);
    g_assert_cmphex(framebuffer_read(test, 20, 10), ==, 0x80ffffff);
    g_assert_cmphex(framebuffer_read(test, 10, 20), ==, 0xc0fe0000);
    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0x405a89ff);
    g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff123456);
    rage128_pm4_stop(test);
}

static void test_pm4_texture_chroma_key(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_control =
        (6U << 16) |                /* ARGB8888 */
        (1U << 7);                  /* disable mip mapping */
    const uint32_t texture_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const float vertices[4][4] = {
        { 10.0f, 10.0f, 50.0f, 1.0f },
        { 20.0f, 10.0f, 50.0f, 1.0f },
        { 10.0f, 20.0f, 50.0f, 1.0f },
        { 20.0f, 20.0f, 50.0f, 1.0f },
    };
    const float st[4][2] = {
        { 0.25f, 0.25f }, { 0.75f, 0.25f },
        { 0.25f, 0.75f }, { 0.75f, 0.75f },
    };
    const uint32_t colors[4] = {
        UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX,
    };
    const uint32_t texels[4] = {
        0xff00ff00, 0x8000ff00, 0xffff0000, 0xff0000ff,
    };
    const uint32_t functions[4] = {
        R128_CLR_CMP_FCN_ALWAYS,
        R128_CLR_CMP_FCN_NEVER,
        R128_CLR_CMP_FCN_EQUAL,
        R128_CLR_CMP_FCN_NEQUAL,
    };
    const bool accepted[4][4] = {
        { true, true, true, true },
        { false, false, false, false },
        { true, true, false, false },
        { false, false, true, true },
    };
    const unsigned int xy[4][2] = {
        { 10, 10 }, { 20, 10 }, { 10, 20 }, { 20, 20 },
    };

    for (unsigned int i = 0; i < G_N_ELEMENTS(texels); i++) {
        vram_write32(test, TEXTURE_OFFSET + i * 4, texels[i]);
    }
    write_textured_vertices(test, 0, vertices, colors, st,
                            G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    for (unsigned int mode = 0; mode < G_N_ELEMENTS(functions); mode++) {
        RingBuilder ring = { 0 };
        uint32_t tex_control = R128_TEXMAP_ENABLE |
                               R128_TEX_CHROMA_KEY_ENABLE;

        ring_clear_surface(&ring, 0, false, 0xff123456);
        if (mode == 2) {
            tex_control |= R128_TEX_Z_ENABLE | R128_TEX_Z_WRITE_ENABLE;
            ring_clear_surface(&ring, DEPTH_OFFSET, false, 100);
        }
        ring_set_3d_state(&ring, tex_control,
                          R128_ALPHA_TEST_ALWAYS | functions[mode],
                          UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, texture_control);
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
        ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
        ring_packet0_one(&ring, R128_CLR_CMP_CLR_3D, 0x0000ff00);
        ring_packet0_one(&ring, R128_CLR_CMP_MASK_3D, 0x00ffffff);
        ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                         R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        for (unsigned int i = 0; i < G_N_ELEMENTS(texels); i++) {
            uint32_t expected = accepted[mode][i] ? texels[i] :
                                                        0xff123456;

            g_assert_cmphex(framebuffer_read(test, xy[i][0], xy[i][1]),
                            ==, expected);
            if (mode == 2) {
                uint32_t depth = vram_read32(
                    test, DEPTH_OFFSET +
                          (xy[i][1] * 64U + xy[i][0]) * 4U);

                g_assert_cmpuint(depth, ==,
                                 accepted[mode][i] ? 50U : 100U);
            }
        }
        g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff123456);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_paletted_textures(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const float vertices[4][4] = {
        { 10.0f, 10.0f, 0.0f, 1.0f },
        { 20.0f, 10.0f, 0.0f, 1.0f },
        { 10.0f, 20.0f, 0.0f, 1.0f },
        { 20.0f, 20.0f, 0.0f, 1.0f },
    };
    const float st[4][2] = {
        { 0.25f, 0.25f }, { 0.75f, 0.25f },
        { 0.25f, 0.75f }, { 0.75f, 0.75f },
    };
    const uint32_t white[4] = {
        UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX,
    };
    const unsigned int xy[4][2] = {
        { 10, 10 }, { 20, 10 }, { 10, 20 }, { 20, 20 },
    };
    uint32_t palette1[257] = { 0 };
    uint32_t palette2[257] = { 0 };
    uint32_t palette4[17] = { 0 };

    palette1[0] = 2U | (1U << 4); /* CI8, palette 1 */
    palette1[1 + 1] = 0xffff0000;
    palette1[1 + 2] = 0xff00ff00;
    palette1[1 + 3] = 0xff0000ff;
    palette1[1 + 4] = 0xffffffff;

    palette2[0] = 2U | (2U << 4); /* CI8, palette 2 */
    palette2[1 + 1] = 0xffffff00;
    palette2[1 + 2] = 0xff00ffff;
    palette2[1 + 3] = 0xffff00ff;
    palette2[1 + 4] = 0xff000000;

    palette4[0] = 1U | (7U << 4); /* CI4, palette 7 */
    palette4[1 + 1] = 0xffffff00;
    palette4[1 + 2] = 0xff00ffff;
    palette4[1 + 3] = 0xffff00ff;
    palette4[1 + 4] = 0xff000000;

    write_textured_vertices(test, 0, vertices, white, st,
                            G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    /* CI8 selects the second independently loaded 256-entry palette. */
    {
        RingBuilder ring = { 0 };
        const uint32_t expected[4] = {
            0xffffff00, 0xff00ffff, 0xffff00ff, 0xff000000,
        };

        for (unsigned int i = 0; i < 4; i++) {
            vram_write8(test, TEXTURE_OFFSET + i, i + 1);
        }
        ring_packet3(&ring, R128_PM4_CNTL_LOAD_PALETTE,
                     palette1, G_N_ELEMENTS(palette1));
        ring_packet3(&ring, R128_PM4_CNTL_LOAD_PALETTE,
                     palette2, G_N_ELEMENTS(palette2));
        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C,
                         R128_TEX_FMT_CI8 | R128_TEX_PALETTE(2) |
                         (1U << 7));
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
        ring_draw_format(&ring, 0, 4, R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        for (unsigned int i = 0; i < 4; i++) {
            g_assert_cmphex(framebuffer_read(test, xy[i][0], xy[i][1]),
                            ==, expected[i]);
        }
    }

    /* CI4 consumes the low nibble and selects one of sixteen palettes. */
    {
        RingBuilder ring = { 0 };
        const uint8_t texels[4] = { 0xa1, 0xb2, 0xc3, 0xd4 };
        const uint32_t expected[4] = {
            0xffffff00, 0xff00ffff, 0xffff00ff, 0xff000000,
        };

        for (unsigned int i = 0; i < 4; i++) {
            vram_write8(test, TEXTURE_OFFSET + i, texels[i]);
        }
        ring_packet3(&ring, R128_PM4_CNTL_LOAD_PALETTE,
                     palette4, G_N_ELEMENTS(palette4));
        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C,
                         R128_TEX_FMT_CI4 | R128_TEX_PALETTE(7) |
                         (1U << 7));
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
        ring_draw_format(&ring, 0, 4, R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        for (unsigned int i = 0; i < 4; i++) {
            g_assert_cmphex(framebuffer_read(test, xy[i][0], xy[i][1]),
                            ==, expected[i]);
        }
    }

    /* CI16 uses an eight-bit palette index plus an independent alpha byte. */
    {
        RingBuilder ring = { 0 };
        const uint16_t texels[4] = {
            0x2001, 0x8002, 0xc003, 0xff04,
        };
        const uint32_t expected[4] = {
            0x20ff0000, 0x8000ff00, 0xc00000ff, 0xffffffff,
        };

        for (unsigned int i = 0; i < 4; i++) {
            vram_write16(test, TEXTURE_OFFSET + i * 2, texels[i]);
        }
        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C,
                         R128_TEX_FMT_CI16 | R128_TEX_PALETTE(1) |
                         (1U << 7));
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
        ring_draw_format(&ring, 0, 4, R128_VC_PRIM_POINT, format);
        execute_ring(test, &ring);

        for (unsigned int i = 0; i < 4; i++) {
            g_assert_cmphex(framebuffer_read(test, xy[i][0], xy[i][1]),
                            ==, expected[i]);
        }
    }

    rage128_pm4_stop(test);

    /* Invalid entry counts fault in place and suppress later commands. */
    test = rage128_pm4_start();
    {
        RingBuilder ring = { 0 };
        const uint32_t short_palette[] = {
            2U | (1U << 4), 0xffff0000,
        };

        load_microcode(test);
        setup_gart(test);
        mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
        ring_packet3(&ring, R128_PM4_CNTL_LOAD_PALETTE,
                     short_palette, G_N_ELEMENTS(short_palette));
        ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);
        execute_faulting_ring(test, &ring,
                              G_N_ELEMENTS(short_palette));
        g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    }
    rage128_pm4_stop(test);
}

static void test_pm4_texture_lighting(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_control =
        (6U << 16) |                 /* ARGB8888 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t texture_combine =
        (4U << 4) |                  /* disabled color passes texture */
        (6U << 18);                  /* disabled alpha passes texture */
    const float vertices[3][4] = {
        { 8.0f,  8.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
    };
    const float st[3][2] = {
        { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
    };
    const uint32_t colors[3] = {
        0x80204080, 0x80204080, 0x80204080,
    };
    static const struct {
        unsigned int function;
        uint32_t expected;
    } color_cases[] = {
        { 0,  0x404080c0 },
        { 1,  0x404080c0 },
        { 2,  0x40804020 },
        { 3,  0x40202018 },
        { 4,  0x40404030 },
        { 5,  0x40818160 },
        { 6,  0x40c0c0e0 },
        { 7,  0x40404060 },
        { 8,  0x40606070 },
        { 9,  0x40705048 },
        { 10, 0x40507098 },
        { 12, 0x40705048 },
        { 14, 0x408080c0 },
        { 15, 0x407860ad },
    };
    static const uint32_t alpha_expected[] = {
        0x404080c0,
        0x404080c0,
        0x804080c0,
        0x204080c0,
        0x404080c0,
        0x814080c0,
        0xc04080c0,
        0x404080c0,
    };

    vram_write32(test, TEXTURE_OFFSET, 0x404080c0);
    write_textured_vertices(test, 0, vertices, colors, st,
                            G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    /*
     * Texture lighting runs after the complete texture cascade. Its first
     * input is the texture result and its second input is interpolated RGBA.
     */
    for (unsigned int i = 0; i < G_N_ELEMENTS(color_cases); i++) {
        RingBuilder ring = { 0 };

        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(
            &ring,
            R128_TEXMAP_ENABLE |
            R128_TEX_LIGHT_FN(color_cases[i].function),
            R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, texture_control);
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, 0xc02080e0);
        ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                         R128_VC_PRIM_TRI_LIST, format);
        execute_ring(test, &ring);

        g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                        color_cases[i].expected);
        g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff123456);
    }

    for (unsigned int function = 0;
         function < G_N_ELEMENTS(alpha_expected); function++) {
        RingBuilder ring = { 0 };

        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(
            &ring,
            R128_TEXMAP_ENABLE | R128_ALPHA_LIGHT_FN(function),
            R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
        ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0);
        ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, texture_control);
        ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                         texture_combine);
        ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
        ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, 0xc02080e0);
        ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                         R128_VC_PRIM_TRI_LIST, format);
        execute_ring(test, &ring);

        g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                        alpha_expected[function]);
        g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff123456);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_dual_texture(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_DIFFUSE_ARGB |
                            R128_VC_FRMT_ST |
                            R128_VC_FRMT_S2T2;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t primary_control =
        (6U << 16) |                 /* ARGB8888 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t secondary_control =
        1U |                         /* use secondary S/T */
        (9U << 16) |                 /* RGB8 / A8 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t primary_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const uint32_t secondary_combine =
        3U |                         /* color modulate */
        (6U << 4) |                  /* color factor: texture alpha */
        (8U << 10) |                 /* color input: previous */
        (3U << 14) |                 /* alpha modulate */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (4U << 25);                  /* alpha input: previous */
    const float vertices[6][4] = {
        { 8.0f,  8.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
        { 56.0f, 56.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
    };
    const float st0[6][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
        { 1.0f, 0.0f }, { 1.0f, 1.0f }, { 0.0f, 1.0f },
    };
    const float st1[6][2] = {
        { 1.0f, 0.0f }, { 0.0f, 0.0f }, { 1.0f, 1.0f },
        { 0.0f, 0.0f }, { 0.0f, 1.0f }, { 1.0f, 1.0f },
    };
    const uint32_t colors[6] = {
        UINT32_MAX, UINT32_MAX, UINT32_MAX,
        UINT32_MAX, UINT32_MAX, UINT32_MAX,
    };

    /* Primary color quadrants. */
    vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
    vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
    vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);
    /* Secondary A8 mask, sampled with horizontally reversed S. */
    vram_write8(test, TEXTURE1_OFFSET + 0, 0xff);
    vram_write8(test, TEXTURE1_OFFSET + 1, 0x80);
    vram_write8(test, TEXTURE1_OFFSET + 2, 0x40);
    vram_write8(test, TEXTURE1_OFFSET + 3, 0x00);
    write_dual_textured_vertices(test, 0, vertices, colors, st0, st1,
                                 G_N_ELEMENTS(vertices));
    load_microcode(test);
    setup_gart(test);

    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring,
                      R128_TEXMAP_ENABLE | R128_SEC_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
    ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
    ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x11111111);
    ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, primary_control);
    ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                     primary_combine);
    ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
    ring_packet0_one(&ring, R128_SEC_TEX_CNTL_C, secondary_control);
    ring_packet0_one(&ring, R128_SEC_TEX_COMBINE_CNTL_C,
                     secondary_combine);
    ring_packet0_one(&ring, R128_SEC_TEX_0_OFFSET_C, TEXTURE1_OFFSET);
    ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
    ring_packet0_one(&ring, R128_SEC_TEXTURE_BORDER_COLOR_C, 0);
    ring_draw_format(&ring, 0, G_N_ELEMENTS(vertices),
                     R128_VC_PRIM_TRI_LIST, format);
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 18, 18), ==, 0x80800000);
    g_assert_cmphex(framebuffer_read(test, 44, 18), ==, 0xff00ff00);
    g_assert_cmphex(framebuffer_read(test, 18, 44), ==, 0x00000000);
    g_assert_cmphex(framebuffer_read(test, 44, 44), ==, 0x40404040);
    g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff000000);
    rage128_pm4_stop(test);
}

static void test_pm4_inline_dual_texture(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t format = R128_VC_FRMT_RHW |
                            R128_VC_FRMT_ST |
                            R128_VC_FRMT_S2T2;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t primary_control =
        (6U << 16) |                 /* ARGB8888 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t secondary_control =
        1U |                         /* use secondary S/T */
        (9U << 16) |                 /* RGB8 / A8 */
        (1U << 7);                   /* disable mip mapping */
    const uint32_t primary_combine =
        (4U << 4) |                  /* color factor: texture */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (2U << 25);                  /* alpha input: interpolated */
    const uint32_t secondary_combine =
        3U |                         /* color modulate */
        (6U << 4) |                  /* color factor: texture alpha */
        (8U << 10) |                 /* color input: previous */
        (3U << 14) |                 /* alpha modulate */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (4U << 25);                  /* alpha input: previous */
    const float positions[4][4] = {
        { 8.0f,  8.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
        { 56.0f, 56.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
    };
    const float st0[4][2] = {
        { 0.0f, 0.0f }, { 0.0f, 1.0f },
        { 1.0f, 1.0f }, { 1.0f, 0.0f },
    };
    const float st1[4][2] = {
        { 1.0f, 0.0f }, { 1.0f, 1.0f },
        { 0.0f, 1.0f }, { 0.0f, 0.0f },
    };
    uint32_t vertices[4 * 8];

    for (unsigned int i = 0; i < 4; i++) {
        vertices[i * 8 + 0] = float_bits(positions[i][0]);
        vertices[i * 8 + 1] = float_bits(positions[i][1]);
        vertices[i * 8 + 2] = float_bits(positions[i][2]);
        vertices[i * 8 + 3] = float_bits(positions[i][3]);
        vertices[i * 8 + 4] = float_bits(st0[i][0]);
        vertices[i * 8 + 5] = float_bits(st0[i][1]);
        vertices[i * 8 + 6] = float_bits(st1[i][0]);
        vertices[i * 8 + 7] = float_bits(st1[i][1]);
    }

    vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
    vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
    vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);
    vram_write8(test, TEXTURE1_OFFSET + 0, 0xff);
    vram_write8(test, TEXTURE1_OFFSET + 1, 0x80);
    vram_write8(test, TEXTURE1_OFFSET + 2, 0x40);
    vram_write8(test, TEXTURE1_OFFSET + 3, 0x00);
    load_microcode(test);
    setup_gart(test);

    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring,
                      R128_TEXMAP_ENABLE | R128_SEC_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
    ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
    ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x11111111);
    ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, primary_control);
    ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                     primary_combine);
    ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
    ring_packet0_one(&ring, R128_SEC_TEX_CNTL_C, secondary_control);
    ring_packet0_one(&ring, R128_SEC_TEX_COMBINE_CNTL_C,
                     secondary_combine);
    ring_packet0_one(&ring, R128_SEC_TEX_0_OFFSET_C, TEXTURE1_OFFSET);
    ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
    ring_packet0_one(&ring, R128_SEC_TEXTURE_BORDER_COLOR_C, 0);
    ring_draw_inline(&ring, 4, R128_VC_PRIM_TRI_FAN, format,
                     vertices, G_N_ELEMENTS(vertices));
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 18, 18), ==, 0x80800000);
    g_assert_cmphex(framebuffer_read(test, 44, 18), ==, 0xff00ff00);
    g_assert_cmphex(framebuffer_read(test, 18, 44), ==, 0x00000000);
    g_assert_cmphex(framebuffer_read(test, 44, 44), ==, 0x40404040);
    g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff000000);
    rage128_pm4_stop(test);
}

static void test_pm4_a8_destination(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t format = R128_VC_FRMT_RHW | R128_VC_FRMT_ST;
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t texture_control =
        (6U << 16) |                 /* ARGB8888 */
        (1U << 7);                   /* disable mip mapping */
    /* Xorg's A8 destination path copies texture alpha into color/Y. */
    const uint32_t texture_combine =
        1U |                         /* color copy */
        (6U << 4) |                  /* color factor: texture alpha */
        (4U << 10) |                 /* color input: interpolated */
        (6U << 18) |                 /* alpha factor: texture alpha */
        (1U << 25);                  /* alpha input: constant */
    const uint32_t misc =
        R128_ALPHA_BLEND_SRC_ONE |   /* PictOpSrc */
        R128_ALPHA_TEST_ALWAYS;
    const float positions[4][4] = {
        { 8.0f,  8.0f, 0.0f, 1.0f },
        { 8.0f, 56.0f, 0.0f, 1.0f },
        { 56.0f, 56.0f, 0.0f, 1.0f },
        { 56.0f, 8.0f, 0.0f, 1.0f },
    };
    const float st[4][2] = {
        { 0.0f, 0.0f }, { 0.0f, 1.0f },
        { 1.0f, 1.0f }, { 1.0f, 0.0f },
    };
    uint32_t vertices[4 * 6];

    for (unsigned int i = 0; i < 4; i++) {
        vertices[i * 6 + 0] = float_bits(positions[i][0]);
        vertices[i * 6 + 1] = float_bits(positions[i][1]);
        vertices[i * 6 + 2] = float_bits(positions[i][2]);
        vertices[i * 6 + 3] = float_bits(positions[i][3]);
        vertices[i * 6 + 4] = float_bits(st[i][0]);
        vertices[i * 6 + 5] = float_bits(st[i][1]);
    }

    vram_write32(test, TEXTURE_OFFSET + 0, 0x20ff0000);
    vram_write32(test, TEXTURE_OFFSET + 4, 0x8000ff00);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xc00000ff);
    vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);
    vram_write8(test, 60U * 64U + 60U, 0x5a);
    load_microcode(test);
    setup_gart(test);

    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE | R128_TEX_ALPHA_ENABLE,
                      misc, UINT32_MAX, vc_gouraud);
    ring_packet0_one(&ring, R128_DP_GUI_MASTER_CNTL_C, R128_GMC_DST_Y8);
    ring_packet0_one(&ring, R128_SETUP_CNTL, 0);
    ring_packet0_one(&ring, R128_TEX_SIZE_PITCH_C, 0x1111);
    ring_packet0_one(&ring, R128_PRIM_TEX_CNTL_C, texture_control);
    ring_packet0_one(&ring, R128_PRIM_TEX_COMBINE_CNTL_C,
                     texture_combine);
    ring_packet0_one(&ring, R128_PRIM_TEX_0_OFFSET_C, TEXTURE_OFFSET);
    ring_packet0_one(&ring, R128_CONSTANT_COLOR_C, UINT32_MAX);
    ring_packet0_one(&ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, 0);
    ring_draw_inline(&ring, 4, R128_VC_PRIM_TRI_FAN, format,
                     vertices, G_N_ELEMENTS(vertices));
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read8(test, 18, 18), ==, 0x20);
    g_assert_cmphex(framebuffer_read8(test, 44, 18), ==, 0x80);
    g_assert_cmphex(framebuffer_read8(test, 18, 44), ==, 0xc0);
    g_assert_cmphex(framebuffer_read8(test, 44, 44), ==, 0xff);
    g_assert_cmphex(framebuffer_read8(test, 60, 60), ==, 0x5a);
    rage128_pm4_stop(test);
}

static void test_pm4_malformed_inline_faults(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t malformed[] = {
        R128_VC_FRMT_RHW | R128_VC_FRMT_ST,
        R128_VC_PRIM_TRI_FAN | R128_VC_PRIM_WALK_RING | (4U << 16),
        0,
    };
    unsigned int packet_start;

    load_microcode(test);
    setup_gart(test);
    ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                      UINT32_MAX, vc_setup);
    packet_start = ring.count;
    ring_packet3(&ring, R128_PM4_3D_RNDR_GEN_PRIM,
                 malformed, G_N_ELEMENTS(malformed));
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);

    execute_faulting_ring(test, &ring,
                          packet_start + G_N_ELEMENTS(malformed));
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    rage128_pm4_stop(test);
}

static void test_pm4_shading_and_coverage(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    uint32_t colors[6];

    load_microcode(test);
    setup_gart(test);

    /* Solid-color mode ignores diffuse vertices. */
    {
        RingBuilder ring = { 0 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0xff0000ff;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX,
                          (1U << 0) | (3U << 1) | (3U << 3) |
                          (0U << 5) | (1U << 8));
        ring_packet0_one(&ring, R128_SOLID_COLOR, 0xff201080);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff801020);
    }

    /* OpenGL flat shading uses the final provoking vertex. */
    {
        RingBuilder ring = { 0 };
        const uint32_t flat_colors[3] = {
            0xff0000ff, 0xff00ff00, 0xffff0000,
        };

        write_vertices(test, 0, triangle, flat_colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX,
                          (1U << 0) | (3U << 1) | (3U << 3) |
                          (1U << 5) | (1U << 8) | (1U << 14));
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff0000ff);
    }

    /* The secondary/specular RGB channel adds after primary shading. */
    {
        RingBuilder ring = { 0 };
        const uint32_t diffuse[3] = {
            0xff000040, 0xff000040, 0xff000040,
        };
        const uint32_t specular[3] = {
            0x00000020, 0x00000020, 0x00000020,
        };
        const uint32_t format = R128_VC_FRMT_DIFFUSE_ARGB |
                                R128_VC_FRMT_SPEC_FRGB;

        write_vertices_format(test, 0, triangle, diffuse, specular, 3,
                              format);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEX_SPEC_LIGHT_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST, format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff600000);
    }

    /* Polygon line mode draws edges instead of silently filling. */
    {
        RingBuilder ring = { 0 };

        for (unsigned int i = 0; i < 3; i++) {
            colors[i] = 0xff0000ff;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX,
                          (1U << 0) | (3U << 1) | (2U << 3) |
                          (2U << 5) | (1U << 8));
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
        g_assert_cmphex(framebuffer_read(test, 8, 20), ==, 0xffff0000);
    }

    /* Adjacent triangles own a shared edge exactly once (top-left rule). */
    {
        RingBuilder ring = { 0 };
        const float square[6][3] = {
            { 8.0f, 8.0f, 0.0f }, { 24.0f, 8.0f, 0.0f },
            { 8.0f, 24.0f, 0.0f }, { 24.0f, 8.0f, 0.0f },
            { 24.0f, 24.0f, 0.0f }, { 8.0f, 24.0f, 0.0f },
        };

        for (unsigned int i = 0; i < 6; i++) {
            colors[i] = 0xff010101;
        }
        write_vertices(test, 0, square, colors, 6);
        ring_clear_surface(&ring, 0, false, 0x00000000);
        ring_set_3d_state(&ring, R128_TEX_ALPHA_ENABLE,
                          R128_ALPHA_BLEND_SRC_ONE |
                          R128_ALPHA_BLEND_DST_ONE |
                          R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_gouraud);
        ring_draw(&ring, 0, 6, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 15, 16), ==, 0xff010101);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_blend_subtract_and_dither(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };

    load_microcode(test);
    setup_gart(test);

    /* Mesa exposes the hardware's clamped source-minus-destination mode. */
    {
        RingBuilder ring = { 0 };
        const uint32_t colors[3] = {
            0xffa05080, 0xffa05080, 0xffa05080,
        };

        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff204060);
        ring_set_3d_state(&ring, R128_TEX_ALPHA_ENABLE,
                          R128_ALPHA_COMB_SUB_SRC_DST_CLAMP |
                          R128_ALPHA_BLEND_SRC_ONE |
                          R128_ALPHA_BLEND_DST_ONE |
                          R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_gouraud);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0x00601040);
    }

    /* A 4x4 ordered matrix perturbs adjacent RGB565 quantization cells. */
    {
        RingBuilder ring = { 0 };
        const uint32_t colors[3] = {
            0xff808080, 0xff808080, 0xff808080,
        };

        write_vertices(test, 0, triangle, colors, 3);
        ring_set_3d_state(&ring, R128_TEX_DITHER_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_DP_GUI_MASTER_CNTL_C,
                         R128_GMC_DST_16BPP);
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read16(test, 20, 20), ==, 0x7bef);
        g_assert_cmphex(framebuffer_read16(test, 21, 20), ==, 0x8410);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_fog_and_stencil(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t vc_gouraud =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const uint32_t fog_format = R128_VC_FRMT_DIFFUSE_ARGB |
                                R128_VC_FRMT_SPEC_FRGB;
    const float base_triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t fog_colors[3] = {
        0xff0000ff, 0xff0000ff, 0xff0000ff,
    };
    const uint32_t fog_specular[3] = { 0, 0, 0 };
    uint32_t depth_stencil;

    load_microcode(test);
    setup_gart(test);

    /* A zero vertex fog factor selects the programmed fog color. */
    {
        RingBuilder ring = { 0 };

        write_vertices_format(test, 0, base_triangle, fog_colors,
                              fog_specular, 3, fog_format);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEX_FOG_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_FOG_COLOR_C, 0x000000ff);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST, fog_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff0000ff);
    }

    /*
     * Direct table fog uses interpolated z as an 8-bit table address.  Upload
     * four entries through a single ONE_REG_WR packet and verify both its
     * post-incremented index and the four resulting fog factors.
     */
    {
        RingBuilder ring = { 0 };
        const float points[4][3] = {
            { 10.0f, 10.0f, 40.0f },
            { 20.0f, 10.0f, 41.0f },
            { 10.0f, 20.0f, 42.0f },
            { 20.0f, 20.0f, 43.0f },
        };
        const uint32_t colors[4] = {
            0xff0000ff, 0xff0000ff, 0xff0000ff, 0xff0000ff,
        };
        const uint32_t factors[4] = { 0, 64, 128, 255 };

        write_vertices(test, 0, points, colors, G_N_ELEMENTS(points));
        ring_clear_surface(&ring, 0, false, 0xff123456);
        ring_set_3d_state(&ring, R128_TEX_FOG_ENABLE,
                          R128_FOG_TABLE_ENABLE | R128_ALPHA_TEST_ALWAYS,
                          UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_FOG_TABLE_INDEX, 40);
        ring_packet0_repeat(&ring, R128_FOG_TABLE_DATA, factors,
                            G_N_ELEMENTS(factors));
        ring_packet0_one(&ring, R128_FOG_COLOR_C, 0x000000ff);
        ring_draw(&ring, 0, G_N_ELEMENTS(points), R128_VC_PRIM_POINT);
        execute_ring(test, &ring);

        g_assert_cmpuint(mmio_read(test, R128_FOG_TABLE_INDEX), ==, 44);
        g_assert_cmphex(framebuffer_read(test, 10, 10), ==, 0xff0000ff);
        g_assert_cmphex(framebuffer_read(test, 20, 10), ==, 0xff4000bf);
        g_assert_cmphex(framebuffer_read(test, 10, 20), ==, 0xff80007f);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 60, 60), ==, 0xff123456);
    }

    /* Stencil pass + Z pass executes ZPASS and preserves S8 on Z writes. */
    {
        RingBuilder ring = { 0 };
        float triangle[3][3];
        const uint32_t colors[3] = {
            0xff00ff00, 0xff00ff00, 0xff00ff00,
        };

        memcpy(triangle, base_triangle, sizeof(triangle));
        for (unsigned int i = 0; i < 3; i++) {
            triangle[i][2] = 50.0f;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_clear_surface(&ring, DEPTH_OFFSET, true,
                           (0x11U << 24) | 100U);
        ring_set_3d_state(&ring,
                          R128_TEX_Z_ENABLE | R128_TEX_Z_WRITE_ENABLE |
                          R128_TEX_STENCIL_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_Z_STEN_CNTL_C,
                         R128_Z_PIX_WIDTH_24 | R128_Z_TEST_LESS |
                         R128_STENCIL_TEST_EQUAL |
                         R128_STENCIL_Z_PASS_INCREMENT);
        ring_packet0_one(&ring, R128_STEN_REF_MASK_C,
                         0x11U | (0xffU << 16) | (0xffU << 24));
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        depth_stencil = vram_read32(test, DEPTH_OFFSET +
                                    (20U * 64 + 20U) * 4);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff00ff00);
        g_assert_cmphex(depth_stencil & 0x00ffffffU, ==, 50U);
        g_assert_cmphex(depth_stencil >> 24, ==, 0x12U);
    }

    /* Stencil pass + Z failure executes ZFAIL without touching color or Z. */
    {
        RingBuilder ring = { 0 };
        float triangle[3][3];
        const uint32_t colors[3] = {
            0xff0000ff, 0xff0000ff, 0xff0000ff,
        };

        memcpy(triangle, base_triangle, sizeof(triangle));
        for (unsigned int i = 0; i < 3; i++) {
            triangle[i][2] = 200.0f;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_set_3d_state(&ring,
                          R128_TEX_Z_ENABLE | R128_TEX_Z_WRITE_ENABLE |
                          R128_TEX_STENCIL_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_Z_STEN_CNTL_C,
                         R128_Z_PIX_WIDTH_24 | R128_Z_TEST_LESS |
                         R128_STENCIL_TEST_EQUAL |
                         R128_STENCIL_Z_FAIL_DECREMENT);
        ring_packet0_one(&ring, R128_STEN_REF_MASK_C,
                         0x12U | (0xffU << 16) | (0xffU << 24));
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        depth_stencil = vram_read32(test, DEPTH_OFFSET +
                                    (20U * 64 + 20U) * 4);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff00ff00);
        g_assert_cmphex(depth_stencil & 0x00ffffffU, ==, 50U);
        g_assert_cmphex(depth_stencil >> 24, ==, 0x11U);
    }

    /* Stencil failure executes SFAIL and suppresses both depth and color. */
    {
        RingBuilder ring = { 0 };
        float triangle[3][3];
        const uint32_t colors[3] = {
            0xff0000ff, 0xff0000ff, 0xff0000ff,
        };

        memcpy(triangle, base_triangle, sizeof(triangle));
        for (unsigned int i = 0; i < 3; i++) {
            triangle[i][2] = 25.0f;
        }
        write_vertices(test, 0, triangle, colors, 3);
        ring_set_3d_state(&ring,
                          R128_TEX_Z_ENABLE | R128_TEX_Z_WRITE_ENABLE |
                          R128_TEX_STENCIL_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_gouraud);
        ring_packet0_one(&ring, R128_Z_STEN_CNTL_C,
                         R128_Z_PIX_WIDTH_24 | R128_Z_TEST_LESS |
                         R128_STENCIL_TEST_EQUAL |
                         R128_STENCIL_S_FAIL_REPLACE);
        ring_packet0_one(&ring, R128_STEN_REF_MASK_C,
                         0x44U | (0xffU << 16) | (0xffU << 24));
        ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
        execute_ring(test, &ring);
        depth_stencil = vram_read32(test, DEPTH_OFFSET +
                                    (20U * 64 + 20U) * 4);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff00ff00);
        g_assert_cmphex(depth_stencil & 0x00ffffffU, ==, 50U);
        g_assert_cmphex(depth_stencil >> 24, ==, 0x44U);
    }

    rage128_pm4_stop(test);
}

static void test_pm4_signed_window_offset(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 16.0f, 16.0f, 0.0f },
        { 48.0f, 16.0f, 0.0f },
        { 16.0f, 48.0f, 0.0f },
    };
    const uint32_t colors[3] = {
        0xff0000ff, 0xff0000ff, 0xff0000ff,
    };
    const uint32_t window_offset =
        (((uint32_t)-8 & 0xfffU) << 20) |
        (((uint32_t)-8 & 0xfffU) << 4);

    write_vertices(test, 0, triangle, colors, 3);
    load_microcode(test);
    setup_gart(test);
    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, 0, R128_ALPHA_TEST_ALWAYS,
                      UINT32_MAX, vc_setup);
    ring_packet0_one(&ring, R128_WINDOW_XY_OFFSET, window_offset);
    ring_draw(&ring, 0, 3, R128_VC_PRIM_TRI_LIST);
    execute_ring(test, &ring);

    g_assert_cmphex(framebuffer_read(test, 12, 12), ==, 0xffff0000);
    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xffff0000);
    g_assert_cmphex(framebuffer_read(test, 44, 44), ==, 0xff000000);
    rage128_pm4_stop(test);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/ati/rage128/pm4-control-and-2d",
                    test_pm4_control_and_2d_packets);
    g_test_add_func("/ati/rage128/pm4-oversized-paint-fault",
                    test_pm4_oversized_paint_faults);
    g_test_add_func("/ati/rage128/pm4-oversized-bitblt-fault",
                    test_pm4_oversized_bitblt_faults);
    g_test_add_func("/ati/rage128/pm4-soft-reset-preservation",
                    test_pm4_soft_reset_preserves_configuration);
    g_test_add_func("/ati/rage128/pm4-untextured-triangle",
                    test_pm4_untextured_triangle);
    g_test_add_func("/ati/rage128/pm4-indexed-triangle-indirect",
                    test_pm4_indexed_triangle_indirect);
    g_test_add_func("/ati/rage128/pm4-fixed-function-state",
                    test_pm4_fixed_function_state);
    g_test_add_func("/ati/rage128/pm4-primary-texture",
                    test_pm4_primary_texture);
    g_test_add_func("/ati/rage128/pm4-lod-bias-and-pro-blend",
                    test_pm4_lod_bias_and_pro_blend);
    g_test_add_func("/ati/rage128/pm4-packed-yuv-textures",
                    test_pm4_packed_yuv_textures);
    g_test_add_func("/ati/rage128/pm4-ayuv444-texture",
                    test_pm4_ayuv444_texture);
    g_test_add_func("/ati/rage128/pm4-texture-chroma-key",
                    test_pm4_texture_chroma_key);
    g_test_add_func("/ati/rage128/pm4-paletted-textures",
                    test_pm4_paletted_textures);
    g_test_add_func("/ati/rage128/pm4-texture-lighting",
                    test_pm4_texture_lighting);
    g_test_add_func("/ati/rage128/pm4-dual-texture",
                    test_pm4_dual_texture);
    g_test_add_func("/ati/rage128/pm4-inline-dual-texture",
                    test_pm4_inline_dual_texture);
    g_test_add_func("/ati/rage128/pm4-a8-destination",
                    test_pm4_a8_destination);
    g_test_add_func("/ati/rage128/pm4-malformed-inline-fault",
                    test_pm4_malformed_inline_faults);
    g_test_add_func("/ati/rage128/pm4-shading-and-coverage",
                    test_pm4_shading_and_coverage);
    g_test_add_func("/ati/rage128/pm4-blend-subtract-and-dither",
                    test_pm4_blend_subtract_and_dither);
    g_test_add_func("/ati/rage128/pm4-fog-and-stencil",
                    test_pm4_fog_and_stencil);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
                    test_pm4_signed_window_offset);
    return g_test_run();
}
