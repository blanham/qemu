#!/usr/bin/env python3
"""Expose bounded virtual and physical guest-memory writes through QMP."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 0 and new_count == 1:
        return
    if old_count == 1 and new_count == 0:
        store(file_path, text.replace(old, new, 1))
        return
    raise RuntimeError(
        f"{path}: ambiguous replacement {old!r} -> {new!r}: "
        f"old={old_count}, new={new_count}"
    )


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
            f"{path}: partially applied memory-write block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one insertion site, found {count}"
        )
    store(file_path, text.replace(anchor, block + anchor, 1))


def main() -> None:
    replace_once(
        "qapi/machine.json",
        "Guest address space used by a WD40 memory read.",
        "Guest address space used by a WD40 memory access.",
    )
    replace_once(
        "qapi/machine.json",
        "@physical: read the system physical address space directly",
        "@physical: access the system physical address space directly",
    )

    insert_before_once(
        "qapi/machine.json",
        """##
# @WD40MemoryTransactionAttributes:
""",
        """##
# @WD40MemoryWrite:
#
# Result of a bounded guest-memory write.
#
# @space: address space used for the write
#
# @address: first guest address written
#
# @bytes: number of bytes written
#
# @cpu-index: selected virtual CPU for a virtual write
#
# Since: 11.2
##
{ 'struct': 'WD40MemoryWrite',
  'data': { 'space': 'WD40MemorySpace',
            'address': 'uint64', 'bytes': 'uint64',
            '*cpu-index': 'int' } }

##
# @x-wd40-write-memory:
#
# Write between 1 byte and 1 MiB to guest virtual or physical
# memory from an exact hexadecimal byte string.
#
# Writes are not atomic.  A failure may leave an earlier portion of
# the requested range modified.
#
# @space: address space used for the write
#
# @address: first guest address to write
#
# @data: hexadecimal byte string to write
#
# @cpu-index: virtual CPU used for MMU translation.  It defaults to
#     CPU 0 for virtual writes and is rejected for physical writes.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: the completed bounded memory write
#
# Since: 11.2
##
{ 'command': 'x-wd40-write-memory',
  'data': { 'space': 'WD40MemorySpace',
            'address': 'uint64', 'data': 'str',
            '*cpu-index': 'int' },
  'returns': 'WD40MemoryWrite',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'struct': 'WD40MemoryWrite'",
            "'command': 'x-wd40-write-memory'",
        ),
    )

    insert_before_once(
        "system/physmem-qmp-cmds.c",
        """void qmp_memsave(uint64_t addr, uint64_t size, const char *filename,
""",
        r"""#define WD40_MEMORY_WRITE_MAX (1024U * 1024U)

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

""",
        owned_markers=(
            "#define WD40_MEMORY_WRITE_MAX",
            "static uint8_t *wd40_memory_hex_to_bytes",
            "qmp_x_wd40_write_memory",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Typed virtual-to-physical translation
-------------------------------------
""",
        """Bounded guest-memory writes
---------------------------

``x-wd40-write-memory`` writes between one byte and one MiB from an exact
hexadecimal byte string.  It shares ``WD40MemorySpace`` with bounded reads:
virtual writes use the selected CPU's debugger path, while physical writes use
the system address space and preserve memory-transaction failures.

The command rejects empty, odd-length, non-hexadecimal, oversized, and wrapped
requests before touching guest memory.  CPU selection follows the read
service: CPU 0 is the virtual default, invalid CPU numbers fail, and physical
writes reject ``cpu-index``.

Writes are debugger operations rather than side-effect-free RAM edits.  They
can invoke MMIO callbacks, and virtual debug writes can modify ROM through
QEMU's debugger path.  Neither virtual nor physical writes are atomic.  If a
multi-byte request fails, an earlier portion of the range may already have
been modified; QEMU does not roll it back.  Clients that need all-or-nothing
behavior must arrange their own validation and rollback.

The command synchronizes accelerator state but does not pause a running guest;
clients should issue ``stop`` before read-modify-write work that must be
coherent.

""",
        owned_markers=(
            "Bounded guest-memory writes",
            "x-wd40-write-memory",
            "virtual debug writes can modify ROM",
        ),
    )


if __name__ == "__main__":
    main()
