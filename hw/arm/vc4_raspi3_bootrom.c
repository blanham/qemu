/*
 * Raspberry Pi VideoCore boot-ROM image loader
 *
 * The BCM2837 first-stage ROM reads bootcode.bin from the first FAT boot
 * volume on the SD card into the VPU's local L2-backed boot area.  This file
 * models that narrow ROM contract without bypassing the later VideoCore
 * firmware: the bytes located here are still executed by the VC4 TCG frontend.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qemu/bswap.h"
#include "system/block-backend.h"
#include "hw/arm/vc4_raspi3_bootrom.h"

#define MBR_SECTOR_SIZE 512
#define MBR_PARTITION_TABLE 446
#define MBR_PARTITION_COUNT 4
#define MBR_PARTITION_SIZE 16
#define MBR_SIGNATURE_OFFSET 510

#define FAT_DIRECTORY_ENTRY_SIZE 32
#define FAT_ATTR_VOLUME_ID 0x08
#define FAT_ATTR_DIRECTORY 0x10
#define FAT_ATTR_LONG_NAME 0x0f

#define FAT12_MAX_CLUSTERS 4085
#define FAT16_MAX_CLUSTERS 65525
#define FAT_MAX_CLUSTER_BYTES (1024 * 1024)
#define FAT_MAX_ROOT_BYTES (4 * 1024 * 1024)

static const uint8_t bootcode_short_name[11] = {
    'B', 'O', 'O', 'T', 'C', 'O', 'D', 'E', 'B', 'I', 'N'
};

typedef struct VC4FatVolume {
    BlockBackend *blk;
    int64_t image_size;
    uint64_t volume_offset;
    uint64_t volume_size;
    uint64_t partition_lba;

    uint16_t bytes_per_sector;
    uint8_t sectors_per_cluster;
    uint16_t reserved_sectors;
    uint8_t fat_count;
    uint32_t fat_sectors;
    uint16_t root_entries;
    uint32_t root_dir_sectors;
    uint32_t root_cluster;
    uint32_t total_sectors;
    uint32_t cluster_count;

    uint64_t fat_offset;
    uint64_t root_offset;
    uint64_t data_offset;
    uint32_t cluster_bytes;
    bool fat32;
} VC4FatVolume;

typedef enum VC4DirScanResult {
    VC4_DIR_CONTINUE,
    VC4_DIR_END,
    VC4_DIR_FOUND,
} VC4DirScanResult;

static bool vc4_bootrom_pread(BlockBackend *blk, int64_t image_size,
                              uint64_t offset, size_t bytes, void *buffer,
                              Error **errp)
{
    int ret;

    if (bytes > INT64_MAX || offset > image_size ||
        bytes > (uint64_t)image_size - offset) {
        error_setg(errp,
                   "Raspberry Pi boot ROM read outside SD image: "
                   "offset=0x%" PRIx64 " size=0x%zx image=0x%" PRIx64,
                   offset, bytes, (uint64_t)image_size);
        return false;
    }

    ret = blk_pread(blk, offset, bytes, buffer, 0);
    if (ret < 0) {
        error_setg_errno(errp, -ret,
                         "Raspberry Pi boot ROM could not read SD image at "
                         "offset 0x%" PRIx64, offset);
        return false;
    }
    return true;
}

static bool vc4_is_power_of_two(unsigned value)
{
    return value != 0 && (value & (value - 1)) == 0;
}

static bool vc4_fat_bpb_plausible(const uint8_t sector[MBR_SECTOR_SIZE])
{
    uint16_t bytes_per_sector = lduw_le_p(sector + 11);
    uint8_t sectors_per_cluster = sector[13];
    uint16_t reserved_sectors = lduw_le_p(sector + 14);
    uint8_t fat_count = sector[16];

    return lduw_le_p(sector + MBR_SIGNATURE_OFFSET) == 0xaa55 &&
           bytes_per_sector >= 512 && bytes_per_sector <= 4096 &&
           vc4_is_power_of_two(bytes_per_sector) &&
           vc4_is_power_of_two(sectors_per_cluster) &&
           sectors_per_cluster <= 128 && reserved_sectors != 0 &&
           fat_count != 0;
}

static bool vc4_partition_type_is_fat(uint8_t type)
{
    switch (type) {
    case 0x04: /* FAT16, less than 32 MiB */
    case 0x06: /* FAT16 */
    case 0x0b: /* FAT32 CHS */
    case 0x0c: /* FAT32 LBA */
    case 0x0e: /* FAT16 LBA */
    case 0xef: /* EFI system partition, also FAT */
        return true;
    default:
        return false;
    }
}

