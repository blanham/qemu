/*
 * QEMU ATI SVGA emulation
 *
 * Copyright (c) 2019 BALATON Zoltan
 *
 * This work is licensed under the GNU GPL license version 2 or later.
 */

#ifndef ATI_INT_H
#define ATI_INT_H

#include "qemu/timer.h"
#include "qemu/units.h"
#include "hw/pci/pci_device.h"
#include "hw/i2c/bitbang_i2c.h"
#include "hw/display/i2c-ddc.h"
#include "vga_int.h"
#include "qom/object.h"

/*#define DEBUG_ATI*/

#ifdef DEBUG_ATI
#define DPRINTF(fmt, ...) printf("%s: " fmt, __func__, ## __VA_ARGS__)
#else
#define DPRINTF(fmt, ...) do {} while (0)
#endif

#define PCI_VENDOR_ID_ATI 0x1002
/* Rage128 Pro GL */
#define PCI_DEVICE_ID_ATI_RAGE128_PF 0x5046
/* Rage128 GL PCI */
#define PCI_DEVICE_ID_ATI_RAGE128_RE 0x5245
/* Radeon RV100 (VE) */
#define PCI_DEVICE_ID_ATI_RADEON_QY 0x5159

#define ATI_RAGE128_LINEAR_APER_SIZE (64 * MiB)
#define ATI_R100_LINEAR_APER_SIZE (128 * MiB)
#define ATI_HOST_DATA_ACC_BITS 128
#define ATI_PM4_PACKET_MAX_DWORDS 4096
#define ATI_3D_REG_DWORDS ((0x1e00 - 0x1800) / sizeof(uint32_t))

#define TYPE_ATI_VGA "ati-vga"
OBJECT_DECLARE_SIMPLE_TYPE(ATIVGAState, ATI_VGA)

typedef struct ATIVGARegs {
    uint32_t mm_index;
    uint32_t clock_cntl_index;
    uint32_t bios_scratch[8];
    uint32_t gen_int_cntl;
    uint32_t gen_int_status;
    uint32_t gen_reset_cntl;
    uint32_t pc_ngui_ctlstat;
    uint32_t crtc_gen_cntl;
    uint32_t crtc_ext_cntl;
    uint32_t dac_cntl;
    uint32_t gpio_vga_ddc;
    uint32_t gpio_dvi_ddc;
    uint32_t gpio_monid;
    uint32_t config_cntl;
    uint32_t palette[256];
    uint32_t crtc_h_total_disp;
    uint32_t crtc_h_sync_strt_wid;
    uint32_t crtc_v_total_disp;
    uint32_t crtc_v_sync_strt_wid;
    uint32_t crtc_offset;
    uint32_t crtc_offset_cntl;
    uint32_t crtc_pitch;
    uint32_t cur_offset;
    uint32_t cur_hv_pos;
    uint32_t cur_hv_offs;
    uint32_t cur_color0;
    uint32_t cur_color1;
    uint32_t dst_offset;
    uint32_t dst_pitch;
    uint32_t dst_tile;
    uint32_t dst_width;
    uint32_t dst_height;
    uint32_t src_offset;
    uint32_t src_pitch;
    uint32_t src_tile;
    uint32_t clr_cmp_cntl;
    uint32_t clr_cmp_clr_src;
    uint32_t clr_cmp_clr_dst;
    uint32_t clr_cmp_mask;
    uint32_t src_x;
    uint32_t src_y;
    uint32_t dst_x;
    uint32_t dst_y;
    uint32_t dst_bres_err;
    uint32_t dst_bres_inc;
    uint32_t dst_bres_dec;
    uint32_t dst_bres_lnth;
    uint32_t dp_cntl_xdir_ydir_ymajor;
    uint32_t dp_gui_master_cntl;
    uint32_t brush_y_x;
    uint32_t brush_data[64];
    uint32_t dp_brush_bkgd_clr;
    uint32_t dp_brush_frgd_clr;
    uint32_t dp_src_frgd_clr;
    uint32_t dp_src_bkgd_clr;
    uint16_t sc_top;
    uint16_t sc_left;
    uint16_t sc_bottom;
    uint16_t sc_right;
    uint16_t src_sc_bottom;
    uint16_t src_sc_right;
    uint32_t dp_cntl;
    uint32_t dp_datatype;
    uint32_t dp_mix;
    uint32_t dp_write_mask;
    uint32_t default_offset;
    uint32_t default_pitch;
    uint16_t default_sc_bottom;
    uint16_t default_sc_right;
    uint32_t default_tile;
} ATIVGARegs;

