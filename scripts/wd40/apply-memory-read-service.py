#!/usr/bin/env python3
"""Expose bounded virtual and physical guest-memory reads through QMP."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def ensure_include(path: str, include: str, anchor: str) -> None:
    file_path, text = load(path)
    count = text.count(include)
    if count == 1:
        return
    if count != 0:
        raise RuntimeError(
            f"{path}: expected at most one {include!r}, found {count}"
        )
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(
            f"{path}: expected one include insertion site, "
            f"found {anchor_count}"
        )
    store(file_path, text.replace(anchor, include + anchor, 1))


def insert_before_once(
    path: str,
    anchor: str,
    block: str,
    *,
    owned_markers: tuple[str, ...],
) -> None:
    file_path, text = load(path)
    marker_counts = [text.count(marker) for marker in owned_markers]
    if all(count == 1 for count in marker_counts):
        return
    if any(marker_counts):
        raise RuntimeError(
            f"{path}: partially applied memory-read block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one insertion site, found {count}"
        )
    store(file_path, text.replace(anchor, block + anchor, 1))


def main() -> None:
    insert_before_once(
        "qapi/machine.json",
        """##
# @memsave:
""",
        """##
# @WD40MemorySpace:
#
# Guest address space used by a WD40 memory read.
#
# @virtual: translate through the selected CPU's current MMU state
#
# @physical: read the system physical address space directly
#
# Since: 11.2
##
{ 'enum': 'WD40MemorySpace',
  'data': [ 'virtual', 'physical' ] }

##
# @WD40MemoryRead:
#
# Exact bytes returned by a bounded guest-memory read.
#
# @space: address space used for the read
#
# @address: first guest address read
#
# @bytes: number of bytes returned
#
# @cpu-index: selected virtual CPU for a virtual read
#
# @data: lowercase hexadecimal encoding of the returned bytes
#
# Since: 11.2
##
{ 'struct': 'WD40MemoryRead',
  'data': { 'space': 'WD40MemorySpace',
            'address': 'uint64', 'bytes': 'uint64',
            '*cpu-index': 'int', 'data': 'str' } }

##
# @x-wd40-read-memory:
#
# Read between 1 byte and 1 MiB from guest virtual or physical
# memory and return the exact bytes through QMP.
#
# @space: address space used for the read
#
# @address: first guest address to read
#
# @size: number of bytes to read
#
# @cpu-index: virtual CPU used for MMU translation.  It defaults to
#     CPU 0 for virtual reads and is rejected for physical reads.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: a bounded raw memory read
#
# Since: 11.2
##
{ 'command': 'x-wd40-read-memory',
  'data': { 'space': 'WD40MemorySpace',
            'address': 'uint64', 'size': 'uint64',
            '*cpu-index': 'int' },
  'returns': 'WD40MemoryRead',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'enum': 'WD40MemorySpace'",
            "'struct': 'WD40MemoryRead'",
            "'command': 'x-wd40-read-memory'",
        ),
    )

    for include in (
        '#include "system/address-spaces.h"\n',
        '#include "system/hw_accel.h"\n',
        '#include "system/memory.h"\n',
    ):
        ensure_include(
            "system/physmem-qmp-cmds.c",
            include,
            '#include "system/physmem.h"\n',
        )

    insert_before_once(
        "system/physmem-qmp-cmds.c",
        """void qmp_memsave(uint64_t addr, uint64_t size, const char *filename,
""",
        r"""#define WD40_MEMORY_READ_MAX (1024U * 1024U)

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

""",
        owned_markers=(
            "#define WD40_MEMORY_READ_MAX",
            "qmp_x_wd40_read_memory",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Cross-architecture CPU register snapshots
-----------------------------------------
""",
        """Bounded guest-memory reads
--------------------------

``x-wd40-read-memory`` returns between one byte and one MiB of raw guest
memory as lowercase hexadecimal.  Virtual reads use the selected CPU's
debugger translation path and synchronize accelerator state first.  Physical
reads use the system address space directly and preserve memory-transaction
errors instead of silently returning filler bytes.

The command rejects wrapped address ranges, invalid CPU selections, and a CPU
selection on physical reads.  It is unavailable while incoming migration is
loading guest RAM.  Like the register snapshot command, it does not pause a
running machine; frontends should stop the guest when they require a coherent
view across multiple reads.

These are debugger accesses, not side-effect-free RAM snapshots.  A virtual or
physical address mapped to MMIO can invoke the device's read callback.  The
bounded response is intended for interactive memory panes and inspectors;
larger captures should continue to use file-oriented dump mechanisms.

""",
        owned_markers=(
            "Bounded guest-memory reads",
            "x-wd40-read-memory",
        ),
    )


if __name__ == "__main__":
    main()
