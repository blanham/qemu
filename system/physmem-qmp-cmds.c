/*
 * QMP commands to dump physical memory
 *
 * Copyright (c) 2003-2008 Fabrice Bellard
 * Copyright (c) Qualcomm Technologies, Inc. and/or its subsidiaries.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "qapi/qapi-commands-machine.h"
#include "qapi/qmp/qerror.h"
#include "qemu/target-info.h"
#include "hw/core/cpu.h"
#include "system/address-spaces.h"
#include "system/hw_accel.h"
#include "system/memory.h"
#include "system/physmem.h"
#include "migration/misc.h"

#define WD40_MEMORY_READ_MAX (1024U * 1024U)

static char *wd40_memory_bytes_to_hex(const uint8_t *bytes, size_t length)
{
    static const char digits[] = "0123456789abcdef";
    char *hex = g_malloc(length * 2 + 1);
    size_t i;

    for (i = 0; i < length; i++) {
        hex[i * 2] = digits[bytes[i] >> 4];
        hex[i * 2 + 1] = digits[bytes[i] & 0x0f];
    }
    hex[length * 2] = '\0';
    return hex;
}

WD40MemoryRead *
qmp_x_wd40_read_memory(WD40MemorySpace space, uint64_t address,
                        uint64_t size, bool has_cpu_index,
                        int64_t cpu_index, Error **errp)
{
    g_autofree uint8_t *buffer = NULL;
    WD40MemoryRead *result;
    CPUState *cpu = NULL;
    MemTxResult transaction;

    if (size == 0 || size > WD40_MEMORY_READ_MAX) {
        error_setg(errp,
                   "size must be between 1 and %u bytes",
                   WD40_MEMORY_READ_MAX);
        return NULL;
    }
    if (address > UINT64_MAX - (size - 1)) {
        error_setg(errp, "address range wraps past UINT64_MAX");
        return NULL;
    }
    if (migration_guest_ram_loading()) {
        error_setg(errp, "Guest memory access not allowed during migration");
        return NULL;
    }

    buffer = g_malloc((gsize)size);
    switch (space) {
    case WD40_MEMORY_SPACE_VIRTUAL:
        if (!has_cpu_index) {
            cpu_index = 0;
        }
        cpu = qemu_get_cpu(cpu_index);
        if (!cpu) {
            error_setg(errp, QERR_INVALID_PARAMETER_VALUE,
                       "cpu-index", "a CPU number");
            return NULL;
        }

        cpu_synchronize_state(cpu);
        if (cpu_memory_rw_debug(cpu, address, buffer,
                                (size_t)size, false) != 0) {
            error_setg(errp,
                       "Virtual memory read failed at 0x%016" PRIx64
                       " for %" PRIu64 " bytes",
                       address, size);
            return NULL;
        }
        break;

    case WD40_MEMORY_SPACE_PHYSICAL:
        if (has_cpu_index) {
            error_setg(errp,
                       "cpu-index is only valid for virtual memory");
            return NULL;
        }
        transaction = address_space_read(&address_space_memory, address,
                                         MEMTXATTRS_UNSPECIFIED,
                                         buffer, size);
        if (transaction != MEMTX_OK) {
            error_setg(errp,
                       "Physical memory read failed at 0x%016" PRIx64
                       " for %" PRIu64
                       " bytes (transaction result 0x%x)",
                       address, size, (unsigned int)transaction);
            return NULL;
        }
        break;

    default:
        g_assert_not_reached();
    }

    result = g_new0(WD40MemoryRead, 1);
    result->space = space;
    result->address = address;
    result->bytes = size;
    if (space == WD40_MEMORY_SPACE_VIRTUAL) {
        result->has_cpu_index = true;
        result->cpu_index = cpu_index;
    }
    result->data = wd40_memory_bytes_to_hex(buffer, (size_t)size);
    return result;
}

WD40AddressTranslation *
qmp_x_wd40_translate_address(uint64_t address, bool has_cpu_index,
                              int64_t cpu_index, Error **errp)
{
    TranslateForDebugResult translation;
    WD40AddressTranslation *result;
    CPUState *cpu;
    bool translated;

    if (migration_guest_ram_loading()) {
        error_setg(errp, "Guest memory access not allowed during migration");
        return NULL;
    }
    if (!has_cpu_index) {
        cpu_index = 0;
    }
    cpu = qemu_get_cpu(cpu_index);
    if (!cpu) {
        error_setg(errp, QERR_INVALID_PARAMETER_VALUE,
                   "cpu-index", "a CPU number");
        return NULL;
    }

    cpu_synchronize_state(cpu);
    translated = cpu_translate_for_debug(cpu, address, &translation);
    if (translated && translation.lg_page_size >= 64) {
        error_setg(errp,
                   "CPU %" PRId64
                   " returned invalid translation page bits %u",
                   cpu_index, translation.lg_page_size);
        return NULL;
    }

    result = g_new0(WD40AddressTranslation, 1);
    result->cpu_index = cpu_index;
    result->target = g_strdup(target_name());
    result->target_bits = target_long_bits();
    result->target_big_endian = target_big_endian();
    result->qom_type = g_strdup(object_get_typename(OBJECT(cpu)));
    result->virtual_address = address;
    result->translated = translated;

    if (!translated) {
        return result;
    }

    result->has_physical_address = true;
    result->physical_address = translation.physaddr;
    result->has_address_space_index = true;
    result->address_space_index = cpu_asidx_from_attrs(cpu,
                                                       translation.attrs);
    result->has_page_bits = true;
    result->page_bits = translation.lg_page_size;
    result->has_page_size = true;
    result->page_size = UINT64_C(1) << translation.lg_page_size;
    result->attributes = g_new0(WD40MemoryTransactionAttributes, 1);
    result->attributes->unspecified = translation.attrs.unspecified;
    result->attributes->secure = translation.attrs.secure;
    result->attributes->security_space = translation.attrs.space;
    result->attributes->user = translation.attrs.user;
    result->attributes->memory = translation.attrs.memory;
    result->attributes->debug = translation.attrs.debug;
    result->attributes->requester_id = translation.attrs.requester_id;
    result->attributes->pid = translation.attrs.pid;
    result->attributes->address_type = translation.attrs.address_type;
    return result;
}

#define WD40_MEMORY_WRITE_MAX (1024U * 1024U)

static int wd40_memory_hex_digit(char value)
{
    if (value >= '0' && value <= '9') {
        return value - '0';
    }
    if (value >= 'a' && value <= 'f') {
        return value - 'a' + 10;
    }
    if (value >= 'A' && value <= 'F') {
        return value - 'A' + 10;
    }
    return -1;
}

static uint8_t *wd40_memory_hex_to_bytes(const char *data,
                                         size_t *length,
                                         Error **errp)
{
    size_t hex_length = strlen(data);
    uint8_t *bytes;
    size_t i;

    if (hex_length == 0) {
        error_setg(errp, "data must encode at least one byte");
        return NULL;
    }
    if (hex_length & 1) {
        error_setg(errp,
                   "data must contain an even number of hexadecimal digits");
        return NULL;
    }
    if (hex_length / 2 > WD40_MEMORY_WRITE_MAX) {
        error_setg(errp,
                   "data must encode at most %u bytes",
                   WD40_MEMORY_WRITE_MAX);
        return NULL;
    }

    bytes = g_malloc(hex_length / 2);
    for (i = 0; i < hex_length; i += 2) {
        int high = wd40_memory_hex_digit(data[i]);
        int low = wd40_memory_hex_digit(data[i + 1]);

        if (high < 0 || low < 0) {
            error_setg(errp,
                       "data contains a non-hexadecimal character at "
                       "offset %zu",
                       high < 0 ? i : i + 1);
            g_free(bytes);
            return NULL;
        }
        bytes[i / 2] = (high << 4) | low;
    }

    *length = hex_length / 2;
    return bytes;
}

WD40MemoryWrite *
qmp_x_wd40_write_memory(WD40MemorySpace space, uint64_t address,
                         const char *data, bool has_cpu_index,
                         int64_t cpu_index, Error **errp)
{
    g_autofree uint8_t *buffer = NULL;
    WD40MemoryWrite *result;
    CPUState *cpu = NULL;
    MemTxResult transaction;
    size_t size = 0;

    buffer = wd40_memory_hex_to_bytes(data, &size, errp);
    if (!buffer) {
        return NULL;
    }
    if (address > UINT64_MAX - (uint64_t)(size - 1)) {
        error_setg(errp, "address range wraps past UINT64_MAX");
        return NULL;
    }
    if (migration_guest_ram_loading()) {
        error_setg(errp, "Guest memory access not allowed during migration");
        return NULL;
    }

    switch (space) {
    case WD40_MEMORY_SPACE_VIRTUAL:
        if (!has_cpu_index) {
            cpu_index = 0;
        }
        cpu = qemu_get_cpu(cpu_index);
        if (!cpu) {
            error_setg(errp, QERR_INVALID_PARAMETER_VALUE,
                       "cpu-index", "a CPU number");
            return NULL;
        }

        cpu_synchronize_state(cpu);
        if (cpu_memory_rw_debug(cpu, address, buffer, size, true) != 0) {
            error_setg(errp,
                       "Virtual memory write failed at 0x%016" PRIx64
                       " for %zu bytes",
                       address, size);
            return NULL;
        }
        break;

    case WD40_MEMORY_SPACE_PHYSICAL:
        if (has_cpu_index) {
            error_setg(errp,
                       "cpu-index is only valid for virtual memory");
            return NULL;
        }
        transaction = address_space_write(&address_space_memory, address,
                                          MEMTXATTRS_UNSPECIFIED,
                                          buffer, size);
        if (transaction != MEMTX_OK) {
            error_setg(errp,
                       "Physical memory write failed at 0x%016" PRIx64
                       " for %zu bytes (transaction result 0x%x)",
                       address, size, (unsigned int)transaction);
            return NULL;
        }
        break;

    default:
        g_assert_not_reached();
    }

    result = g_new0(WD40MemoryWrite, 1);
    result->space = space;
    result->address = address;
    result->bytes = size;
    if (space == WD40_MEMORY_SPACE_VIRTUAL) {
        result->has_cpu_index = true;
        result->cpu_index = cpu_index;
    }
    return result;
}

void qmp_memsave(uint64_t addr, uint64_t size, const char *filename,
                 bool has_cpu, int64_t cpu_index, Error **errp)
{
    FILE *f;
    uint64_t l;
    CPUState *cpu;
    uint8_t buf[1024];
    uint64_t orig_addr = addr, orig_size = size;

    if (migration_guest_ram_loading()) {
        error_setg(errp, "Guest memory access not allowed during migration");
        return;
    }

    if (!has_cpu) {
        cpu_index = 0;
    }

    cpu = qemu_get_cpu(cpu_index);
    if (cpu == NULL) {
        error_setg(errp, QERR_INVALID_PARAMETER_VALUE, "cpu-index",
                   "a CPU number");
        return;
    }

    f = fopen(filename, "wb");
    if (!f) {
        error_setg_file_open(errp, errno, filename);
        return;
    }

    while (size != 0) {
        l = sizeof(buf);
        if (l > size) {
            l = size;
        }
        if (cpu_memory_rw_debug(cpu, addr, buf, l, 0) != 0) {
            error_setg(errp, "Invalid addr 0x%016" PRIx64 "/size %" PRIu64
                             " specified", orig_addr, orig_size);
            goto exit;
        }
        if (fwrite(buf, 1, l, f) != l) {
            error_setg(errp, "writing memory to '%s' failed",
                       filename);
            goto exit;
        }
        addr += l;
        size -= l;
    }

exit:
    fclose(f);
}

void qmp_pmemsave(uint64_t addr, uint64_t size, const char *filename,
                  Error **errp)
{
    FILE *f;
    uint64_t l;
    uint8_t buf[1024];

    if (migration_guest_ram_loading()) {
        error_setg(errp, "Guest memory access not allowed during migration");
        return;
    }

    f = fopen(filename, "wb");
    if (!f) {
        error_setg_file_open(errp, errno, filename);
        return;
    }

    while (size != 0) {
        l = sizeof(buf);
        if (l > size) {
            l = size;
        }
        physical_memory_read(addr, buf, l);
        if (fwrite(buf, 1, l, f) != l) {
            error_setg(errp, "writing memory to '%s' failed",
                       filename);
            goto exit;
        }
        addr += l;
        size -= l;
    }

exit:
    fclose(f);
}
