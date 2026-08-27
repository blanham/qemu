/*
 * BCM2835 VideoCore IV V3D accelerator
 *
 * This first hardware slice models the architected register file, control
 * threads, interrupts, and the render-command-list packets required for a
 * deterministic clear/store operation.  Unsupported primitive and shader
 * packets stop the control thread with CTERR instead of pretending that a
 * draw completed.  Later slices can therefore add binning and QPU execution
 * without weakening the current correctness boundary.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qemu/units.h"
#include "system/memory.h"
#include "hw/core/irq.h"
#include "hw/display/bcm2835_v3d.h"
#include "migration/vmstate.h"
#include "qapi/error.h"
#include "qemu/log.h"
#include "qemu/module.h"

#define V3D_IDENT0             0x000
#define V3D_IDENT1             0x004
#define V3D_IDENT2             0x008
#define V3D_SCRATCH            0x010
#define V3D_L2CACTL            0x020
#define V3D_SLCACTL            0x024
#define V3D_INTCTL             0x030
#define V3D_INTENA             0x034
#define V3D_INTDIS             0x038

#define V3D_CT0CS              0x100
#define V3D_CT1CS              0x104
#define V3D_CTNCS(n)           (V3D_CT0CS + 4 * (n))
#define V3D_CT0EA              0x108
#define V3D_CT1EA              0x10c
#define V3D_CTNEA(n)           (V3D_CT0EA + 4 * (n))
#define V3D_CT0CA              0x110
#define V3D_CT1CA              0x114
#define V3D_CTNCA(n)           (V3D_CT0CA + 4 * (n))
#define V3D_CT00RA0            0x118
#define V3D_CT01RA0            0x11c
#define V3D_CT0LC              0x120
#define V3D_CT1LC              0x124
#define V3D_CT0PC              0x128
#define V3D_CT1PC              0x12c
#define V3D_PCS                0x130
#define V3D_BFC                0x134
#define V3D_RFC                0x138

#define V3D_BPCA               0x300
#define V3D_BPCS               0x304
#define V3D_BPOA               0x308
#define V3D_BPOS               0x30c
#define V3D_BXCF               0x310
#define V3D_SQRSV0             0x410
#define V3D_SQRSV1             0x414
#define V3D_SQCNTL             0x418
#define V3D_SRQPC              0x430
#define V3D_SRQUA              0x434
#define V3D_SRQUL              0x438
#define V3D_SRQCS              0x43c
#define V3D_VPACNTL            0x500
#define V3D_VPMBASE            0x504
#define V3D_PCTRC              0x670
#define V3D_PCTRE              0x674
#define V3D_DBGE               0xf00
#define V3D_FDBGO              0xf04
#define V3D_FDBGB              0xf08
#define V3D_FDBGR              0xf0c
#define V3D_FDBGS              0xf10
#define V3D_ERRSTAT            0xf20

#define V3D_IDENT0_VALUE       ((2u << 24) | ('D' << 16) | ('3' << 8) | 'V')
/* 12KB VPM, 16 semaphores, 2 TMUs, 12 QPUs, one slice, revision 1. */
#define V3D_IDENT1_VALUE       0xc0102c11u
#define V3D_IDENT2_VALUE       0x00000000u

#define V3D_INT_SPILLUSE       (1u << 3)
#define V3D_INT_OUTOMEM        (1u << 2)
#define V3D_INT_FLDONE         (1u << 1)
#define V3D_INT_FRDONE         (1u << 0)
#define V3D_INT_MASK           (V3D_INT_SPILLUSE | V3D_INT_OUTOMEM | \
                                V3D_INT_FLDONE | V3D_INT_FRDONE)

#define V3D_CTRSTA             (1u << 15)
#define V3D_CTSEMA_SHIFT       12
#define V3D_CTSEMA_MASK        (0x7u << V3D_CTSEMA_SHIFT)
#define V3D_CTRTSD_SHIFT       8
#define V3D_CTRTSD_MASK        (0x3u << V3D_CTRTSD_SHIFT)
#define V3D_CTRUN              (1u << 5)
#define V3D_CTSUBS             (1u << 4)
#define V3D_CTERR              (1u << 3)
#define V3D_CTMODE             (1u << 0)

#define V3D_PCS_RMBUSY         (1u << 3)
#define V3D_PCS_RMACTIVE       (1u << 2)
#define V3D_PCS_BMBUSY         (1u << 1)
#define V3D_PCS_BMACTIVE       (1u << 0)

