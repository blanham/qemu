/*
 * QEMU ATI Rage 128 PM4/CCE emulation
 *
 * Copyright (c) 2026 Bryce Lanham
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#include "qemu/osdep.h"
#include "ati_int.h"
#include "ati_regs.h"
#include "hw/pci/pci_device.h"
#include "qemu/bswap.h"
#include "qemu/log.h"

#define ATI_PM4_MAX_EXEC_DWORDS (1U << 20)
#define ATI_PM4_MAX_SURFACE_PIXELS (16U * 1024U * 1024U)
#define ATI_PM4_MAX_INDIRECT_DEPTH 4

static bool ati_pm4_bus_master_mode(uint32_t mode)
{
    switch (mode) {
    case 2: /* 192 bus-master */
    case 4: /* 128 bus-master + 64 indirect */
    case 6: /* 64 bus-master + 128 indirect */
    case 8: /* 64 bus-master + 64 VC + 64 indirect */
        return true;
    default:
        return false;
    }
}

static uint32_t ati_pm4_fifo_size(uint32_t buffer_cntl)
{
    switch ((buffer_cntl >> 28) & 0xf) {
    case 1: /* 192 PIO */
    case 2: /* 192 bus-master */
        return 192;
    case 3: /* 128 PIO + 64 indirect */
    case 4: /* 128 bus-master + 64 indirect */
        return 128;
    case 5: /* 64 PIO + 128 indirect */
    case 6: /* 64 bus-master + 128 indirect */
    case 7: /* 64 PIO + 64 VC + 64 indirect */
    case 8: /* 64 bus-master + 64 VC + 64 indirect */
    case 15:
        return 64;
    default:
        return 0;
    }
}

static bool ati_pm4_bus_master_enabled(const ATIVGAState *s)
{
    uint16_t command = pci_get_word(s->dev.config + PCI_COMMAND);

    return (command & PCI_COMMAND_MASTER) &&
           !(s->pm4.bus_cntl & BUS_MASTER_DIS);
}

static void ati_pm4_fault(ATIVGAState *s, const char *message)
{
    if (!s->pm4.fault) {
        qemu_log_mask(LOG_GUEST_ERROR, "ATI Rage 128 PM4: %s\n", message);
    }
    s->pm4.fault = true;
}

static MemTxResult ati_pm4_dma_read_direct(ATIVGAState *s,
                                            dma_addr_t address,
                                            void *buffer, size_t length)
{
    MemTxResult result;

    result = pci_dma_read(&s->dev, address, buffer, length);
    if (result != MEMTX_OK) {
        ati_pm4_fault(s, "DMA read failed");
    }
    return result;
}

static MemTxResult ati_pm4_dma_write_direct(ATIVGAState *s,
                                             dma_addr_t address,
                                             const void *buffer, size_t length)
{
    MemTxResult result;

    result = pci_dma_write(&s->dev, address, buffer, length);
    if (result != MEMTX_OK) {
        ati_pm4_fault(s, "DMA write failed");
    }
    return result;
}

/*
 * The PCI Rage 128 driver exposes a 32 MiB virtual aperture beginning at
 * 0x02000000. PCI_GART_PAGE points at an 8192-entry little-endian page table.
 * AGP boards use the same address convention in command streams; accepting a
 * populated PCI table there gives the software model one deterministic DMA
 * path on both board personalities without pretending that the host bridge is
 * an AGP chipset.
 */
