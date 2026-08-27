#!/usr/bin/env python3
"""Expose cross-architecture CPU register snapshots through QMP."""

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
        raise RuntimeError(f"{path}: expected at most one {include!r}, found {count}")
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(
            f"{path}: expected one include insertion site, found {anchor_count}"
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
            f"{path}: partially applied CPU register snapshot block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected one insertion site, found {count}")
    store(file_path, text.replace(anchor, block + anchor, 1))


def main() -> None:
    insert_before_once(
        "qapi/misc.json",
        """##\n# @LogCategoryInfo:\n""",
        """##
# @WD40CPURegister:
#
# One register from QEMU's active GDB register model.
#
# @number: architecture-defined GDB register number
#
# @name: GDB register name, or ``gdb-reg-N`` when this CPU has no
#     name metadata for register N
#
# @feature: GDB feature containing the register, when available
#
# @described: whether @name came from GDB feature metadata
#
# @available: whether the CPU returned a value for this register
#
# @bytes: number of value bytes; zero when @available is false
#
# @value: lowercase hexadecimal encoding of the bytes returned by the
#     CPU's GDB register callback
#
# Since: 11.2
##
{ 'struct': 'WD40CPURegister',
  'data': { 'number': 'int', 'name': 'str', '*feature': 'str',
            'described': 'bool', 'available': 'bool',
            'bytes': 'uint64', '*value': 'str' } }

##
# @WD40CPURegisterSnapshot:
#
# A synchronized register snapshot for one realized virtual CPU.
#
# @cpu-index: QEMU virtual CPU index
#
# @target: canonical QEMU target name
#
# @target-bits: target ``long`` width
#
# @target-big-endian: whether the target's default byte order is big
#     endian
#
# @qom-type: concrete CPU QOM type
#
# @registers: registers sorted by GDB register number
#
# Since: 11.2
##
{ 'struct': 'WD40CPURegisterSnapshot',
  'data': { 'cpu-index': 'int', 'target': 'str',
            'target-bits': 'uint64',
            'target-big-endian': 'bool', 'qom-type': 'str',
            'registers': [ 'WD40CPURegister' ] } }

##
# @x-wd40-query-cpu-registers:
#
# Read one virtual CPU through the same register callbacks and
# metadata used by QEMU's GDB stub.  This avoids parsing
# architecture-specific
# ``info registers`` text while retaining supplemental register sets.
#
# @cpu-index: virtual CPU index; defaults to the first realized CPU
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: synchronized register snapshot
#
# Since: 11.2
##
{ 'command': 'x-wd40-query-cpu-registers',
  'data': { '*cpu-index': 'int' },
  'returns': 'WD40CPURegisterSnapshot',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'struct': 'WD40CPURegister'",
            "'struct': 'WD40CPURegisterSnapshot'",
            "'command': 'x-wd40-query-cpu-registers'",
        ),
    )

    for include in (
        '#include "exec/gdbstub.h"\n',
        '#include "hw/core/cpu.h"\n',
        '#include "qemu/target-info.h"\n',
        '#include "system/hw_accel.h"\n',
    ):
        ensure_include(
            "monitor/qmp-cmds.c",
            include,
            '#include "monitor-internal.h"\n',
        )

    insert_before_once(
        "monitor/qmp-cmds.c",
        """static LogCategoryInfoList *qmp_log_category_info_list(void)\n""",
        r'''typedef struct WD40RegisterDescriptor {
    int number;
    const char *name;
    const char *feature;
} WD40RegisterDescriptor;

static bool wd40_register_descriptor_present(const GArray *descriptors,
                                               int number)
{
    guint i;

    for (i = 0; i < descriptors->len; i++) {
        const WD40RegisterDescriptor *descriptor =
            &g_array_index(descriptors, WD40RegisterDescriptor, i);

        if (descriptor->number == number) {
            return true;
        }
    }
    return false;
}

static int wd40_register_descriptor_compare(const void *left,
                                             const void *right)
{
    const WD40RegisterDescriptor *left_descriptor = left;
    const WD40RegisterDescriptor *right_descriptor = right;

    return (left_descriptor->number > right_descriptor->number) -
           (left_descriptor->number < right_descriptor->number);
}

static char *wd40_register_value_to_hex(const GByteArray *value)
{
    static const char digits[] = "0123456789abcdef";
    char *hex = g_malloc_n((gsize)value->len + 1, 2);
    guint i;

    for (i = 0; i < value->len; i++) {
        hex[i * 2] = digits[value->data[i] >> 4];
        hex[i * 2 + 1] = digits[value->data[i] & 0x0f];
    }
    hex[value->len * 2] = '\0';
    return hex;
}

static CPUState *wd40_cpu_by_index(bool has_cpu_index, int64_t cpu_index)
{
    CPUState *cpu;

    if (!has_cpu_index) {
        return first_cpu;
    }

    CPU_FOREACH(cpu) {
        if (cpu->cpu_index == cpu_index) {
            return cpu;
        }
    }
    return NULL;
}

WD40CPURegisterSnapshot *
qmp_x_wd40_query_cpu_registers(bool has_cpu_index, int64_t cpu_index,
                                Error **errp)
{
    CPUState *cpu = wd40_cpu_by_index(has_cpu_index, cpu_index);
    GArray *gdb_descriptors = NULL;
    GArray *descriptors = NULL;
    GByteArray *value = NULL;
    WD40CPURegisterSnapshot *snapshot = NULL;
    WD40CPURegisterList **tail;
    guint i;

    if (!cpu) {
        if (has_cpu_index) {
            error_setg(errp, "CPU index %" PRId64 " does not exist",
                       cpu_index);
        } else {
            error_setg(errp, "No realized CPU is available");
        }
        return NULL;
    }

    cpu_synchronize_state(cpu);
    gdb_descriptors = gdb_get_register_list(cpu);
    descriptors = g_array_new(false, false,
                              sizeof(WD40RegisterDescriptor));

    for (i = 0; i < gdb_descriptors->len; i++) {
        const GDBRegDesc *gdb_descriptor =
            &g_array_index(gdb_descriptors, GDBRegDesc, i);
        WD40RegisterDescriptor descriptor = {
            .number = gdb_descriptor->gdb_reg,
            .name = gdb_descriptor->name,
            .feature = gdb_descriptor->feature_name,
        };

        if (wd40_register_descriptor_present(descriptors,
                                             descriptor.number)) {
            error_setg(errp, "GDB register number %d is duplicated",
                       descriptor.number);
            goto fail;
        }
        g_array_append_val(descriptors, descriptor);
    }

    for (i = 0; i < cpu->cc->gdb_num_core_regs; i++) {
        WD40RegisterDescriptor descriptor = {
            .number = i,
        };

        if (!wd40_register_descriptor_present(descriptors,
                                              descriptor.number)) {
            g_array_append_val(descriptors, descriptor);
        }
    }

    if (descriptors->len == 0) {
        error_setg(errp, "CPU type '%s' exposes no GDB registers",
                   object_get_typename(OBJECT(cpu)));
        goto fail;
    }
    g_array_sort(descriptors, wd40_register_descriptor_compare);

    snapshot = g_new0(WD40CPURegisterSnapshot, 1);
    snapshot->cpu_index = cpu->cpu_index;
    snapshot->target = g_strdup(target_name());
    snapshot->target_bits = target_long_bits();
    snapshot->target_big_endian = target_big_endian();
    snapshot->qom_type = g_strdup(object_get_typename(OBJECT(cpu)));
    tail = &snapshot->registers;
    value = g_byte_array_new();

    for (i = 0; i < descriptors->len; i++) {
        const WD40RegisterDescriptor *descriptor =
            &g_array_index(descriptors, WD40RegisterDescriptor, i);
        WD40CPURegister *info;
        WD40CPURegisterList *entry;
        bool name_valid;
        int bytes;

        g_byte_array_set_size(value, 0);
        bytes = gdb_read_register(cpu, value, descriptor->number);
        if (bytes < 0 || (guint)bytes != value->len) {
            error_setg(errp,
                       "GDB register %d returned inconsistent size %d/%u",
                       descriptor->number, bytes, value->len);
            goto fail;
        }

        info = g_new0(WD40CPURegister, 1);
        info->number = descriptor->number;
        name_valid = descriptor->name &&
                     g_utf8_validate(descriptor->name, -1, NULL);
        info->described = name_valid;
        info->name = name_valid
            ? g_strdup(descriptor->name)
            : g_strdup_printf("gdb-reg-%d", descriptor->number);
        if (descriptor->feature &&
            g_utf8_validate(descriptor->feature, -1, NULL)) {
            info->feature = g_strdup(descriptor->feature);
        }
        info->available = bytes > 0;
        info->bytes = value->len;
        if (info->available) {
            info->value = wd40_register_value_to_hex(value);
        }

        entry = g_new0(WD40CPURegisterList, 1);
        entry->value = info;
        *tail = entry;
        tail = &entry->next;
    }

    g_byte_array_unref(value);
    g_array_free(descriptors, true);
    g_array_free(gdb_descriptors, true);
    return snapshot;

fail:
    if (value) {
        g_byte_array_unref(value);
    }
    if (descriptors) {
        g_array_free(descriptors, true);
    }
    if (gdb_descriptors) {
        g_array_free(gdb_descriptors, true);
    }
    qapi_free_WD40CPURegisterSnapshot(snapshot);
    return NULL;
}

''',
        owned_markers=(
            "typedef struct WD40RegisterDescriptor",
            "qmp_x_wd40_query_cpu_registers",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Structured log-category control\n-------------------------------\n""",
        """Cross-architecture CPU register snapshots
-----------------------------------------

``x-wd40-query-cpu-registers`` reads the selected virtual CPU through QEMU's
GDB register registry and callbacks.  It returns canonical target and CPU type
metadata plus registers sorted by GDB number, including dynamically registered
supplemental feature sets.  Architectures that have not supplied register-name
metadata still expose their core register numbers with ``gdb-reg-N`` names.

Register values are the exact byte sequences produced by the target's GDB
callback, encoded as lowercase hexadecimal rather than converted through an
architecture-specific integer formatter.  The snapshot reports target word
size and default endianness, but clients should retain the register's GDB
feature when interpreting vector, floating-point, or special register layouts.

The command synchronizes accelerator state before reading.
It does not pause a running machine, so debugger frontends should issue
``stop`` first when they need a coherent snapshot across all registers or
virtual CPUs.  This gives TTYphoon a typed cross-target register foundation
without scraping ``info registers`` output or adding per-architecture monitor
parsers.

""",
        owned_markers=(
            "Cross-architecture CPU register snapshots",
            "x-wd40-query-cpu-registers",
        ),
    )


if __name__ == "__main__":
    main()