static bool vc4_find_fat_volume(VC4FatVolume *volume, Error **errp)
{
    uint8_t sector[MBR_SECTOR_SIZE];
    unsigned i;

    if (!vc4_bootrom_pread(volume->blk, volume->image_size, 0,
                           sizeof(sector), sector, errp)) {
        return false;
    }

    if (lduw_le_p(sector + MBR_SIGNATURE_OFFSET) == 0xaa55) {
        for (i = 0; i < MBR_PARTITION_COUNT; i++) {
            const uint8_t *entry = sector + MBR_PARTITION_TABLE +
                                   i * MBR_PARTITION_SIZE;
            uint8_t type = entry[4];
            uint32_t start_lba = ldl_le_p(entry + 8);
            uint32_t sector_count = ldl_le_p(entry + 12);
            uint64_t offset;
            uint64_t bytes;

            if (!vc4_partition_type_is_fat(type) ||
                start_lba == 0 || sector_count == 0) {
                continue;
            }

            offset = (uint64_t)start_lba * MBR_SECTOR_SIZE;
            bytes = (uint64_t)sector_count * MBR_SECTOR_SIZE;
            if (offset > volume->image_size ||
                bytes > (uint64_t)volume->image_size - offset) {
                error_setg(errp,
                           "FAT partition %u extends beyond the SD image",
                           i + 1);
                return false;
            }

            volume->volume_offset = offset;
            volume->volume_size = bytes;
            volume->partition_lba = start_lba;
            return true;
        }
    }

    if (vc4_fat_bpb_plausible(sector)) {
        volume->volume_offset = 0;
        volume->volume_size = volume->image_size;
        volume->partition_lba = 0;
        return true;
    }

    error_setg(errp,
               "Raspberry Pi boot ROM found no FAT16/FAT32 boot volume "
               "on the SD image");
    return false;
}

