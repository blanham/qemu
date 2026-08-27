#!/usr/bin/env python3
"""Expose exact cross-architecture CPU register writes through QMP."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


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
            f"{path}: partially applied CPU register-write block: "
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
# @WD40CPURegisterWrite:
#
# Result of one exact GDB-register write and read-back.
#
# @cpu-index: QEMU virtual CPU index
#
# @number: architecture-defined GDB register number
#
# @name: GDB register name, or ``gdb-reg-N`` when unavailable
#
# @feature: GDB feature containing the register, when available
#
# @described: whether @name came from GDB feature metadata
#
# @bytes: exact register width in bytes
#
# @value: lowercase hexadecimal post-write read-back value
#
# Since: 11.2
##
{ 'struct': 'WD40CPURegisterWrite',
  'data': { 'cpu-index': 'int', 'number': 'int', 'name': 'str',
            '*feature': 'str', 'described': 'bool',
            'bytes': 'uint64', 'value': 'str' } }

##
# @x-wd40-write-cpu-register:
#
# Write one register through QEMU's active GDB register callback.
# The hexadecimal value must encode exactly the current register width.
# The returned value is read back after the callback completes.
#
# @number: architecture-defined GDB register number
#
# @value: exact hexadecimal bytes in GDB register byte order
#
# @cpu-index: virtual CPU index; defaults to the first realized CPU
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: completed register write with post-write read-back
#
# Since: 11.2
##
{ 'command': 'x-wd40-write-cpu-register',
  'data': { 'number': 'int', 'value': 'str',
            '*cpu-index': 'int' },
  'returns': 'WD40CPURegisterWrite',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'struct': 'WD40CPURegisterWrite'",
            "'command': 'x-wd40-write-cpu-register'",
        ),
    )

    insert_before_once(
        "monitor/qmp-cmds.c",
        """static LogCategoryInfoList *qmp_log_category_info_list(void)\n""",
        r'''static int wd40_register_hex_digit(char value)
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

static uint8_t *wd40_register_hex_to_bytes(const char *value,
                                            size_t expected_bytes,
                                            int number,
                                            Error **errp)
{
    size_t hex_length = strlen(value);
    uint8_t *bytes;
    size_t i;

    if (hex_length == 0) {
        error_setg(errp, "register value must encode at least one byte");
        return NULL;
    }
    if (hex_length & 1) {
        error_setg(errp,
                   "register value must contain an even number of "
                   "hexadecimal digits");
        return NULL;
    }
    if (hex_length / 2 != expected_bytes) {
        error_setg(errp,
                   "GDB register %d requires exactly %zu bytes, got %zu",
                   number, expected_bytes, hex_length / 2);
        return NULL;
    }

    bytes = g_malloc(expected_bytes);
    for (i = 0; i < hex_length; i += 2) {
        int high = wd40_register_hex_digit(value[i]);
        int low = wd40_register_hex_digit(value[i + 1]);

        if (high < 0 || low < 0) {
            error_setg(errp,
                       "register value contains a non-hexadecimal "
                       "character at offset %zu",
                       high < 0 ? i : i + 1);
            g_free(bytes);
            return NULL;
        }
        bytes[i / 2] = (high << 4) | low;
    }
    return bytes;
}

static bool wd40_register_descriptor_for_number(
    CPUState *cpu, int64_t number, WD40RegisterDescriptor *descriptor,
    Error **errp)
{
    GArray *gdb_descriptors;
    unsigned matches = 0;
    guint i;

    if (number < 0 || number > INT_MAX) {
        error_setg(errp, "GDB register number must be between 0 and %d",
                   INT_MAX);
        return false;
    }

    memset(descriptor, 0, sizeof(*descriptor));
    descriptor->number = number;
    if (number < cpu->cc->gdb_num_core_regs) {
        matches++;
    }

    gdb_descriptors = gdb_get_register_list(cpu);
    for (i = 0; i < gdb_descriptors->len; i++) {
        const GDBRegDesc *candidate =
            &g_array_index(gdb_descriptors, GDBRegDesc, i);

        if (candidate->gdb_reg != number) {
            continue;
        }
        if (matches != 0) {
            error_setg(errp,
                       "CPU type '%s' exposes GDB register %" PRId64
                       " more than once",
                       object_get_typename(OBJECT(cpu)), number);
            g_array_free(gdb_descriptors, true);
            return false;
        }
        descriptor->name = candidate->name;
        descriptor->feature = candidate->feature_name;
        matches++;
    }
    g_array_free(gdb_descriptors, true);

    if (matches == 0) {
        error_setg(errp, "CPU type '%s' has no GDB register %" PRId64,
                   object_get_typename(OBJECT(cpu)), number);
        return false;
    }
    return true;
}

WD40CPURegisterWrite *
qmp_x_wd40_write_cpu_register(int64_t number, const char *value,
                               bool has_cpu_index, int64_t cpu_index,
                               Error **errp)
{
    CPUState *cpu = wd40_cpu_by_index(has_cpu_index, cpu_index);
    WD40RegisterDescriptor descriptor;
    GByteArray *register_value = NULL;
    g_autofree uint8_t *buffer = NULL;
    WD40CPURegisterWrite *result = NULL;
    bool name_valid;
    int bytes;

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
    if (!wd40_register_descriptor_for_number(cpu, number, &descriptor,
                                             errp)) {
        return NULL;
    }

    register_value = g_byte_array_new();
    bytes = gdb_read_register(cpu, register_value, descriptor.number);
    if (bytes < 0 || (guint)bytes != register_value->len) {
        error_setg(errp,
                   "GDB register %d returned inconsistent size %d/%u",
                   descriptor.number, bytes, register_value->len);
        goto fail;
    }
    if (bytes == 0) {
        error_setg(errp, "GDB register %d is not available",
                   descriptor.number);
        goto fail;
    }

    buffer = wd40_register_hex_to_bytes(value, register_value->len,
                                         descriptor.number, errp);
    if (!buffer) {
        goto fail;
    }

    bytes = gdb_write_register(cpu, buffer, descriptor.number);
    if (bytes == 0) {
        error_setg(errp, "GDB register %d is not writable",
                   descriptor.number);
        goto fail;
    }
    if (bytes < 0 || (guint)bytes != register_value->len) {
        error_setg(errp,
                   "GDB register %d wrote inconsistent size %d/%u",
                   descriptor.number, bytes, register_value->len);
        goto fail;
    }

    g_byte_array_set_size(register_value, 0);
    bytes = gdb_read_register(cpu, register_value, descriptor.number);
    if (bytes < 0 || (guint)bytes != register_value->len ||
        (guint)bytes != strlen(value) / 2) {
        error_setg(errp,
                   "GDB register %d returned inconsistent post-write "
                   "size %d/%u",
                   descriptor.number, bytes, register_value->len);
        goto fail;
    }

    result = g_new0(WD40CPURegisterWrite, 1);
    result->cpu_index = cpu->cpu_index;
    result->number = descriptor.number;
    name_valid = descriptor.name &&
                 g_utf8_validate(descriptor.name, -1, NULL);
    result->described = name_valid;
    result->name = name_valid
        ? g_strdup(descriptor.name)
        : g_strdup_printf("gdb-reg-%d", descriptor.number);
    if (descriptor.feature &&
        g_utf8_validate(descriptor.feature, -1, NULL)) {
        result->feature = g_strdup(descriptor.feature);
    }
    result->bytes = register_value->len;
    result->value = wd40_register_value_to_hex(register_value);

    g_byte_array_unref(register_value);
    return result;

fail:
    if (register_value) {
        g_byte_array_unref(register_value);
    }
    qapi_free_WD40CPURegisterWrite(result);
    return NULL;
}

''',
        owned_markers=(
            "static int wd40_register_hex_digit",
            "wd40_register_descriptor_for_number",
            "qmp_x_wd40_write_cpu_register",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Structured log-category control\n-------------------------------\n""",
        """Typed CPU register writes
-------------------------

``x-wd40-write-cpu-register`` writes one register by its architecture-defined
GDB number.  The value is an exact hexadecimal byte string in the same byte
order returned by ``x-wd40-query-cpu-registers``; it is not a formatted target
integer.  Clients should discover the live register number, width, name, and
feature from a snapshot instead of carrying architecture-specific tables.

Before writing, the command synchronizes accelerator state and reads the
register to establish its exact width.  It rejects unknown or unavailable
registers, malformed hexadecimal, and values of the wrong size before calling
the target's GDB write callback.  A zero-length callback result is reported as
a non-writable register.

The result contains a fresh read-back rather than merely echoing the request,
so target masking, normalization, and architecture-specific side effects are
visible to the client.  A callback error is not a transactional rollback
guarantee.  The command does not pause a running guest; frontends should issue
``stop`` before changing execution-critical state or coordinating writes
across registers and memory.

""",
        owned_markers=(
            "Typed CPU register writes",
            "x-wd40-write-cpu-register",
            "fresh read-back",
        ),
    )


if __name__ == "__main__":
    main()
