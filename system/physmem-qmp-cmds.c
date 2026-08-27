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