#define VC4_PACKET_HALT                       0
#define VC4_PACKET_NOP                        1
#define VC4_PACKET_FLUSH                      4
#define VC4_PACKET_FLUSH_ALL                  5
#define VC4_PACKET_START_TILE_BINNING         6
#define VC4_PACKET_INCREMENT_SEMAPHORE        7
#define VC4_PACKET_WAIT_ON_SEMAPHORE          8
#define VC4_PACKET_BRANCH                    16
#define VC4_PACKET_BRANCH_TO_SUB_LIST        17
#define VC4_PACKET_RETURN_FROM_SUB_LIST      18
#define VC4_PACKET_STORE_MS_TILE_BUFFER      24
#define VC4_PACKET_STORE_MS_TILE_BUFFER_EOF  25
#define VC4_PACKET_STORE_FULL_RES_TILE       26
#define VC4_PACKET_LOAD_FULL_RES_TILE        27
#define VC4_PACKET_STORE_TILE_BUFFER_GENERAL 28
#define VC4_PACKET_LOAD_TILE_BUFFER_GENERAL  29
#define VC4_PACKET_GL_INDEXED_PRIMITIVE      32
#define VC4_PACKET_GL_ARRAY_PRIMITIVE        33
#define VC4_PACKET_COMPRESSED_PRIMITIVE      48
#define VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE 49
#define VC4_PACKET_PRIMITIVE_LIST_FORMAT     56
#define VC4_PACKET_GL_SHADER_STATE           64
#define VC4_PACKET_NV_SHADER_STATE           65
#define VC4_PACKET_VG_SHADER_STATE           66
#define VC4_PACKET_CONFIGURATION_BITS        96
#define VC4_PACKET_FLAT_SHADE_FLAGS          97
#define VC4_PACKET_POINT_SIZE                98
#define VC4_PACKET_LINE_WIDTH                99
#define VC4_PACKET_RHT_X_BOUNDARY           100
#define VC4_PACKET_DEPTH_OFFSET             101
#define VC4_PACKET_CLIP_WINDOW              102
#define VC4_PACKET_VIEWPORT_OFFSET          103
#define VC4_PACKET_Z_CLIPPING               104
#define VC4_PACKET_CLIPPER_XY_SCALING       105
#define VC4_PACKET_CLIPPER_Z_SCALING        106
#define VC4_PACKET_TILE_BINNING_MODE_CONFIG 112
#define VC4_PACKET_TILE_RENDERING_MODE_CONFIG 113
#define VC4_PACKET_CLEAR_COLORS             114
#define VC4_PACKET_TILE_COORDINATES         115

#define VC4_RENDER_CONFIG_MEMORY_FORMAT_MASK 0x00c0
#define VC4_RENDER_CONFIG_FORMAT_MASK        0x000c
#define VC4_RENDER_CONFIG_FORMAT_RGBA8888    0x0004
#define VC4_RENDER_CONFIG_MS_MODE_4X         0x0001
#define VC4_TILING_FORMAT_LINEAR             0

#define VC4_MAX_CONTROL_LIST_BYTES (16 * MiB)
#define VC4_MAX_CONTROL_LIST_STEPS (4 * 1024 * 1024)
#define VC4_MAX_SUB_LIST_DEPTH     2
#define VC4_MAX_RENDER_DIMENSION   4096

#define REG_INDEX(offset) ((offset) >> 2)

/* ERRSTAT bits used by this model. */
#define V3D_ERR_DMA_READ       (1u << 0)
#define V3D_ERR_DMA_WRITE      (1u << 1)
#define V3D_ERR_BAD_PACKET     (1u << 2)
#define V3D_ERR_BAD_RENDER     (1u << 3)
#define V3D_ERR_UNSUPPORTED    (1u << 4)

/* Per-list transient state.  Control lists execute synchronously. */
typedef struct VC4V3DCLState {
    uint32_t render_base;
    uint32_t clear_color[2];
    uint32_t clear_z;
    uint16_t width;
    uint16_t height;
    uint16_t render_config;
    uint8_t clear_stencil;
    uint8_t tile_x;
    uint8_t tile_y;
    bool have_render_config;
    bool have_clear_color;
    bool saw_eof;
    uint32_t main_start;
    uint32_t main_end;
    uint32_t return_pc[VC4_MAX_SUB_LIST_DEPTH];
    uint8_t sub_list_depth;
} VC4V3DCLState;

static void bcm2835_v3d_update_irq(BCM2835V3DState *s)
{
    uint32_t pending = s->regs[REG_INDEX(V3D_INTCTL)];
    uint32_t enabled = s->regs[REG_INDEX(V3D_INTENA)];

    qemu_set_irq(s->irq, (pending & enabled & V3D_INT_MASK) != 0);
}

