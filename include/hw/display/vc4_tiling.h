/*
 * VideoCore IV texture/render-target tiling helpers
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_VC4_TILING_H
#define HW_DISPLAY_VC4_TILING_H

#include <stdbool.h>
#include <stdint.h>

#define VC4_TILING_FORMAT_LINEAR 0
#define VC4_TILING_FORMAT_T      1
#define VC4_TILING_FORMAT_LT     2

/*
 * Return the byte stride selected by VC4 for an RGBA8888 level of the given
 * logical width.  T levels are aligned to a complete 4 KiB tile width; LT
 * levels are aligned to a 64-byte utile width.
 */
bool vc4_tiling_rgba8888_stride(uint8_t tiling, uint32_t width,
                                uint32_t *stride);

/*
 * Translate an RGBA8888 pixel coordinate into a byte offset from a level's
 * base address.  This models the LT utile swizzle and the alternating T-tile
 * and subtile ordering used by VideoCore IV.
 */
bool vc4_tiling_rgba8888_offset(uint8_t tiling, uint32_t stride,
                                uint32_t x, uint32_t y,
                                uint64_t *offset);

#endif /* HW_DISPLAY_VC4_TILING_H */