static MemTxResult ati_pm4_dma_rw(ATIVGAState *s, dma_addr_t address,
                                  void *buffer, size_t length, bool write)
{
    uint8_t *bytes = buffer;

    if (!ati_pm4_bus_master_enabled(s)) {
        ati_pm4_fault(s, "bus-master access while PCI mastering is disabled");
        return MEMTX_ERROR;
    }

    while (length) {
        dma_addr_t physical = address;
        size_t chunk = length;

        if (address >= ATI_RAGE128_GART_VIRT_BASE &&
            address < ATI_RAGE128_GART_VIRT_END) {
            uint32_t page_index;
            uint32_t page_entry;
            dma_addr_t page_table_address;
            size_t in_page;

            if (!s->pm4.pci_gart_page) {
                ati_pm4_fault(s, "AGP/PCI-GART access without a page table");
                return MEMTX_ERROR;
            }
            page_index = (address - ATI_RAGE128_GART_VIRT_BASE) >> 12;
            page_table_address = s->pm4.pci_gart_page + page_index * 4;
            if (ati_pm4_dma_read_direct(s, page_table_address,
                                        &page_entry, sizeof(page_entry)) !=
                MEMTX_OK) {
                return MEMTX_ERROR;
            }
            page_entry = le32_to_cpu(page_entry);
            physical = (page_entry & UINT32_C(0xfffff000)) |
                       (address & UINT32_C(0x00000fff));
            if (!(page_entry & UINT32_C(0xfffff000))) {
                ati_pm4_fault(s, "unmapped PCI-GART page");
                return MEMTX_ERROR;
            }
            in_page = 0x1000 - (address & 0xfff);
            chunk = MIN(chunk, in_page);
        }

        if (write) {
            if (ati_pm4_dma_write_direct(s, physical, bytes, chunk) !=
                MEMTX_OK) {
                return MEMTX_ERROR;
            }
        } else if (ati_pm4_dma_read_direct(s, physical, bytes, chunk) !=
                   MEMTX_OK) {
            return MEMTX_ERROR;
        }

        address += chunk;
        bytes += chunk;
        length -= chunk;
    }
    return MEMTX_OK;
}

static MemTxResult ati_pm4_dma_read(ATIVGAState *s, dma_addr_t address,
                                    void *buffer, size_t length)
{
    return ati_pm4_dma_rw(s, address, buffer, length, false);
}

static MemTxResult ati_pm4_dma_write(ATIVGAState *s, dma_addr_t address,
                                     const void *buffer, size_t length)
{
    return ati_pm4_dma_rw(s, address, (void *)buffer, length, true);
}

bool ati_pm4_read_guest(ATIVGAState *s, dma_addr_t address,
                         void *buffer, size_t length)
{
    return ati_pm4_dma_read(s, address, buffer, length) == MEMTX_OK;
}

static bool ati_pm4_read_dword(ATIVGAState *s, dma_addr_t address,
                               uint32_t *value)
{
    uint32_t raw;

    if (ati_pm4_dma_read(s, address, &raw, sizeof(raw)) != MEMTX_OK) {
        return false;
    }
    *value = le32_to_cpu(raw);
    return true;
}

static bool ati_pm4_write_dword(ATIVGAState *s, dma_addr_t address,
                                uint32_t value)
{
    uint32_t raw = cpu_to_le32(value);

    return ati_pm4_dma_write(s, address, &raw, sizeof(raw)) == MEMTX_OK;
}

