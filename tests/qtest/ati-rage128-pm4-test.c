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
#define R128_PM4_IW_INDOFF             0x0738
#define R128_PM4_IW_INDSIZE            0x073c
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
#define R128_PRIM_TEX_CNTL_C           0x1cb0
#define R128_PRIM_TEX_COMBINE_CNTL_C   0x1cb4
#define R128_TEX_SIZE_PITCH_C          0x1cb8
#define R128_PRIM_TEX_0_OFFSET_C       0x1cbc
#define R128_PRIM_TEXTURE_BORDER_COLOR_C 0x1d38
#define R128_PLANE_3D_MASK_C           0x1d44
#define R128_SOLID_COLOR               0x1bc8
#define R128_WINDOW_XY_OFFSET           0x1bcc
#define R128_AUX_SC_CNTL               0x1660
#define R128_AUX1_SC_LEFT              0x1664
#define R128_AUX1_SC_RIGHT             0x1668
#define R128_AUX1_SC_TOP               0x166c
#define R128_AUX1_SC_BOTTOM            0x1670

#define R128_PM4_PACKET0               0x00000000U
#define R128_PM4_PACKET2               0x80000000U
#define R128_PM4_PACKET3               0xc0000000U
#define R128_PM4_CNTL_HOSTDATA_BLT     0x00009400U
#define R128_PM4_CNTL_PAINT_MULTI      0x00009a00U
#define R128_PM4_CNTL_BITBLT_MULTI     0x00009b00U
#define R128_PM4_3D_RNDR_GEN_INDX_PRIM 0x00002300U

#define R128_GMC_SRC_PITCH_OFFSET_CNTL 0x00000001U
#define R128_GMC_DST_PITCH_OFFSET_CNTL 0x00000002U
#define R128_GMC_BRUSH_SOLID_COLOR     0x000000d0U
#define R128_GMC_BRUSH_NONE            0x000000f0U
#define R128_GMC_DST_32BPP             0x00000600U
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
#define R128_VC_FRMT_SPEC_FRGB         0x00000040U
#define R128_VC_FRMT_S_T               0x00000080U
#define R128_VC_PRIM_POINT             0x00000001U
#define R128_VC_PRIM_LINE              0x00000002U
#define R128_VC_PRIM_TRI_LIST          0x00000004U
#define R128_VC_PRIM_WALK_IND          0x00000010U
#define R128_VC_PRIM_WALK_LIST         0x00000020U

#define R128_TEX_Z_ENABLE              (1U << 0)
#define R128_TEX_Z_WRITE_ENABLE        (1U << 1)
#define R128_TEXMAP_ENABLE             (1U << 4)
#define R128_TEX_ALPHA_ENABLE          (1U << 9)
#define R128_TEX_ALPHA_TEST_ENABLE     (1U << 10)
#define R128_TEX_SPEC_LIGHT_ENABLE     (1U << 11)
#define R128_Z_PIX_WIDTH_32            (2U << 1)
#define R128_Z_TEST_LESS               (1U << 4)
#define R128_ALPHA_BLEND_SRC_ONE       (1U << 16)
#define R128_ALPHA_BLEND_SRC_SRCALPHA  (4U << 16)
#define R128_ALPHA_BLEND_DST_ONE       (1U << 20)
#define R128_ALPHA_BLEND_DST_INVSRCALPHA (5U << 20)
#define R128_ALPHA_TEST_GREATER        (5U << 24)
#define R128_ALPHA_TEST_ALWAYS         (7U << 24)

#define R128_PRIM_TEX_MIN_LINEAR       (1U << 1)
#define R128_PRIM_TEX_MAG_LINEAR       (1U << 4)
#define R128_PRIM_TEX_MIP_MAP_DISABLE  (1U << 7)
#define R128_PRIM_TEX_CLAMP_S_MIRROR   (1U << 8)
#define R128_PRIM_TEX_CLAMP_S_CLAMP    (2U << 8)
#define R128_PRIM_TEX_CLAMP_S_BORDER   (3U << 8)
#define R128_PRIM_TEX_CLAMP_T_MIRROR   (1U << 11)
#define R128_PRIM_TEX_FORMAT_ARGB1555  (3U << 16)
#define R128_PRIM_TEX_FORMAT_RGB565    (4U << 16)
#define R128_PRIM_TEX_FORMAT_ARGB8888  (6U << 16)
#define R128_PRIM_TEX_FORMAT_RGB8      (9U << 16)
#define R128_PRIM_TEX_FORMAT_ARGB4444  (15U << 16)