static bool bcm2835_v3d_dma_read(BCM2835V3DState *s, hwaddr address,
                                 void *buffer, size_t size)
{
    MemTxResult result;

    result = address_space_read(&s->dma_as, address,
                                MEMTXATTRS_UNSPECIFIED, buffer, size);
    if (result != MEMTX_OK) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_DMA_READ;
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_V3D
                      ": DMA read failed at 0x%08" HWADDR_PRIx
                      " (%zu bytes)\n", address, size);
        return false;
    }
    return true;
}

static bool bcm2835_v3d_dma_write(BCM2835V3DState *s, hwaddr address,
                                  const void *buffer, size_t size)
{
    MemTxResult result;

    result = address_space_write(&s->dma_as, address,
                                 MEMTXATTRS_UNSPECIFIED, buffer, size);
    if (result != MEMTX_OK) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_DMA_WRITE;
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_V3D
                      ": DMA write failed at 0x%08" HWADDR_PRIx
                      " (%zu bytes)\n", address, size);
        return false;
    }
    return true;
}

static bool bcm2835_v3d_cl_read(BCM2835V3DState *s, uint32_t address,
                                void *buffer, size_t size)
{
    return bcm2835_v3d_dma_read(s, address, buffer, size);
}

static bool bcm2835_v3d_cl_read_u8(BCM2835V3DState *s, uint32_t address,
                                   uint8_t *value)
{
    return bcm2835_v3d_cl_read(s, address, value, sizeof(*value));
}

static bool bcm2835_v3d_cl_read_u16(BCM2835V3DState *s, uint32_t address,
                                    uint16_t *value)
{
    uint8_t bytes[2];

    if (!bcm2835_v3d_cl_read(s, address, bytes, sizeof(bytes))) {
        return false;
    }
    *value = lduw_le_p(bytes);
    return true;
}

static bool bcm2835_v3d_cl_read_u32(BCM2835V3DState *s, uint32_t address,
                                    uint32_t *value)
{
    uint8_t bytes[4];

    if (!bcm2835_v3d_cl_read(s, address, bytes, sizeof(bytes))) {
        return false;
    }
    *value = ldl_le_p(bytes);
    return true;
}

static bool bcm2835_v3d_store_clear_tile(BCM2835V3DState *s,
                                         VC4V3DCLState *cl)
{
    uint32_t format = cl->render_config & VC4_RENDER_CONFIG_FORMAT_MASK;
    uint32_t memory_format =
        (cl->render_config & VC4_RENDER_CONFIG_MEMORY_FORMAT_MASK) >> 6;
    uint32_t tile_size =
        (cl->render_config & VC4_RENDER_CONFIG_MS_MODE_4X) ? 32 : 64;
    uint32_t x0 = cl->tile_x * tile_size;
    uint32_t y0 = cl->tile_y * tile_size;
    uint32_t x1;
    uint32_t y1;
    uint32_t pitch;
    uint32_t row;
    uint32_t col;
    uint8_t *line;
    size_t line_size;

    if (!cl->have_render_config || !cl->have_clear_color ||
        cl->width == 0 || cl->height == 0 ||
        cl->width > VC4_MAX_RENDER_DIMENSION ||
        cl->height > VC4_MAX_RENDER_DIMENSION) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_RENDER;
        return false;
    }

    if (format != VC4_RENDER_CONFIG_FORMAT_RGBA8888 ||
        memory_format != VC4_TILING_FORMAT_LINEAR) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": clear-store supports linear RGBA8888 only "
                      "(config=0x%04x)\n", cl->render_config);
        return false;
    }

    if (x0 >= cl->width || y0 >= cl->height) {
        /* Hardware clips out-of-range edge tiles. */
        return true;
    }

    x1 = MIN((uint32_t)cl->width, x0 + tile_size);
    y1 = MIN((uint32_t)cl->height, y0 + tile_size);
    pitch = (uint32_t)cl->width * 4;
    line_size = (x1 - x0) * 4;
    line = g_malloc(line_size);

    for (col = 0; col < x1 - x0; col++) {
        stl_le_p(line + col * 4, cl->clear_color[0]);
    }

    for (row = y0; row < y1; row++) {
        hwaddr address = cl->render_base + (hwaddr)row * pitch + x0 * 4;

        if (!bcm2835_v3d_dma_write(s, address, line, line_size)) {
            g_free(line);
            return false;
        }
    }

    g_free(line);
    return true;
}