static bool ati_pm4_packet3(ATIVGAState *s, uint32_t opcode,
                            const uint32_t *payload, unsigned int count)
{
    switch (opcode) {
    case R128_PM4_PACKET3_NOP:
        return true;

    case R128_PM4_CNTL_PAINT_MULTI:
        if (count < 5) {
            ati_pm4_fault(s, "short PAINT_MULTI packet");
            return false;
        }
        if ((uint64_t)(payload[4] >> 16) *
            (payload[4] & UINT32_C(0xffff)) >
            ATI_PM4_MAX_SURFACE_PIXELS) {
            ati_pm4_fault(s,
                          "PAINT_MULTI rectangle exceeds pixel work limit");
            return false;
        }
        if (payload[0] & R128_GMC_WR_MSK_DIS) {
            ati_mm_write_reg(s, DP_WRITE_MASK, UINT32_MAX);
        }
        if (payload[0] & R128_GMC_AUX_CLIP_DIS) {
            ati_mm_write_reg(s, AUX_SC_CNTL, 0);
        }
        /*
         * PAINT_MULTI carries an explicit Rage 128 pitch/offset surface.
         * Route every surface through the shared software surface helper;
         * the legacy 2D register path uses the implicit/default surface and
         * therefore loses this packet-local destination on non-tiled clears.
         */
        if (ati_3d_surface_fill(s, payload[0], payload[1], payload[2],
                                payload[3], payload[4])) {
            return true;
        }
        ati_mm_write_reg(s, DP_GUI_MASTER_CNTL, payload[0]);
        ati_mm_write_reg(s, DST_PITCH_OFFSET, payload[1]);
        ati_mm_write_reg(s, DP_BRUSH_FRGD_CLR, payload[2]);
        ati_mm_write_reg(s, DST_X_Y, payload[3]);
        ati_mm_write_reg(s, DST_WIDTH_HEIGHT, payload[4]);
        return true;

    case R128_PM4_CNTL_BITBLT_MULTI:
        if (count < 6) {
            ati_pm4_fault(s, "short BITBLT_MULTI packet");
            return false;
        }
        if (payload[0] & R128_GMC_WR_MSK_DIS) {
            ati_mm_write_reg(s, DP_WRITE_MASK, UINT32_MAX);
        }
        if (payload[0] & R128_GMC_AUX_CLIP_DIS) {
            ati_mm_write_reg(s, AUX_SC_CNTL, 0);
        }
        ati_mm_write_reg(s, DP_GUI_MASTER_CNTL, payload[0]);
        ati_mm_write_reg(s, SRC_PITCH_OFFSET, payload[1]);
        ati_mm_write_reg(s, DST_PITCH_OFFSET, payload[2]);
        ati_mm_write_reg(s, SRC_X_Y, payload[3]);
        ati_mm_write_reg(s, DST_X_Y, payload[4]);
        ati_mm_write_reg(s, DST_WIDTH_HEIGHT, payload[5]);
        return true;

    case R128_PM4_CNTL_HOSTDATA_BLT:
    {
        unsigned int data_words;

        if (count < 7) {
            ati_pm4_fault(s, "short HOSTDATA_BLT packet");
            return false;
        }
        if (payload[6] != count - 7) {
            ati_pm4_fault(s, "HOSTDATA_BLT payload length is inconsistent");
            return false;
        }
        data_words = payload[6];
        if (payload[0] & R128_GMC_WR_MSK_DIS) {
            ati_mm_write_reg(s, DP_WRITE_MASK, UINT32_MAX);
        }
        if (payload[0] & R128_GMC_AUX_CLIP_DIS) {
            ati_mm_write_reg(s, AUX_SC_CNTL, 0);
        }
        ati_mm_write_reg(s, DP_GUI_MASTER_CNTL, payload[0]);
        ati_mm_write_reg(s, DST_PITCH_OFFSET, payload[1]);
        /* HOSTDATA_BLT uses Y:X and HEIGHT:WIDTH field ordering. */
        ati_mm_write_reg(s, DST_Y_X, payload[4]);
        ati_mm_write_reg(s, DST_HEIGHT_WIDTH, payload[5]);
        if (!s->host_data.active && data_words) {
            ati_pm4_fault(s, "HOSTDATA_BLT did not start a host transfer");
            return false;
        }
        for (unsigned int i = 0; i < data_words; i++) {
            hwaddr reg = i + 1 == data_words ? HOST_DATA_LAST :
                         HOST_DATA0 + (i & 3) * 4;
            ati_mm_write_reg(s, reg, payload[7 + i]);
        }
        if (s->host_data.active) {
            ati_host_data_finish(s);
        }
        return true;
    }

    case R128_PM4_3D_RNDR_GEN_INDX_PRIM:
        if (count < 4) {
            ati_pm4_fault(s, "short 3D primitive packet");
            return false;
        }
        if (!ati_3d_draw_indexed(s, payload[0], payload[1],
                                 payload[2], payload[3],
                                 count > 4 ? &payload[4] : NULL,
                                 count > 4 ? count - 4 : 0)) {
            ati_pm4_fault(s, "3D primitive command failed");
            return false;
        }
        return true;

    default:
        qemu_log_mask(LOG_UNIMP,
                      "ATI Rage 128 PM4 packet3 opcode 0x%04x is not implemented\n",
                      opcode);
        ati_pm4_fault(s, "unsupported packet3 opcode");
        return false;
    }
}