static bool vc4_parse_fat_volume(VC4FatVolume *volume, Error **errp)
{
    uint8_t sector[MBR_SECTOR_SIZE];
    uint16_t total_sectors16;
    uint16_t fat_sectors16;
    uint32_t total_sectors32;
    uint32_t fat_sectors32;
    uint64_t overhead_sectors;
    uint64_t data_sectors;
    uint64_t total_bytes;
    uint64_t fat_bytes;
    uint64_t fat_entries;
    unsigned fat_entry_bytes;

    if (!vc4_bootrom_pread(volume->blk, volume->image_size,
                           volume->volume_offset, sizeof(sector),
                           sector, errp)) {
        return false;
    }
    if (!vc4_fat_bpb_plausible(sector)) {
        error_setg(errp, "Raspberry Pi boot partition has an invalid FAT BPB");
        return false;
    }

    volume->bytes_per_sector = lduw_le_p(sector + 11);
    volume->sectors_per_cluster = sector[13];
    volume->reserved_sectors = lduw_le_p(sector + 14);
    volume->fat_count = sector[16];
    volume->root_entries = lduw_le_p(sector + 17);
    total_sectors16 = lduw_le_p(sector + 19);
    fat_sectors16 = lduw_le_p(sector + 22);
    total_sectors32 = ldl_le_p(sector + 32);
    fat_sectors32 = ldl_le_p(sector + 36);

    volume->total_sectors = total_sectors16 ?
                            total_sectors16 : total_sectors32;
    volume->fat_sectors = fat_sectors16 ? fat_sectors16 : fat_sectors32;
    if (volume->total_sectors == 0 || volume->fat_sectors == 0) {
        error_setg(errp, "Raspberry Pi boot partition has no FAT geometry");
        return false;
    }

    volume->root_dir_sectors =
        ((uint32_t)volume->root_entries * FAT_DIRECTORY_ENTRY_SIZE +
         volume->bytes_per_sector - 1) / volume->bytes_per_sector;
    overhead_sectors = volume->reserved_sectors +
                       (uint64_t)volume->fat_count * volume->fat_sectors +
                       volume->root_dir_sectors;
    if (overhead_sectors >= volume->total_sectors) {
        error_setg(errp, "Raspberry Pi boot partition FAT geometry overlaps");
        return false;
    }

    total_bytes = (uint64_t)volume->total_sectors *
                  volume->bytes_per_sector;
    if (total_bytes > volume->volume_size) {
        error_setg(errp,
                   "FAT volume is larger than its enclosing SD partition");
        return false;
    }

    data_sectors = volume->total_sectors - overhead_sectors;
    volume->cluster_count = data_sectors / volume->sectors_per_cluster;
    if (volume->cluster_count < FAT12_MAX_CLUSTERS) {
        error_setg(errp,
                   "Raspberry Pi boot ROM FAT12 volumes are not yet supported");
        return false;
    }
    volume->fat32 = volume->cluster_count >= FAT16_MAX_CLUSTERS;

    if (volume->fat32) {
        volume->root_cluster = ldl_le_p(sector + 44) & 0x0fffffff;
        if (volume->root_entries != 0 || volume->root_cluster < 2) {
            error_setg(errp, "Raspberry Pi FAT32 root directory is invalid");
            return false;
        }
    } else {
        volume->root_cluster = 0;
        if (volume->root_entries == 0) {
            error_setg(errp, "Raspberry Pi FAT16 root directory is missing");
            return false;
        }
    }

    volume->cluster_bytes = volume->bytes_per_sector *
                            volume->sectors_per_cluster;
    if (volume->cluster_bytes == 0 ||
        volume->cluster_bytes > FAT_MAX_CLUSTER_BYTES) {
        error_setg(errp, "Raspberry Pi FAT cluster size is unsupported");
        return false;
    }

    volume->fat_offset = volume->volume_offset +
                         (uint64_t)volume->reserved_sectors *
                         volume->bytes_per_sector;
    volume->root_offset = volume->fat_offset +
                          (uint64_t)volume->fat_count *
                          volume->fat_sectors *
                          volume->bytes_per_sector;
    volume->data_offset = volume->root_offset +
                          (uint64_t)volume->root_dir_sectors *
                          volume->bytes_per_sector;

    fat_entry_bytes = volume->fat32 ? 4 : 2;
    fat_bytes = (uint64_t)volume->fat_sectors *
                volume->bytes_per_sector;
    fat_entries = fat_bytes / fat_entry_bytes;
    if (fat_entries < (uint64_t)volume->cluster_count + 2) {
        error_setg(errp, "Raspberry Pi boot partition FAT is too small");
        return false;
    }

    return true;
}

static bool vc4_cluster_offset(const VC4FatVolume *volume, uint32_t cluster,
                               uint64_t *offset, Error **errp)
{
    uint64_t index;

    if (cluster < 2) {
        error_setg(errp, "invalid FAT cluster %u", cluster);
        return false;
    }
    index = cluster - 2;
    if (index >= volume->cluster_count) {
        error_setg(errp, "FAT cluster %u is outside the data region", cluster);
        return false;
    }

    *offset = volume->data_offset + index * volume->cluster_bytes;
    return true;
}

static bool vc4_fat_next_cluster(const VC4FatVolume *volume,
                                 uint32_t cluster, uint32_t *next,
                                 bool *end_of_chain, Error **errp)
{
    uint8_t entry[4];
    unsigned entry_size = volume->fat32 ? 4 : 2;
    uint64_t offset = volume->fat_offset +
                      (uint64_t)cluster * entry_size;
    uint32_t value;
    uint32_t eoc_min;
    uint32_t reserved_min;
    uint32_t bad_cluster;

    if (!vc4_bootrom_pread(volume->blk, volume->image_size,
                           offset, entry_size, entry, errp)) {
        return false;
    }

    if (volume->fat32) {
        value = ldl_le_p(entry) & 0x0fffffff;
        eoc_min = 0x0ffffff8;
        reserved_min = 0x0ffffff0;
        bad_cluster = 0x0ffffff7;
    } else {
        value = lduw_le_p(entry);
        eoc_min = 0xfff8;
        reserved_min = 0xfff0;
        bad_cluster = 0xfff7;
    }

    if (value >= eoc_min) {
        *end_of_chain = true;
        *next = 0;
        return true;
    }
    if (value == bad_cluster || value < 2 || value >= reserved_min) {
        error_setg(errp,
                   "invalid FAT chain value 0x%x after cluster %u",
                   value, cluster);
        return false;
    }

    *end_of_chain = false;
    *next = value;
    return true;
}