typedef struct ATIHostDataState {
    bool active;
    uint8_t pixel[4];
    uint8_t pixel_bytes_used;
    uint8_t row_padding;
    uint32_t row;
    uint32_t col;
    uint32_t next;
    uint32_t acc[4];
} ATIHostDataState;

typedef struct ATI3DState {
    uint32_t regs[ATI_3D_REG_DWORDS];
    uint32_t re_top_left;
    uint32_t aux_sc_cntl;
    uint32_t aux_sc[3][4];
    uint32_t gui_scratch[6];
} ATI3DState;

typedef struct ATIPM4State {
    uint32_t bus_cntl;
    uint32_t pci_gart_page;
    uint32_t buffer_offset;
    uint32_t buffer_cntl;
    uint32_t buffer_wm_cntl;
    uint32_t rptr_addr;
    uint32_t rptr;
    uint32_t wptr;
    uint32_t vc_fpu_setup;
    uint32_t iw_indoff;
    uint32_t iw_indsize;
    uint32_t microcode_addr;
    uint32_t microcode_raddr;
    uint32_t microcode_datah;
    uint32_t micro_cntl;
    uint32_t pc_gui_ctlstat;
    uint32_t wait_until;
    uint64_t microcode[256];
    uint64_t packets_executed;
    uint64_t dwords_executed;
    uint64_t primitives_drawn;
    uint32_t packet[ATI_PM4_PACKET_MAX_DWORDS];
    uint16_t microcode_words;
    uint16_t packet_used;
    uint16_t packet_needed;
    uint8_t indirect_depth;
    bool microcode_loaded;
    bool executing;
    bool busy;
    bool fault;
} ATIPM4State;

struct ATIVGAState {
    PCIDevice dev;
    VGACommonState vga;
    char *model;
    uint16_t dev_id;
    uint16_t subsystem_id;
    bool rage128_pci;
    uint8_t mode;
    uint8_t use_pixman;
    bool cursor_guest_mode;
    uint16_t cursor_size;
    uint32_t cursor_offset;
    QEMUCursor *cursor;
    QEMUTimer vblank_timer;
    bitbang_i2c_interface bbi2c;
    I2CDDCState i2cddc;
    uint64_t linear_aper_sz;
    MemoryRegion linear_aper;
    MemoryRegion io;
    MemoryRegion mm;
    ATIVGARegs regs;
    uint32_t pll_regs[64];
    ATIHostDataState host_data;
    ATIPM4State pm4;
    ATI3DState accel3d;
};

const char *ati_reg_name(int num);

void ati_2d_blt(ATIVGAState *s);
void ati_2d_line(ATIVGAState *s);

bool ati_host_data_flush(ATIVGAState *s);
void ati_host_data_finish(ATIVGAState *s);

bool ati_pm4_mm_read(ATIVGAState *s, hwaddr addr, unsigned int size,
                     uint32_t *value);
bool ati_pm4_mm_write(ATIVGAState *s, hwaddr addr, uint64_t data,
                      unsigned int size);
void ati_pm4_reset(ATIVGAState *s, bool full);
void ati_pm4_run(ATIVGAState *s);
uint32_t ati_pm4_gui_status(const ATIVGAState *s);
bool ati_pm4_read_guest(ATIVGAState *s, dma_addr_t address,
                         void *buffer, size_t length);

bool ati_3d_mm_read(ATIVGAState *s, hwaddr addr, unsigned int size,
                    uint32_t *value);
bool ati_3d_mm_write(ATIVGAState *s, hwaddr addr, uint64_t data,
                     unsigned int size);
void ati_3d_reset(ATIVGAState *s);
bool ati_3d_surface_fill(ATIVGAState *s, uint32_t master,
                         uint32_t pitch_offset, uint32_t color,
                         uint32_t dst_xy, uint32_t width_height);
bool ati_3d_draw_indexed(ATIVGAState *s, uint32_t address,
                         uint32_t size, uint32_t format,
                         uint32_t vc_cntl,
                         const uint32_t *index_words,
                         unsigned int index_dwords);
bool ati_3d_draw_inline(ATIVGAState *s, uint32_t format,
                        uint32_t vc_cntl,
                        const uint32_t *vertex_words,
                        unsigned int vertex_dwords);

void ati_mm_write_reg(ATIVGAState *s, hwaddr addr, uint32_t data);

#endif /* ATI_INT_H */