static unsigned bcm2835_v3d_packet_size(uint8_t packet)
{
    switch (packet) {
    case VC4_PACKET_HALT:
    case VC4_PACKET_NOP:
    case VC4_PACKET_FLUSH:
    case VC4_PACKET_FLUSH_ALL:
    case VC4_PACKET_START_TILE_BINNING:
    case VC4_PACKET_INCREMENT_SEMAPHORE:
    case VC4_PACKET_WAIT_ON_SEMAPHORE:
    case VC4_PACKET_RETURN_FROM_SUB_LIST:
    case VC4_PACKET_STORE_MS_TILE_BUFFER:
    case VC4_PACKET_STORE_MS_TILE_BUFFER_EOF:
    case VC4_PACKET_COMPRESSED_PRIMITIVE:
    case VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE:
        return 1;
    case VC4_PACKET_BRANCH:
    case VC4_PACKET_BRANCH_TO_SUB_LIST:
    case VC4_PACKET_STORE_FULL_RES_TILE:
    case VC4_PACKET_LOAD_FULL_RES_TILE:
    case VC4_PACKET_GL_SHADER_STATE:
    case VC4_PACKET_NV_SHADER_STATE:
    case VC4_PACKET_VG_SHADER_STATE:
        return 5;
    case VC4_PACKET_STORE_TILE_BUFFER_GENERAL:
    case VC4_PACKET_LOAD_TILE_BUFFER_GENERAL:
        return 7;
    case VC4_PACKET_GL_INDEXED_PRIMITIVE:
        return 14;
    case VC4_PACKET_GL_ARRAY_PRIMITIVE:
        return 10;
    case VC4_PACKET_PRIMITIVE_LIST_FORMAT:
        return 2;
    case VC4_PACKET_CONFIGURATION_BITS:
        return 4;
    case VC4_PACKET_FLAT_SHADE_FLAGS:
    case VC4_PACKET_POINT_SIZE:
    case VC4_PACKET_LINE_WIDTH:
    case VC4_PACKET_DEPTH_OFFSET:
    case VC4_PACKET_VIEWPORT_OFFSET:
        return 5;
    case VC4_PACKET_RHT_X_BOUNDARY:
    case VC4_PACKET_TILE_COORDINATES:
        return 3;
    case VC4_PACKET_CLIP_WINDOW:
    case VC4_PACKET_Z_CLIPPING:
    case VC4_PACKET_CLIPPER_XY_SCALING:
    case VC4_PACKET_CLIPPER_Z_SCALING:
        return 9;
    case VC4_PACKET_TILE_BINNING_MODE_CONFIG:
        return 16;
    case VC4_PACKET_TILE_RENDERING_MODE_CONFIG:
        return 11;
    case VC4_PACKET_CLEAR_COLORS:
        return 14;
    default:
        return 0;
    }
}

static void bcm2835_v3d_update_sub_list_state(BCM2835V3DState *s,
                                                  unsigned thread,
                                                  VC4V3DCLState *cl)
{
    uint32_t cs_index = REG_INDEX(V3D_CTNCS(thread));
    uint32_t ra_index = REG_INDEX(V3D_CT00RA0 + thread * 4);

    s->regs[cs_index] =
        (s->regs[cs_index] & ~V3D_CTRTSD_MASK) |
        ((uint32_t)cl->sub_list_depth << V3D_CTRTSD_SHIFT);
    if (cl->sub_list_depth != 0) {
        s->regs[ra_index] = cl->return_pc[cl->sub_list_depth - 1];
    } else {
        s->regs[ra_index] = 0;
    }
}

static void bcm2835_v3d_increment_list_counter(BCM2835V3DState *s,
                                                unsigned thread,
                                                bool major)
{
    uint32_t index = REG_INDEX(V3D_CT0LC + thread * 4);
    unsigned shift = major ? 16 : 0;
    uint32_t mask = 0xffffu << shift;
    uint32_t count = ((s->regs[index] & mask) >> shift) + 1;

    s->regs[index] = (s->regs[index] & ~mask) |
                     ((count & 0xffffu) << shift);
}

static bool bcm2835_v3d_packet_fits(BCM2835V3DState *s,
                                    VC4V3DCLState *cl,
                                    uint32_t pc, uint8_t packet,
                                    unsigned size)
{
    bool wraps = size == 0 || UINT32_MAX - pc < size - 1;
    bool outside_main = cl->sub_list_depth == 0 &&
        (pc < cl->main_start || pc > cl->main_end ||
         cl->main_end - pc < size);

    if (!wraps && !outside_main) {
        return true;
    }

    s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
    qemu_log_mask(LOG_GUEST_ERROR,
                  TYPE_BCM2835_V3D
                  ": invalid/truncated packet 0x%02x at 0x%08x "
                  "(sub-list depth %u)\n",
                  packet, pc, cl->sub_list_depth);
    return false;
}