static VC4DirScanResult vc4_scan_directory(const VC4FatVolume *volume,
                                            const uint8_t *directory,
                                            size_t bytes,
                                            uint32_t *first_cluster,
                                            uint32_t *file_size)
{
    size_t offset;

    for (offset = 0; offset + FAT_DIRECTORY_ENTRY_SIZE <= bytes;
         offset += FAT_DIRECTORY_ENTRY_SIZE) {
        const uint8_t *entry = directory + offset;
        uint8_t attributes;
        uint32_t cluster;

        if (entry[0] == 0x00) {
            return VC4_DIR_END;
        }
        if (entry[0] == 0xe5) {
            continue;
        }

        attributes = entry[11];
        if (attributes == FAT_ATTR_LONG_NAME ||
            attributes & (FAT_ATTR_VOLUME_ID | FAT_ATTR_DIRECTORY)) {
            continue;
        }
        if (memcmp(entry, bootcode_short_name,
                   sizeof(bootcode_short_name)) != 0) {
            continue;
        }

        cluster = lduw_le_p(entry + 26);
        if (volume->fat32) {
            cluster |= (uint32_t)lduw_le_p(entry + 20) << 16;
            cluster &= 0x0fffffff;
        }
        *first_cluster = cluster;
        *file_size = ldl_le_p(entry + 28);
        return VC4_DIR_FOUND;
    }

    return VC4_DIR_CONTINUE;
}

static bool vc4_find_bootcode(const VC4FatVolume *volume,
                              uint32_t *first_cluster,
                              uint32_t *file_size,
                              Error **errp)
{
    if (!volume->fat32) {
        size_t root_bytes = (size_t)volume->root_entries *
                            FAT_DIRECTORY_ENTRY_SIZE;
        g_autofree uint8_t *root = NULL;
        VC4DirScanResult result;

        if (root_bytes == 0 || root_bytes > FAT_MAX_ROOT_BYTES) {
            error_setg(errp, "Raspberry Pi FAT16 root directory is too large");
            return false;
        }
        root = g_malloc(root_bytes);
        if (!vc4_bootrom_pread(volume->blk, volume->image_size,
                               volume->root_offset, root_bytes, root, errp)) {
            return false;
        }

        result = vc4_scan_directory(volume, root, root_bytes,
                                    first_cluster, file_size);
        if (result == VC4_DIR_FOUND) {
            return true;
        }
    } else {
        g_autoptr(GHashTable) seen =
            g_hash_table_new(g_direct_hash, g_direct_equal);
        g_autofree uint8_t *cluster_data =
            g_malloc(volume->cluster_bytes);
        uint32_t cluster = volume->root_cluster;
        uint32_t next;
        bool end_of_chain;
        uint64_t cluster_offset;

        while (true) {
            VC4DirScanResult result;

            if (g_hash_table_contains(seen, GUINT_TO_POINTER(cluster))) {
                error_setg(errp, "loop in Raspberry Pi FAT32 root directory");
                return false;
            }
            g_hash_table_add(seen, GUINT_TO_POINTER(cluster));

            if (!vc4_cluster_offset(volume, cluster,
                                     &cluster_offset, errp) ||
                !vc4_bootrom_pread(volume->blk, volume->image_size,
                                   cluster_offset, volume->cluster_bytes,
                                   cluster_data, errp)) {
                return false;
            }

            result = vc4_scan_directory(volume, cluster_data,
                                         volume->cluster_bytes,
                                         first_cluster, file_size);
            if (result == VC4_DIR_FOUND) {
                return true;
            }
            if (result == VC4_DIR_END) {
                break;
            }

            if (!vc4_fat_next_cluster(volume, cluster, &next,
                                      &end_of_chain, errp)) {
                return false;
            }
            if (end_of_chain) {
                break;
            }
            cluster = next;
        }
    }

    error_setg(errp,
               "Raspberry Pi boot ROM could not find bootcode.bin "
               "in the FAT root directory");
    return false;
}