#define R128_TEX_COMB_INPUT_INTERP \
    ((4U << 10) | (2U << 25))
#define R128_TEX_COMB_REPLACE_RGBA \
    ((4U << 4) | (6U << 18) | R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_MODULATE_RGBA \
    ((3U << 0) | (4U << 4) | (3U << 14) | (6U << 18) | \
     R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_DECAL_RGBA \
    ((9U << 0) | (4U << 4) | (2U << 14) | (6U << 18) | \
     R128_TEX_COMB_INPUT_INTERP)
#define R128_TEX_COMB_ADD_RGBA \
    ((6U << 0) | (4U << 4) | (3U << 14) | (6U << 18) | \
     R128_TEX_COMB_INPUT_INTERP)

#define RING_PHYS                      0x00100000U
#define VERTEX_PHYS                    0x00110000U
#define RPTR_PHYS                      0x00120000U
#define INDIRECT_PHYS                  0x00130000U
#define GART_PHYS                      0x00180000U
#define GART_VIRT                      0x02000000U
#define DEPTH_OFFSET                   0x00010000U
#define TEXTURE_OFFSET                 0x00020000U
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

static uint32_t vram_read32(Rage128PM4Test *test, uint32_t offset)
{
    return qpci_io_readl(test->dev, test->framebuffer, offset);
}

static void vram_write32(Rage128PM4Test *test, uint32_t offset,
                         uint32_t value)
{
    qpci_io_writel(test->dev, test->framebuffer, offset, value);
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

static uint32_t texture_size_pitch(unsigned int width_log2,
                                   unsigned int height_log2)
{
    unsigned int size_log2 = MAX(width_log2, height_log2);

    return width_log2 | (size_log2 << 4) | (height_log2 << 8) |
           (size_log2 << 12);
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
    ring_packet0_one(ring, R128_TEX_CNTL_C, tex_control);
    ring_packet0_one(ring, R128_MISC_3D_STATE_CNTL_REG, misc);
    ring_packet0_one(ring, R128_PLANE_3D_MASK_C, plane_mask);
    ring_packet0_one(ring, R128_PM4_VC_FPU_SETUP, vc_setup);
}

static void ring_set_texture0_state(RingBuilder *ring, uint32_t control,
                                    uint32_t combine, uint32_t size_pitch,
                                    uint32_t offset, uint32_t border_color)
{
    ring_packet0_one(ring, R128_PRIM_TEX_CNTL_C, control);
    ring_packet0_one(ring, R128_PRIM_TEX_COMBINE_CNTL_C, combine);
    ring_packet0_one(ring, R128_TEX_SIZE_PITCH_C, size_pitch);
    ring_packet0_one(ring, R128_PRIM_TEX_0_OFFSET_C, offset);
    ring_packet0_one(ring, R128_PRIM_TEXTURE_BORDER_COLOR_C, border_color);
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

static void write_textured_vertices(Rage128PM4Test *test, uint32_t offset,
                                    const float (*xyz)[3],
                                    const float *rhw,
                                    const uint32_t *colors,
                                    const float (*st)[2],
                                    unsigned int count)
{
    const unsigned int stride = 7;
    uint32_t *vertices = g_new0(uint32_t, count * stride);

    for (unsigned int i = 0; i < count; i++) {
        vertices[i * stride + 0] = cpu_to_le32(float_bits(xyz[i][0]));
        vertices[i * stride + 1] = cpu_to_le32(float_bits(xyz[i][1]));
        vertices[i * stride + 2] = cpu_to_le32(float_bits(xyz[i][2]));
        vertices[i * stride + 3] = cpu_to_le32(float_bits(rhw[i]));
        vertices[i * stride + 4] = cpu_to_le32(colors[i]);
        vertices[i * stride + 5] = cpu_to_le32(float_bits(st[i][0]));
        vertices[i * stride + 6] = cpu_to_le32(float_bits(st[i][1]));
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


static void test_pm4_texture0_sampling(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    const uint32_t vertex_format = R128_VC_FRMT_RHW |
                                   R128_VC_FRMT_DIFFUSE_ARGB |
                                   R128_VC_FRMT_S_T;
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t white[3] = {
        0xffffffff, 0xffffffff, 0xffffffff,
    };
    const float one[3] = { 1.0f, 1.0f, 1.0f };
    const float mapped[3][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
    };

    load_microcode(test);
    setup_gart(test);
    vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
    vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
    vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
    vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);

    /* Nearest sampling and the Mesa unit-zero modulate combiner. */
    {
        RingBuilder ring = { 0 };

        write_textured_vertices(test, 0, triangle, one, white, mapped, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_MODULATE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 16, 16), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 40, 16), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 16, 40), ==, 0xff0000ff);
        g_assert_cmphex(framebuffer_read(test, 40, 40), ==, 0xff000000);
    }

    /* RHW changes the nearest texel relative to affine interpolation. */
    {
        RingBuilder ring = { 0 };
        const float perspective_rhw[3] = { 1.0f, 0.125f, 1.0f };
        const float perspective_st[3][2] = {
            { 0.0f, 0.25f }, { 1.0f, 0.25f }, { 0.0f, 0.25f },
        };

        write_textured_vertices(test, 0, triangle, perspective_rhw, white,
                                perspective_st, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_REPLACE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 32, 16), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 52, 10), ==, 0xff00ff00);
    }

    /* Bilinear filtering at the center averages all four texels. */
    {
        RingBuilder ring = { 0 };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        write_textured_vertices(test, 0, triangle, one, white, centered, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIN_LINEAR |
            R128_PRIM_TEX_MAG_LINEAR |
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_REPLACE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff808080);
    }

    /* The same sampler decodes a 16-bit RGB565 image. */
    {
        RingBuilder ring = { 0 };

        vram_write32(test, TEXTURE_OFFSET + 0, 0x07e0f800);
        vram_write32(test, TEXTURE_OFFSET + 4, 0xffff001f);
        write_textured_vertices(test, 0, triangle, one, white, mapped, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_RGB565,
            R128_TEX_COMB_MODULATE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 16, 16), ==, 0xffff0000);
        g_assert_cmphex(framebuffer_read(test, 40, 16), ==, 0xff00ff00);
        g_assert_cmphex(framebuffer_read(test, 16, 40), ==, 0xff0000ff);
    }

    /* GL_CLAMP's border-color state is handled by the software sampler. */
    {
        RingBuilder ring = { 0 };
        const float outside[3][2] = {
            { -0.25f, 0.25f }, { -0.25f, 0.25f },
            { -0.25f, 0.25f },
        };

        write_textured_vertices(test, 0, triangle, one, white, outside, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_CLAMP_S_BORDER |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_REPLACE_RGBA,
            texture_size_pitch(1, 1), TEXTURE_OFFSET, 0xff204080);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff204080);
    }


    /* Repeat, mirrored repeat, and clamp-to-edge resolve outside ST. */
    {
        static const struct {
            uint32_t clamp;
            float s;
            float t;
            uint32_t expected;
        } cases[] = {
            { 0, 1.25f, 0.25f, 0xffff0000 },
            { R128_PRIM_TEX_CLAMP_S_MIRROR,
              1.25f, 0.25f, 0xff00ff00 },
            { R128_PRIM_TEX_CLAMP_S_CLAMP,
              -0.25f, 0.25f, 0xffff0000 },
            { R128_PRIM_TEX_CLAMP_T_MIRROR,
              0.25f, 1.25f, 0xff0000ff },
        };

        vram_write32(test, TEXTURE_OFFSET + 0, 0xffff0000);
        vram_write32(test, TEXTURE_OFFSET + 4, 0xff00ff00);
        vram_write32(test, TEXTURE_OFFSET + 8, 0xff0000ff);
        vram_write32(test, TEXTURE_OFFSET + 12, 0xffffffff);
        for (unsigned int i = 0; i < G_N_ELEMENTS(cases); i++) {
            RingBuilder ring = { 0 };
            float outside[3][2] = {
                { cases[i].s, cases[i].t },
                { cases[i].s, cases[i].t },
                { cases[i].s, cases[i].t },
            };

            write_textured_vertices(test, 0, triangle, one, white,
                                    outside, 3);
            ring_clear_surface(&ring, 0, false, 0xff000000);
            ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                              R128_ALPHA_TEST_ALWAYS, UINT32_MAX,
                              vc_setup);
            ring_set_texture0_state(
                &ring,
                R128_PRIM_TEX_MIP_MAP_DISABLE | cases[i].clamp |
                R128_PRIM_TEX_FORMAT_ARGB8888,
                R128_TEX_COMB_REPLACE_RGBA,
                texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
            ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                             vertex_format);
            execute_ring(test, &ring);
            g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                            cases[i].expected);
        }
    }

    /* Packed 1555, 4444, and the RGB8/332 mode share the sampler. */
    {
        static const struct {
            uint32_t format;
            uint32_t texel;
            uint32_t expected;
        } cases[] = {
            { R128_PRIM_TEX_FORMAT_ARGB1555, 0x0000fc00, 0xffff0000 },
            { R128_PRIM_TEX_FORMAT_ARGB4444, 0x0000f0f0, 0xff00ff00 },
            { R128_PRIM_TEX_FORMAT_RGB8, 0x000000e3, 0xffff00ff },
        };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        for (unsigned int i = 0; i < G_N_ELEMENTS(cases); i++) {
            RingBuilder ring = { 0 };

            vram_write32(test, TEXTURE_OFFSET, cases[i].texel);
            write_textured_vertices(test, 0, triangle, one, white,
                                    centered, 3);
            ring_clear_surface(&ring, 0, false, 0xff000000);
            ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                              R128_ALPHA_TEST_ALWAYS, UINT32_MAX,
                              vc_setup);
            ring_set_texture0_state(
                &ring,
                R128_PRIM_TEX_MIP_MAP_DISABLE | cases[i].format,
                R128_TEX_COMB_REPLACE_RGBA,
                texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
            ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                             vertex_format);
            execute_ring(test, &ring);
            g_assert_cmphex(framebuffer_read(test, 20, 20), ==,
                            cases[i].expected);
        }
    }

    /* Mesa's GL_ADD and GL_DECAL unit-zero combiner programs. */
    {
        RingBuilder ring = { 0 };
        const uint32_t incoming[3] = {
            0xff302010, 0xff302010, 0xff302010,
        };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        vram_write32(test, TEXTURE_OFFSET, 0xff010203);
        write_textured_vertices(test, 0, triangle, one, incoming,
                                centered, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_ADD_RGBA,
            texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff112233);
    }
    {
        RingBuilder ring = { 0 };
        const uint32_t incoming[3] = {
            0xff0000ff, 0xff0000ff, 0xff0000ff,
        };
        const float centered[3][2] = {
            { 0.5f, 0.5f }, { 0.5f, 0.5f }, { 0.5f, 0.5f },
        };

        vram_write32(test, TEXTURE_OFFSET, 0x8000ff00);
        write_textured_vertices(test, 0, triangle, one, incoming,
                                centered, 3);
        ring_clear_surface(&ring, 0, false, 0xff000000);
        ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                          R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
        ring_set_texture0_state(
            &ring,
            R128_PRIM_TEX_MIP_MAP_DISABLE |
            R128_PRIM_TEX_FORMAT_ARGB8888,
            R128_TEX_COMB_DECAL_RGBA,
            texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
        ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                         vertex_format);
        execute_ring(test, &ring);
        g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff7f8000);
    }

    rage128_pm4_stop(test);
}