static bool ati_pm4_dispatch_packet(ATIVGAState *s,
                                    const uint32_t *packet,
                                    unsigned int dwords)
{
    uint32_t header = packet[0];
    uint32_t type = header & R128_PM4_PACKET_TYPE_MASK;
    unsigned int payload_count = dwords - 1;

    s->pm4.packets_executed++;
    switch (type) {
    case R128_PM4_PACKET0:
    {
        hwaddr reg = (header & R128_PM4_PACKET0_REG_MASK) << 2;
        bool one_reg = header & R128_PM4_PACKET0_ONE_REG_WR;

        for (unsigned int i = 0; i < payload_count; i++) {
            ati_mm_write_reg(s, one_reg ? reg : reg + i * 4, packet[i + 1]);
            if (s->pm4.fault) {
                return false;
            }
        }
        return true;
    }

    case R128_PM4_PACKET1:
        if (payload_count != 2) {
            ati_pm4_fault(s, "malformed packet1");
            return false;
        }
        ati_mm_write_reg(s,
                         (header & R128_PM4_PACKET1_REG0_MASK) << 2,
                         packet[1]);
        ati_mm_write_reg(s,
                         ((header & R128_PM4_PACKET1_REG1_MASK) >> 11) << 2,
                         packet[2]);
        return !s->pm4.fault;

    case R128_PM4_PACKET2:
        return true;

    case R128_PM4_PACKET3:
        return ati_pm4_packet3(s, header & R128_PM4_PACKET3_OPCODE_MASK,
                               &packet[1], payload_count);

    default:
        ati_pm4_fault(s, "unknown packet type");
        return false;
    }
}

static bool ati_pm4_feed_word(ATIVGAState *s, uint32_t word)
{
    ATIPM4State *pm4 = &s->pm4;

    if (!pm4->packet_used) {
        uint32_t type = word & R128_PM4_PACKET_TYPE_MASK;
        unsigned int payload_count;

        switch (type) {
        case R128_PM4_PACKET0:
        case R128_PM4_PACKET3:
            payload_count = ((word & R128_PM4_PACKET_COUNT_MASK) >> 16) + 1;
            pm4->packet_needed = payload_count + 1;
            break;
        case R128_PM4_PACKET1:
            pm4->packet_needed = 3;
            break;
        case R128_PM4_PACKET2:
            pm4->packet_needed = 1;
            break;
        default:
            ati_pm4_fault(s, "invalid packet header");
            return false;
        }
        if (pm4->packet_needed > ATI_PM4_PACKET_MAX_DWORDS) {
            ati_pm4_fault(s, "packet exceeds the command-size limit");
            pm4->packet_needed = 0;
            return false;
        }
    }

    pm4->packet[pm4->packet_used++] = word;
    pm4->dwords_executed++;
    if (pm4->packet_used == pm4->packet_needed) {
        unsigned int dwords = pm4->packet_used;
        uint32_t *packet = g_memdup2(pm4->packet,
                                     dwords * sizeof(*packet));
        bool result;

        pm4->packet_used = 0;
        pm4->packet_needed = 0;
        result = ati_pm4_dispatch_packet(s, packet, dwords);
        g_free(packet);
        return result;
    }
    return true;
}

static bool ati_pm4_execute_stream(ATIVGAState *s, dma_addr_t address,
                                   unsigned int dwords)
{
    if (++s->pm4.indirect_depth > ATI_PM4_MAX_INDIRECT_DEPTH) {
        ati_pm4_fault(s, "indirect-buffer nesting is too deep");
        s->pm4.indirect_depth--;
        return false;
    }
    if (dwords > ATI_PM4_MAX_EXEC_DWORDS) {
        ati_pm4_fault(s, "indirect buffer is too large");
        s->pm4.indirect_depth--;
        return false;
    }

    if (s->pm4.packet_used) {
        ati_pm4_fault(s, "indirect buffer entered in the middle of a packet");
        s->pm4.indirect_depth--;
        return false;
    }
    for (unsigned int i = 0; i < dwords && !s->pm4.fault; i++) {
        uint32_t word;

        if (!ati_pm4_read_dword(s, address + (dma_addr_t)i * 4, &word) ||
            !ati_pm4_feed_word(s, word)) {
            s->pm4.indirect_depth--;
            return false;
        }
    }
    if (s->pm4.packet_used) {
        s->pm4.packet_used = 0;
        s->pm4.packet_needed = 0;
        ati_pm4_fault(s, "indirect buffer ended in the middle of a packet");
    }
    s->pm4.indirect_depth--;
    return !s->pm4.fault;
}