static bool vc4_load_bootcode_clusters(const VC4FatVolume *volume,
                                       uint32_t first_cluster,
                                       uint32_t file_size,
                                       uint8_t *boot_cache,
                                       size_t boot_cache_size,
                                       Error **errp)
{
    g_autoptr(GHashTable) seen =
        g_hash_table_new(g_direct_hash, g_direct_equal);
    uint32_t cluster = first_cluster;
    size_t done = 0;

    if (file_size == 0) {
        error_setg(errp, "Raspberry Pi bootcode.bin is empty");
        return false;
    }
    if (file_size > boot_cache_size) {
        error_setg(errp,
                   "Raspberry Pi bootcode.bin is %u bytes, larger than the "
                   "%zu-byte VPU boot cache",
                   file_size, boot_cache_size);
        return false;
    }

    memset(boot_cache, 0, boot_cache_size);
    while (done < file_size) {
        uint64_t cluster_offset;
        size_t bytes = MIN((size_t)volume->cluster_bytes,
                           (size_t)file_size - done);
        uint32_t next;
        bool end_of_chain;

        if (g_hash_table_contains(seen, GUINT_TO_POINTER(cluster))) {
            error_setg(errp, "loop in Raspberry Pi bootcode.bin FAT chain");
            return false;
        }
        g_hash_table_add(seen, GUINT_TO_POINTER(cluster));

        if (!vc4_cluster_offset(volume, cluster, &cluster_offset, errp) ||
            !vc4_bootrom_pread(volume->blk, volume->image_size,
                               cluster_offset, bytes,
                               boot_cache + done, errp)) {
            return false;
        }
        done += bytes;
        if (done == file_size) {
            break;
        }

        if (!vc4_fat_next_cluster(volume, cluster, &next,
                                  &end_of_chain, errp)) {
            return false;
        }
        if (end_of_chain) {
            error_setg(errp,
                       "bootcode.bin FAT chain ended after %zu of %u bytes",
                       done, file_size);
            return false;
        }
        cluster = next;
    }

    return true;
}

bool vc4_raspi3_bootrom_load(BlockBackend *blk,
                             uint8_t *boot_cache,
                             size_t boot_cache_size,
                             VC4Raspi3BootInfo *info,
                             Error **errp)
{
    VC4FatVolume volume = {
        .blk = blk,
    };
    uint32_t first_cluster;
    uint32_t file_size;

    if (!blk) {
        error_setg(errp, "Raspberry Pi boot ROM has no SD-card backend");
        return false;
    }
    if (!boot_cache || boot_cache_size == 0) {
        error_setg(errp, "Raspberry Pi VPU boot cache is unavailable");
        return false;
    }

    volume.image_size = blk_getlength(blk);
    if (volume.image_size < 0) {
        error_setg_errno(errp, -volume.image_size,
                         "could not determine Raspberry Pi SD image size");
        return false;
    }
    if (volume.image_size < MBR_SECTOR_SIZE) {
        error_setg(errp, "Raspberry Pi SD image is too small");
        return false;
    }

    if (!vc4_find_fat_volume(&volume, errp) ||
        !vc4_parse_fat_volume(&volume, errp) ||
        !vc4_find_bootcode(&volume, &first_cluster, &file_size, errp) ||
        !vc4_load_bootcode_clusters(&volume, first_cluster, file_size,
                                    boot_cache, boot_cache_size, errp)) {
        return false;
    }

    if (info) {
        *info = (VC4Raspi3BootInfo) {
            .partition_lba = volume.partition_lba,
            .file_size = file_size,
            .first_cluster = first_cluster,
            .bytes_per_sector = volume.bytes_per_sector,
            .sectors_per_cluster = volume.sectors_per_cluster,
            .fat32 = volume.fat32,
        };
    }
    return true;
}
