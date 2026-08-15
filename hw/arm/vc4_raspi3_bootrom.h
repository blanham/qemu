/*
 * Raspberry Pi VideoCore boot-ROM image loader
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_ARM_VC4_RASPI3_BOOTROM_H
#define HW_ARM_VC4_RASPI3_BOOTROM_H

#include "qemu/typedefs.h"

#define VC4_RASPI3_BOOT_CACHE_SIZE (128 * 1024)
/* The BCM boot ROM reserves/ignores the first 512 bytes of bootcode.bin. */
#define VC4_RASPI3_BOOT_ENTRY 0x200

typedef struct VC4Raspi3BootInfo {
    uint64_t partition_lba;
    uint32_t file_size;
    uint32_t first_cluster;
    uint16_t bytes_per_sector;
    uint8_t sectors_per_cluster;
    bool fat32;
} VC4Raspi3BootInfo;

bool vc4_raspi3_bootrom_load(BlockBackend *blk,
                             uint8_t *boot_cache,
                             size_t boot_cache_size,
                             VC4Raspi3BootInfo *info,
                             Error **errp);

#endif