static void ati_pm4_execute_indirect(ATIVGAState *s)
{
    if (!s->pm4.iw_indsize || s->pm4.fault) {
        return;
    }
    ati_pm4_execute_stream(s, s->pm4.iw_indoff, s->pm4.iw_indsize);
    s->pm4.iw_indsize = 0;
}

void ati_pm4_run(ATIVGAState *s)
{
    ATIPM4State *pm4 = &s->pm4;
    uint32_t size_l2qw = pm4->buffer_cntl & 0x3f;
    uint32_t mode = (pm4->buffer_cntl >> 28) & 0xf;
    uint32_t ring_dwords;
    uint32_t mask;
    unsigned int executed = 0;

    if (pm4->executing || pm4->fault ||
        !ati_pm4_bus_master_mode(mode) ||
        !(pm4->micro_cntl & R128_PM4_MICRO_FREERUN)) {
        return;
    }
    /* PIO modes consume PM4_FIFO_DATA; only BM modes fetch a DMA ring. */
    if (!pm4->microcode_loaded) {
        ati_pm4_fault(s, "ring started before the CCE microcode was loaded");
        return;
    }
    if (size_l2qw > 24) {
        ati_pm4_fault(s, "invalid ring size");
        return;
    }
    ring_dwords = 2U << size_l2qw;
    mask = ring_dwords - 1;
    pm4->rptr &= mask;
    pm4->wptr &= mask;

    pm4->executing = true;
    pm4->busy = true;
    while (pm4->rptr != pm4->wptr && !pm4->fault) {
        uint32_t word;
        dma_addr_t address;

        if (++executed > ATI_PM4_MAX_EXEC_DWORDS) {
            ati_pm4_fault(s, "ring execution exceeded the safety limit");
            break;
        }
        address = pm4->buffer_offset + (dma_addr_t)pm4->rptr * 4;
        if (!ati_pm4_read_dword(s, address, &word) ||
            !ati_pm4_feed_word(s, word)) {
            break;
        }
        pm4->rptr = (pm4->rptr + 1) & mask;
    }
    if (pm4->rptr_addr) {
        ati_pm4_write_dword(s, pm4->rptr_addr, pm4->rptr);
    }
    pm4->busy = false;
    pm4->executing = false;
}

uint32_t ati_pm4_gui_status(const ATIVGAState *s)
{
    return 64 | (s->pm4.busy ? GUI_ACTIVE : 0);
}

static uint32_t ati_pm4_status(const ATIVGAState *s)
{
    uint32_t value = ati_pm4_fifo_size(s->pm4.buffer_cntl);

    if (s->pm4.busy) {
        value |= R128_PM4_BUSY | R128_PM4_GUI_ACTIVE;
    }
    return value;
}