static bool bcm2835_v3d_execute_packet(BCM2835V3DState *s,
                                       unsigned thread,
                                       VC4V3DCLState *cl,
                                       uint32_t pc,
                                       uint8_t packet, uint32_t *next_pc,
                                       bool *stop)
{
    unsigned size = bcm2835_v3d_packet_size(packet);
    uint32_t target;

    if (!bcm2835_v3d_packet_fits(s, cl, pc, packet, size)) {
        return false;
    }

    *next_pc = pc + size;
    *stop = false;

    switch (packet) {
    case VC4_PACKET_HALT:
        *stop = true;
        return true;
    case VC4_PACKET_NOP:
    case VC4_PACKET_START_TILE_BINNING:
    case VC4_PACKET_INCREMENT_SEMAPHORE:
    case VC4_PACKET_WAIT_ON_SEMAPHORE:
    case VC4_PACKET_PRIMITIVE_LIST_FORMAT:
    case VC4_PACKET_GL_SHADER_STATE:
    case VC4_PACKET_NV_SHADER_STATE:
    case VC4_PACKET_VG_SHADER_STATE:
    case VC4_PACKET_CONFIGURATION_BITS:
    case VC4_PACKET_FLAT_SHADE_FLAGS:
    case VC4_PACKET_POINT_SIZE:
    case VC4_PACKET_LINE_WIDTH:
    case VC4_PACKET_RHT_X_BOUNDARY:
    case VC4_PACKET_DEPTH_OFFSET:
    case VC4_PACKET_CLIP_WINDOW:
    case VC4_PACKET_VIEWPORT_OFFSET:
    case VC4_PACKET_Z_CLIPPING:
    case VC4_PACKET_CLIPPER_XY_SCALING:
    case VC4_PACKET_CLIPPER_Z_SCALING:
    case VC4_PACKET_TILE_BINNING_MODE_CONFIG:
    case VC4_PACKET_STORE_FULL_RES_TILE:
    case VC4_PACKET_LOAD_FULL_RES_TILE:
    case VC4_PACKET_STORE_TILE_BUFFER_GENERAL:
    case VC4_PACKET_LOAD_TILE_BUFFER_GENERAL:
        return true;

    case VC4_PACKET_FLUSH:
    case VC4_PACKET_FLUSH_ALL:
        bcm2835_v3d_increment_list_counter(s, thread, true);
        return true;

    case VC4_PACKET_BRANCH:
        if (!bcm2835_v3d_cl_read_u32(s, pc + 1, &target)) {
            return false;
        }
        if (cl->sub_list_depth == 0 &&
            (target < cl->main_start || target > cl->main_end)) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_V3D
                          ": main-list branch target 0x%08x outside "
                          "[0x%08x,0x%08x] at 0x%08x\n",
                          target, cl->main_start, cl->main_end, pc);
            return false;
        }
        *next_pc = target;
        return true;

    case VC4_PACKET_BRANCH_TO_SUB_LIST:
        if (cl->sub_list_depth == VC4_MAX_SUB_LIST_DEPTH) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_V3D
                          ": sub-list nesting exceeds %u at 0x%08x\n",
                          VC4_MAX_SUB_LIST_DEPTH, pc);
            return false;
        }
        if (!bcm2835_v3d_cl_read_u32(s, pc + 1, &target)) {
            return false;
        }
        cl->return_pc[cl->sub_list_depth++] = *next_pc;
        bcm2835_v3d_update_sub_list_state(s, thread, cl);
        *next_pc = target;
        return true;

    case VC4_PACKET_RETURN_FROM_SUB_LIST:
        if (cl->sub_list_depth == 0) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            qemu_log_mask(LOG_GUEST_ERROR,
                          TYPE_BCM2835_V3D
                          ": return-from-sub-list without a caller "
                          "at 0x%08x\n", pc);
            return false;
        }
        *next_pc = cl->return_pc[--cl->sub_list_depth];
        bcm2835_v3d_update_sub_list_state(s, thread, cl);
        bcm2835_v3d_increment_list_counter(s, thread, false);
        return true;

    case VC4_PACKET_TILE_RENDERING_MODE_CONFIG:
        if (thread != 1 ||
            !bcm2835_v3d_cl_read_u32(s, pc + 1, &cl->render_base) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 5, &cl->width) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 7, &cl->height) ||
            !bcm2835_v3d_cl_read_u16(s, pc + 9, &cl->render_config)) {
            return false;
        }
        cl->have_render_config = true;
        return true;

    case VC4_PACKET_CLEAR_COLORS:
        if (thread != 1 ||
            !bcm2835_v3d_cl_read_u32(s, pc + 1, &cl->clear_color[0]) ||
            !bcm2835_v3d_cl_read_u32(s, pc + 5, &cl->clear_color[1]) ||
            !bcm2835_v3d_cl_read_u32(s, pc + 9, &cl->clear_z) ||
            !bcm2835_v3d_cl_read_u8(s, pc + 13, &cl->clear_stencil)) {
            return false;
        }
        cl->have_clear_color = true;
        return true;

    case VC4_PACKET_TILE_COORDINATES:
        if (!bcm2835_v3d_cl_read_u8(s, pc + 1, &cl->tile_x) ||
            !bcm2835_v3d_cl_read_u8(s, pc + 2, &cl->tile_y)) {
            return false;
        }
        return true;

    case VC4_PACKET_STORE_MS_TILE_BUFFER:
    case VC4_PACKET_STORE_MS_TILE_BUFFER_EOF:
        if (thread != 1 || !bcm2835_v3d_store_clear_tile(s, cl)) {
            return false;
        }
        if (packet == VC4_PACKET_STORE_MS_TILE_BUFFER_EOF) {
            cl->saw_eof = true;
        }
        return true;

    case VC4_PACKET_GL_INDEXED_PRIMITIVE:
    case VC4_PACKET_GL_ARRAY_PRIMITIVE:
    case VC4_PACKET_COMPRESSED_PRIMITIVE:
    case VC4_PACKET_CLIPPED_COMPRESSED_PRIMITIVE:
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_UNSUPPORTED;
        qemu_log_mask(LOG_UNIMP,
                      TYPE_BCM2835_V3D
                      ": packet 0x%02x requires binning/QPU execution "
                      "at 0x%08x\n", packet, pc);
        return false;

    default:
        g_assert_not_reached();
    }
}