static void test_pm4_texture0_invalid_rhw_fault(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t vertex_format = R128_VC_FRMT_RHW |
                                   R128_VC_FRMT_DIFFUSE_ARGB |
                                   R128_VC_FRMT_S_T;
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t white[3] = {
        0xffffffff, 0xffffffff, 0xffffffff,
    };
    const float invalid_rhw[3] = { 1.0f, -1.0f, 1.0f };
    const float mapped[3][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
    };
    unsigned int draw_start;

    load_microcode(test);
    setup_gart(test);
    vram_write32(test, TEXTURE_OFFSET, 0xffffffff);
    write_textured_vertices(test, 0, triangle, invalid_rhw, white,
                            mapped, 3);
    mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
    ring_set_texture0_state(
        &ring,
        R128_PRIM_TEX_MIP_MAP_DISABLE |
        R128_PRIM_TEX_FORMAT_ARGB8888,
        R128_TEX_COMB_REPLACE_RGBA,
        texture_size_pitch(0, 0), TEXTURE_OFFSET, 0);
    draw_start = ring.count;
    ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST,
                     vertex_format);
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);

    execute_faulting_ring(test, &ring, draw_start + 4);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
    rage128_pm4_stop(test);
}

static void test_pm4_texture0_mipmap_fault(void)
{
    Rage128PM4Test *test = rage128_pm4_start();
    RingBuilder ring = { 0 };
    const uint32_t vertex_format = R128_VC_FRMT_RHW |
                                   R128_VC_FRMT_DIFFUSE_ARGB |
                                   R128_VC_FRMT_S_T;
    const uint32_t vc_setup =
        (1U << 0) | (3U << 1) | (3U << 3) | (2U << 5) | (1U << 8);
    const float triangle[3][3] = {
        { 8.0f, 8.0f, 0.0f },
        { 56.0f, 8.0f, 0.0f },
        { 8.0f, 56.0f, 0.0f },
    };
    const uint32_t white[3] = {
        0xffffffff, 0xffffffff, 0xffffffff,
    };
    const float one[3] = { 1.0f, 1.0f, 1.0f };
    const float mapped[3][2] = {
        { 0.0f, 0.0f }, { 1.0f, 0.0f }, { 0.0f, 1.0f },
    };
    unsigned int draw_start;

    load_microcode(test);
    setup_gart(test);
    vram_write32(test, TEXTURE_OFFSET, 0xffffffff);
    write_textured_vertices(test, 0, triangle, one, white, mapped, 3);
    mmio_write(test, R128_GUI_SCRATCH_REG0, 0);
    ring_clear_surface(&ring, 0, false, 0xff000000);
    ring_set_3d_state(&ring, R128_TEXMAP_ENABLE,
                      R128_ALPHA_TEST_ALWAYS, UINT32_MAX, vc_setup);
    ring_set_texture0_state(
        &ring, R128_PRIM_TEX_FORMAT_ARGB8888,
        R128_TEX_COMB_REPLACE_RGBA,
        texture_size_pitch(1, 1), TEXTURE_OFFSET, 0);
    draw_start = ring.count;
    ring_draw_format(&ring, 0, 3, R128_VC_PRIM_TRI_LIST, vertex_format);
    ring_packet0_one(&ring, R128_GUI_SCRATCH_REG0, 0x13579bdf);

    /* Dispatch fails on the final primitive payload dword, before raster. */
    execute_faulting_ring(test, &ring, draw_start + 4);
    g_assert_cmphex(mmio_read(test, R128_GUI_SCRATCH_REG0), ==, 0);
    g_assert_cmphex(framebuffer_read(test, 20, 20), ==, 0xff000000);
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
    g_test_add_func("/ati/rage128/pm4-soft-reset-preservation",
                    test_pm4_soft_reset_preserves_configuration);
    g_test_add_func("/ati/rage128/pm4-untextured-triangle",
                    test_pm4_untextured_triangle);
    g_test_add_func("/ati/rage128/pm4-indexed-triangle-indirect",
                    test_pm4_indexed_triangle_indirect);
    g_test_add_func("/ati/rage128/pm4-fixed-function-state",
                    test_pm4_fixed_function_state);
    g_test_add_func("/ati/rage128/pm4-shading-and-coverage",
                    test_pm4_shading_and_coverage);
    g_test_add_func("/ati/rage128/pm4-texture0-sampling",
                    test_pm4_texture0_sampling);
    g_test_add_func("/ati/rage128/pm4-texture0-mipmap-fault",
                    test_pm4_texture0_mipmap_fault);
    g_test_add_func("/ati/rage128/pm4-texture0-invalid-rhw-fault",
                    test_pm4_texture0_invalid_rhw_fault);
    g_test_add_func("/ati/rage128/pm4-signed-window-offset",
                    test_pm4_signed_window_offset);
    return g_test_run();
}