void ati_pm4_reset(ATIVGAState *s, bool full)
{
    ATIPM4State saved = { 0 };

    if (!full) {
        /*
         * SOFT_RESET_GUI stops and drains the command processor, but the
         * historical DRM driver programs the GART and ring location before
         * issuing that reset and does not program them again afterwards.
         * Preserve the CCE configuration and uploaded microcode while
         * clearing live execution, pointers, packet assembly, and faults.
         */
        saved.bus_cntl = s->pm4.bus_cntl;
        saved.pci_gart_page = s->pm4.pci_gart_page;
        saved.buffer_offset = s->pm4.buffer_offset;
        saved.buffer_cntl = s->pm4.buffer_cntl;
        saved.buffer_wm_cntl = s->pm4.buffer_wm_cntl;
        saved.rptr_addr = s->pm4.rptr_addr;
        memcpy(saved.microcode, s->pm4.microcode,
               sizeof(saved.microcode));
        saved.microcode_words = s->pm4.microcode_words;
        saved.microcode_loaded = s->pm4.microcode_loaded;
    }
    memset(&s->pm4, 0, sizeof(s->pm4));
    if (!full) {
        s->pm4.bus_cntl = saved.bus_cntl;
        s->pm4.pci_gart_page = saved.pci_gart_page;
        s->pm4.buffer_offset = saved.buffer_offset;
        s->pm4.buffer_cntl = saved.buffer_cntl;
        s->pm4.buffer_wm_cntl = saved.buffer_wm_cntl;
        s->pm4.rptr_addr = saved.rptr_addr;
        memcpy(s->pm4.microcode, saved.microcode,
               sizeof(s->pm4.microcode));
        s->pm4.microcode_words = saved.microcode_words;
        s->pm4.microcode_loaded = saved.microcode_loaded;
        if (s->pm4.rptr_addr && ati_pm4_bus_master_enabled(s)) {
            uint32_t zero = 0;

            /* Keep the driver-visible ring-head shadow coherent with reset. */
            ati_pm4_dma_rw(s, s->pm4.rptr_addr, &zero, sizeof(zero), true);
        }
    }
    /*
     * The GUI clip registers reset to the full drawable coordinate range.
     * Leaving them at zero silently clips every legacy 2D operation after a
     * device or GUI reset, including PM4 BITBLT and HOSTDATA packets that
     * intentionally use the default clip rectangle.
     */
    s->regs.default_sc_right = 0x3fff;
    s->regs.default_sc_bottom = 0x3fff;
    s->regs.sc_right = 0x3fff;
    s->regs.sc_bottom = 0x3fff;
    s->regs.src_sc_right = 0x3fff;
    s->regs.src_sc_bottom = 0x3fff;
    ati_3d_reset(s);
}

bool ati_pm4_mm_read(ATIVGAState *s, hwaddr addr, unsigned int size,
                     uint32_t *value)
{
    ATIPM4State *pm4 = &s->pm4;
    uint32_t val;

    if (ati_3d_mm_read(s, addr, size, value)) {
        return true;
    }
    if (size != 4 || (addr & 3)) {
        return false;
    }

    switch (addr) {
    case BUS_CNTL:
        val = pm4->bus_cntl;
        break;
    case PCI_GART_PAGE:
        val = pm4->pci_gart_page;
        break;
    case PM4_BUFFER_OFFSET:
        val = pm4->buffer_offset;
        break;
    case PM4_BUFFER_CNTL:
        val = pm4->buffer_cntl;
        break;
    case PM4_BUFFER_WM_CNTL:
        val = pm4->buffer_wm_cntl;
        break;
    case PM4_BUFFER_DL_RPTR_ADDR:
        val = pm4->rptr_addr;
        break;
    case PM4_BUFFER_DL_RPTR:
        val = pm4->rptr;
        break;
    case PM4_BUFFER_DL_WPTR:
        val = pm4->wptr;
        break;
    case PM4_VC_FPU_SETUP:
        val = pm4->vc_fpu_setup;
        break;
    case PM4_IW_INDOFF:
        val = pm4->iw_indoff;
        break;
    case PM4_IW_INDSIZE:
        val = pm4->iw_indsize;
        break;
    case PM4_STAT:
        val = ati_pm4_status(s);
        break;
    case PM4_MICROCODE_ADDR:
        val = pm4->microcode_addr;
        break;
    case PM4_MICROCODE_RADDR:
        val = pm4->microcode_raddr;
        break;
    case PM4_MICROCODE_DATAH:
        val = pm4->microcode[pm4->microcode_raddr & 0xff] >> 32;
        break;
    case PM4_MICROCODE_DATAL:
        val = pm4->microcode[pm4->microcode_raddr & 0xff];
        pm4->microcode_raddr = (pm4->microcode_raddr + 1) & 0xff;
        break;
    case PM4_BUFFER_ADDR:
        val = pm4->buffer_offset + pm4->rptr * 4;
        break;
    case PM4_MICRO_CNTL:
        val = pm4->micro_cntl;
        break;
    case PC_GUI_CTLSTAT:
        val = pm4->pc_gui_ctlstat & ~PC_BUSY;
        break;
    case WAIT_UNTIL:
        val = pm4->wait_until;
        break;
    default:
        return false;
    }
    *value = val;
    return true;
}