static void bcm2835_v3d_complete_thread(BCM2835V3DState *s,
                                         unsigned thread, bool success,
                                         bool halted)
{
    uint32_t cs_index = REG_INDEX(V3D_CTNCS(thread));
    uint32_t pcs = s->regs[REG_INDEX(V3D_PCS)];

    s->regs[cs_index] &= ~V3D_CTRUN;
    if (thread == 0) {
        pcs &= ~(V3D_PCS_BMBUSY | V3D_PCS_BMACTIVE);
    } else {
        pcs &= ~(V3D_PCS_RMBUSY | V3D_PCS_RMACTIVE);
    }
    s->regs[REG_INDEX(V3D_PCS)] = pcs;

    if (!success) {
        s->regs[cs_index] |= V3D_CTERR;
        return;
    }

    s->regs[cs_index] &= ~(V3D_CTERR | V3D_CTSUBS |
                           V3D_CTRTSD_MASK);
    if (halted) {
        s->regs[cs_index] |= V3D_CTSUBS;
    }
    s->regs[REG_INDEX(V3D_CT00RA0 + thread * 4)] = 0;
    s->regs[REG_INDEX(V3D_CTNCA(thread))] =
        s->regs[REG_INDEX(V3D_CTNEA(thread))];

    if (thread == 0) {
        s->regs[REG_INDEX(V3D_BFC)]++;
        s->regs[REG_INDEX(V3D_INTCTL)] |= V3D_INT_FLDONE;
    } else {
        s->regs[REG_INDEX(V3D_RFC)]++;
        s->regs[REG_INDEX(V3D_INTCTL)] |= V3D_INT_FRDONE;
    }
    bcm2835_v3d_update_irq(s);
}

