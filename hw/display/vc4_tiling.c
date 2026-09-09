/*
 * VideoCore IV texture/render-target tiling helpers
 *
 * The layout follows the public VC4 Mesa tiling implementation: RGBA8888
 * pixels form 4x4-pixel, 64-byte utiles; four-by-four utiles form a 1 KiB
 * subtile; and two-by-two subtiles form a 4 KiB T tile.  T-tile rows reverse
 * direction on odd rows and use the hardware's row-dependent subtile maps.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_tiling.h"

#define VC4_RGBA8888_CPP          4u
#define VC4_RGBA8888_UTILE_WIDTH  4u
#define VC4_RGBA8888_UTILE_HEIGHT 4u
#define VC4_UTILE_BYTES          64u
#define VC4_SUBTILE_BYTES      1024u
#define VC4_TILE_BYTES         4096u
#define VC4_T_UTILES_PER_TILE     8u
#define VC4_T_PIXELS_PER_TILE    32u

static bool vc4_u32_align_up(uint32_t value, uint32_t alignment,
                             uint32_t *result)
{
    uint64_t aligned;

    if (alignment == 0 || (alignment & (alignment - 1)) != 0) {
        return false;
    }
    aligned = ((uint64_t)value + alignment - 1) &
              ~((uint64_t)alignment - 1);
    if (aligned > UINT32_MAX) {
        return false;
    }
    *result = aligned;
    return true;
}

bool vc4_tiling_rgba8888_stride(uint8_t tiling, uint32_t width,
                                uint32_t *stride)
{
    uint32_t aligned_width;
    uint64_t bytes;

    if (stride == NULL || width == 0) {
        return false;
    }

    switch (tiling) {
    case VC4_TILING_FORMAT_LINEAR:
        aligned_width = width;
        break;
    case VC4_TILING_FORMAT_LT:
        if (!vc4_u32_align_up(width, VC4_RGBA8888_UTILE_WIDTH,
                              &aligned_width)) {
            return false;
        }
        break;
    case VC4_TILING_FORMAT_T:
        if (!vc4_u32_align_up(width, VC4_T_PIXELS_PER_TILE,
                              &aligned_width)) {
            return false;
        }
        break;
    default:
        return false;
    }

    bytes = (uint64_t)aligned_width * VC4_RGBA8888_CPP;
    if (bytes > UINT32_MAX) {
        return false;
    }
    *stride = bytes;
    return true;
}

static uint64_t vc4_rgba8888_utile_pixel_offset(uint32_t x, uint32_t y)
{
    return (uint64_t)(y & (VC4_RGBA8888_UTILE_HEIGHT - 1)) * 16 +
           (uint64_t)(x & (VC4_RGBA8888_UTILE_WIDTH - 1)) *
               VC4_RGBA8888_CPP;
}

static bool vc4_rgba8888_lt_offset(uint32_t stride, uint32_t x,
                                   uint32_t y, uint64_t *offset)
{
    uint64_t utile_row;
    uint64_t utile_column;

    if (stride == 0 || (stride & 15) != 0 ||
        x >= stride / VC4_RGBA8888_CPP) {
        return false;
    }

    utile_row = (uint64_t)(y >> 2) * 4 * stride;
    utile_column = (uint64_t)(x >> 2) * VC4_UTILE_BYTES;
    *offset = utile_row + utile_column +
              vc4_rgba8888_utile_pixel_offset(x, y);
    return true;
}

static bool vc4_rgba8888_t_offset(uint32_t stride, uint32_t x,
                                  uint32_t y, uint64_t *offset)
{
    static const uint8_t odd_subtile_map[4] = { 2, 1, 3, 0 };
    static const uint8_t even_subtile_map[4] = { 0, 3, 1, 2 };
    uint32_t utile_stride;
    uint32_t tile_stride;
    uint32_t utile_x = x >> 2;
    uint32_t utile_y = y >> 2;
    uint32_t tile_x = utile_x >> 3;
    uint32_t tile_y = utile_y >> 3;
    bool odd_tile_y = tile_y & 1;
    uint32_t subtile_x;
    uint32_t subtile_y;
    uint32_t subtile_index;
    uint32_t mapped_subtile;
    uint64_t tile_offset;
    uint64_t local_utile_offset;

    if (stride == 0 || stride % (VC4_RGBA8888_CPP *
                                 VC4_RGBA8888_UTILE_WIDTH) != 0) {
        return false;
    }
    utile_stride = stride /
                   (VC4_RGBA8888_CPP * VC4_RGBA8888_UTILE_WIDTH);
    if (utile_stride == 0 ||
        (utile_stride & (VC4_T_UTILES_PER_TILE - 1)) != 0) {
        return false;
    }
    tile_stride = utile_stride >> 3;
    if (tile_x >= tile_stride) {
        return false;
    }

    if (odd_tile_y) {
        tile_x = tile_stride - tile_x - 1;
    }
    tile_offset = (uint64_t)VC4_TILE_BYTES *
                  ((uint64_t)tile_y * tile_stride + tile_x);

    subtile_x = (utile_x >> 2) & 1;
    subtile_y = (utile_y >> 2) & 1;
    subtile_index = (subtile_y << 1) | subtile_x;
    mapped_subtile = odd_tile_y ? odd_subtile_map[subtile_index] :
                                  even_subtile_map[subtile_index];

    /* Each subtile is an LT image with a 64-byte conceptual row stride. */
    local_utile_offset =
        (uint64_t)(((utile_y & 3) << 2) | (utile_x & 3)) *
        VC4_UTILE_BYTES;
    *offset = tile_offset + (uint64_t)mapped_subtile *
              VC4_SUBTILE_BYTES + local_utile_offset +
              vc4_rgba8888_utile_pixel_offset(x, y);
    return true;
}

bool vc4_tiling_rgba8888_offset(uint8_t tiling, uint32_t stride,
                                uint32_t x, uint32_t y,
                                uint64_t *offset)
{
    if (offset == NULL) {
        return false;
    }

    switch (tiling) {
    case VC4_TILING_FORMAT_LINEAR:
        if (stride < VC4_RGBA8888_CPP ||
            x >= stride / VC4_RGBA8888_CPP) {
            return false;
        }
        *offset = (uint64_t)y * stride +
                  (uint64_t)x * VC4_RGBA8888_CPP;
        return true;
    case VC4_TILING_FORMAT_LT:
        return vc4_rgba8888_lt_offset(stride, x, y, offset);
    case VC4_TILING_FORMAT_T:
        return vc4_rgba8888_t_offset(stride, x, y, offset);
    default:
        return false;
    }
}