bool ati_pm4_mm_write(ATIVGAState *s, hwaddr addr, uint64_t data,
                      unsigned int size)
{
    ATIPM4State *pm4 = &s->pm4;
    uint32_t value = data;

    if (ati_3d_mm_write(s, addr, data, size)) {
        return true;
    }
    if (size != 4 || (addr & 3)) {
        return false;
    }

    switch (addr) {
    case BUS_CNTL:
        pm4->bus_cntl = value;
        return true;
    case PCI_GART_PAGE:
        pm4->pci_gart_page = value & ~0xfffU;
        return true;
    case PM4_BUFFER_OFFSET:
        pm4->buffer_offset = value & ~7U;
        return true;
    case PM4_BUFFER_CNTL:
        pm4->buffer_cntl = value;
        ati_pm4_run(s);
        return true;
    case PM4_BUFFER_WM_CNTL:
        pm4->buffer_wm_cntl = value;
        return true;
    case PM4_BUFFER_DL_RPTR_ADDR:
        pm4->rptr_addr = value & ~3U;
        return true;
    case PM4_BUFFER_DL_RPTR:
        pm4->rptr = value & 0x00ffffffU;
        pm4->packet_used = 0;
        pm4->packet_needed = 0;
        if (pm4->rptr_addr && ati_pm4_bus_master_enabled(s)) {
            uint32_t shadow = cpu_to_le32(pm4->rptr);

            ati_pm4_dma_rw(s, pm4->rptr_addr, &shadow, sizeof(shadow), true);
        }
        return true;
    case PM4_BUFFER_DL_WPTR:
        pm4->wptr = value & 0x00ffffffU;
        ati_pm4_run(s);
        return true;
    case PM4_VC_FPU_SETUP:
        pm4->vc_fpu_setup = value;
        return true;
    case PM4_IW_INDOFF:
        pm4->iw_indoff = value & ~7U;
        return true;
    case PM4_IW_INDSIZE:
        pm4->iw_indsize = value & 0x00ffffffU;
        ati_pm4_execute_indirect(s);
        return true;
    case PM4_MICROCODE_ADDR:
        pm4->microcode_addr = value & 0xff;
        pm4->microcode_words = 0;
        pm4->microcode_loaded = false;
        return true;
    case PM4_MICROCODE_RADDR:
        pm4->microcode_raddr = value & 0xff;
        return true;
    case PM4_MICROCODE_DATAH:
        pm4->microcode_datah = value;
        return true;
    case PM4_MICROCODE_DATAL:
        pm4->microcode[pm4->microcode_addr & 0xff] =
            ((uint64_t)pm4->microcode_datah << 32) | value;
        pm4->microcode_addr = (pm4->microcode_addr + 1) & 0xff;
        if (pm4->microcode_words < 256) {
            pm4->microcode_words++;
        }
        pm4->microcode_loaded = pm4->microcode_words == 256;
        return true;
    case PM4_MICRO_CNTL:
        pm4->micro_cntl = value & R128_PM4_MICRO_FREERUN;
        ati_pm4_run(s);
        return true;
    case PM4_FIFO_DATA_EVEN:
    case PM4_FIFO_DATA_ODD:
        if (!ati_pm4_feed_word(s, value)) {
            return true;
        }
        return true;
    case PC_GUI_CTLSTAT:
        pm4->pc_gui_ctlstat = value & UINT32_C(0x000000ff);
        return true;
    case WAIT_UNTIL:
        pm4->wait_until = value;
        return true;
    default:
        return false;
    }
}