static void bcm2835_v3d_execute_thread(BCM2835V3DState *s, unsigned thread)
{
    VC4V3DCLState cl = { 0 };
    uint32_t cs_index = REG_INDEX(V3D_CTNCS(thread));
    uint32_t pc = s->regs[REG_INDEX(V3D_CTNCA(thread))];
    uint32_t end = s->regs[REG_INDEX(V3D_CTNEA(thread))];
    uint32_t next_pc;
    uint32_t steps = 0;
    uint32_t pcs;
    bool stop;
    bool halted = false;
    bool finished = false;
    bool success = true;

    if (end < pc || end - pc > VC4_MAX_CONTROL_LIST_BYTES) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
        bcm2835_v3d_complete_thread(s, thread, false, false);
        return;
    }
    if (end == pc) {
        return;
    }

    s->regs[cs_index] |= V3D_CTRUN;
    pcs = s->regs[REG_INDEX(V3D_PCS)];
    if (thread == 0) {
        pcs |= V3D_PCS_BMBUSY | V3D_PCS_BMACTIVE;
    } else {
        pcs |= V3D_PCS_RMBUSY | V3D_PCS_RMACTIVE;
    }
    s->regs[REG_INDEX(V3D_PCS)] = pcs;

    cl.main_start = pc;
    cl.main_end = end;

    while (steps < VC4_MAX_CONTROL_LIST_STEPS) {
        uint8_t packet;

        if (cl.sub_list_depth == 0 && pc == end) {
            finished = true;
            break;
        }
        if (cl.sub_list_depth == 0 &&
            (pc < cl.main_start || pc > cl.main_end)) {
            s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
            success = false;
            break;
        }

        steps++;
        s->regs[REG_INDEX(V3D_CTNCA(thread))] = pc;
        if (!bcm2835_v3d_cl_read_u8(s, pc, &packet) ||
            !bcm2835_v3d_execute_packet(s, thread, &cl, pc,
                                        packet, &next_pc, &stop)) {
            success = false;
            break;
        }
        pc = next_pc;
        if (stop) {
            halted = true;
            finished = true;
            break;
        }
    }

    if (success && !finished) {
        s->regs[REG_INDEX(V3D_ERRSTAT)] |= V3D_ERR_BAD_PACKET;
        qemu_log_mask(LOG_GUEST_ERROR,
                      TYPE_BCM2835_V3D
                      ": control-list step limit exceeded at 0x%08x\n",
                      pc);
        success = false;
    }

    bcm2835_v3d_complete_thread(s, thread, success, halted);
}

static uint64_t bcm2835_v3d_read(void *opaque, hwaddr addr, unsigned size)
{
    BCM2835V3DState *s = BCM2835_V3D(opaque);
    unsigned index = REG_INDEX(addr);

    switch (addr) {
    case V3D_IDENT0:
        return V3D_IDENT0_VALUE;
    case V3D_IDENT1:
        return V3D_IDENT1_VALUE;
    case V3D_IDENT2:
        return V3D_IDENT2_VALUE;
    case V3D_INTDIS:
        return s->regs[REG_INDEX(V3D_INTENA)];
    default:
        if (index < BCM2835_V3D_REG_WORDS) {
            return s->regs[index];
        }
        return 0;
    }
}

static void bcm2835_v3d_reset_thread(BCM2835V3DState *s, unsigned thread)
{
    s->regs[REG_INDEX(V3D_CTNCS(thread))] = 0;
    s->regs[REG_INDEX(V3D_CTNEA(thread))] = 0;
    s->regs[REG_INDEX(V3D_CTNCA(thread))] = 0;
    s->regs[REG_INDEX(V3D_CT00RA0 + thread * 4)] = 0;
    s->regs[REG_INDEX(V3D_CT0LC + thread * 4)] = 0;
    s->regs[REG_INDEX(V3D_CT0PC + thread * 4)] = 0;
}

