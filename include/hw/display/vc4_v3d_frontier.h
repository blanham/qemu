/*
 * VideoCore IV V3D shader and primitive frontier diagnostics
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_VC4_V3D_FRONTIER_H
#define HW_DISPLAY_VC4_V3D_FRONTIER_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef bool (*VC4V3DFrontierReadFunc)(void *opaque, uint32_t address,
                                       void *buffer, size_t size);

typedef struct VC4V3DFrontierState {
    uint32_t bin_alloc_base;
    uint32_t bin_alloc_size;
    uint32_t bin_state_base;
    uint32_t shader_record;
    uint8_t bin_tiles_x;
    uint8_t bin_tiles_y;
    uint8_t bin_flags;
    bool have_binning_config;
    bool have_shader_record;
} VC4V3DFrontierState;

typedef struct VC4V3DPrimitiveInfo {
    uint32_t pc;
    uint32_t length;
    uint32_t first;
    uint32_t index_address;
    uint32_t max_index;
    unsigned thread;
    uint8_t packet;
    uint8_t mode_byte;
    bool indexed;
} VC4V3DPrimitiveInfo;

/*
 * Emit a bounded transcript for a newly observed primitive.  Returning false
 * means the same PC and shader record were already reported, allowing callers
 * to suppress repeated timeout/recovery noise without changing device state.
 */
bool vc4_v3d_frontier_report(VC4V3DFrontierReadFunc read_func,
                              void *opaque, const char *device_name,
                              const VC4V3DFrontierState *state,
                              const VC4V3DPrimitiveInfo *primitive,
                              uint32_t *last_pc,
                              uint32_t *last_shader_record);

#endif /* HW_DISPLAY_VC4_V3D_FRONTIER_H */