static void bcm2835_v3d_write(void *opaque, hwaddr addr,
                              uint64_t value, unsigned size)
{
    BCM2835V3DState *s = BCM2835_V3D(opaque);
    uint32_t v = value;
    unsigned index = REG_INDEX(addr);
    unsigned thread;

    switch (addr) {
    case V3D_IDENT0:
    case V3D_IDENT1:
    case V3D_IDENT2:
        return;
    case V3D_INTCTL:
        s->regs[REG_INDEX(V3D_INTCTL)] &= ~(v & V3D_INT_MASK);
        bcm2835_v3d_update_irq(s);
        return;
    case V3D_INTENA:
        s->regs[REG_INDEX(V3D_INTENA)] |= v & V3D_INT_MASK;
        bcm2835_v3d_update_irq(s);
        return;
    case V3D_INTDIS:
        s->regs[REG_INDEX(V3D_INTENA)] &= ~(v & V3D_INT_MASK);
        bcm2835_v3d_update_irq(s);
        return;
    case V3D_L2CACTL:
        /* Cache clear is synchronous; retain only enable/disable controls. */
        s->regs[index] = v & 0x3;
        return;
    case V3D_SLCACTL:
        /* Slice-cache clear operations complete synchronously. */
        s->regs[index] = 0;
        return;
    case V3D_CT0CS:
    case V3D_CT1CS:
        thread = (addr - V3D_CT0CS) >> 2;
        if (v & V3D_CTRSTA) {
            bcm2835_v3d_reset_thread(s, thread);
            return;
        }
        if (v & V3D_CTERR) {
            s->regs[index] &= ~V3D_CTERR;
        }
        s->regs[index] =
            (s->regs[index] & (V3D_CTRUN | V3D_CTERR |
                               V3D_CTSEMA_MASK | V3D_CTRTSD_MASK |
                               V3D_CTMODE)) |
            (v & V3D_CTSUBS);
        return;
    case V3D_CT0CA:
    case V3D_CT1CA:
        s->regs[index] = v;
        return;
    case V3D_CT0EA:
    case V3D_CT1EA:
        thread = (addr - V3D_CT0EA) >> 2;
        s->regs[index] = v;
        bcm2835_v3d_execute_thread(s, thread);
        return;
    case V3D_CT0LC:
    case V3D_CT1LC:
        if (v & 1u) {
            s->regs[index] &= 0xffff0000u;
        }
        if (v & (1u << 16)) {
            s->regs[index] &= 0x0000ffffu;
        }
        return;
    case V3D_BFC:
    case V3D_RFC:
        /* Hardware counters are writable for driver/debug reset. */
        s->regs[index] = v;
        return;
    case V3D_ERRSTAT:
        /* ERRSTAT is write-one-to-clear for the modeled sticky errors. */
        s->regs[index] &= ~v;
        return;
    default:
        if (index < BCM2835_V3D_REG_WORDS) {
            s->regs[index] = v;
        }
        return;
    }
}

static const MemoryRegionOps bcm2835_v3d_ops = {
    .read = bcm2835_v3d_read,
    .write = bcm2835_v3d_write,
    .impl.min_access_size = 4,
    .impl.max_access_size = 4,
    .valid.min_access_size = 4,
    .valid.max_access_size = 4,
    .endianness = DEVICE_LITTLE_ENDIAN,
};

static void bcm2835_v3d_reset(DeviceState *dev)
{
    BCM2835V3DState *s = BCM2835_V3D(dev);

    memset(s->regs, 0, sizeof(s->regs));
    bcm2835_v3d_update_irq(s);
}

static int bcm2835_v3d_post_load(void *opaque, int version_id)
{
    BCM2835V3DState *s = opaque;

    bcm2835_v3d_update_irq(s);
    return 0;
}

static const VMStateDescription bcm2835_v3d_vmstate = {
    .name = TYPE_BCM2835_V3D,
    .version_id = 1,
    .minimum_version_id = 1,
    .post_load = bcm2835_v3d_post_load,
    .fields = (const VMStateField[]) {
        VMSTATE_UINT32_ARRAY(regs, BCM2835V3DState,
                             BCM2835_V3D_REG_WORDS),
        VMSTATE_END_OF_LIST()
    }
};

static void bcm2835_v3d_realize(DeviceState *dev, Error **errp)
{
    BCM2835V3DState *s = BCM2835_V3D(dev);
    Object *obj;

    obj = object_property_get_link(OBJECT(dev), "dma-mr", errp);
    if (!obj) {
        return;
    }

    s->dma_mr = MEMORY_REGION(obj);
    address_space_init(&s->dma_as, s->dma_mr,
                       TYPE_BCM2835_V3D "-memory");
    bcm2835_v3d_reset(dev);
}

static void bcm2835_v3d_init(Object *obj)
{
    BCM2835V3DState *s = BCM2835_V3D(obj);

    memory_region_init_io(&s->iomem, obj, &bcm2835_v3d_ops, s,
                          TYPE_BCM2835_V3D, BCM2835_V3D_MMIO_SIZE);
    sysbus_init_mmio(SYS_BUS_DEVICE(s), &s->iomem);
    sysbus_init_irq(SYS_BUS_DEVICE(s), &s->irq);
}

static void bcm2835_v3d_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);

    dc->realize = bcm2835_v3d_realize;
    device_class_set_legacy_reset(dc, bcm2835_v3d_reset);
    dc->vmsd = &bcm2835_v3d_vmstate;
}

static const TypeInfo bcm2835_v3d_info = {
    .name = TYPE_BCM2835_V3D,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(BCM2835V3DState),
    .instance_init = bcm2835_v3d_init,
    .class_init = bcm2835_v3d_class_init,
};

static void bcm2835_v3d_register_types(void)
{
    type_register_static(&bcm2835_v3d_info);
}

type_init(bcm2835_v3d_register_types)
