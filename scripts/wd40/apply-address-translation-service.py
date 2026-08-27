#!/usr/bin/env python3
"""Expose typed CPU virtual-to-physical translations through QMP."""

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
            f"{path}: partially applied address-translation block: "
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
# @WD40MemoryTransactionAttributes:
#
# Raw memory-transaction attributes returned by CPU debug translation.
#
# @unspecified: whether the translating CPU supplied no explicit
#     transaction attributes
#
# @secure: architecture-specific secure or system-management access
#
# @security-space: raw two-bit architecture security-space value
#
# @user: whether the access is unprivileged
#
# @memory: whether the access is restricted to normal memory
#
# @debug: whether the access has debugger privileges
#
# @requester-id: bus requester identifier
#
# @pid: process identifier or PCI PASID
#
# @address-type: architecture-specific address-type bit
#
# Since: 11.2
##
{ 'struct': 'WD40MemoryTransactionAttributes',
  'data': { 'unspecified': 'bool', 'secure': 'bool',
            'security-space': 'uint8', 'user': 'bool',
            'memory': 'bool', 'debug': 'bool',
            'requester-id': 'uint16', 'pid': 'uint8',
            'address-type': 'bool' } }

##
# @WD40AddressTranslation:
#
# CPU debugger translation of one guest virtual address.
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
# @virtual-address: address supplied to the CPU translation hook
#
# @translated: whether the CPU translated the virtual address
#
# @physical-address: translated physical address
#
# @address-space-index: CPU address-space index selected by the
#     returned transaction attributes
#
# @page-bits: log2 of the aligned translation block size
#
# @page-size: aligned translation block size in bytes
#
# @attributes: raw memory-transaction attributes for the translation
#
# Since: 11.2
##
{ 'struct': 'WD40AddressTranslation',
  'data': { 'cpu-index': 'int', 'target': 'str',
            'target-bits': 'uint64',
            'target-big-endian': 'bool', 'qom-type': 'str',
            'virtual-address': 'uint64', 'translated': 'bool',
            '*physical-address': 'uint64',
            '*address-space-index': 'int', '*page-bits': 'uint8',
            '*page-size': 'uint64',
            '*attributes': 'WD40MemoryTransactionAttributes' } }

##
# @x-wd40-translate-address:
#
# Translate one guest virtual address through the selected CPU's
# debugger translation hook.  An ordinary translation miss returns
# @WD40AddressTranslation.translated as false rather than a QMP error.
#
# @address: guest virtual address to translate
#
# @cpu-index: virtual CPU used for translation; defaults to CPU 0
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: typed CPU address-translation result
#
# Since: 11.2
##
{ 'command': 'x-wd40-translate-address',
  'data': { 'address': 'uint64', '*cpu-index': 'int' },
  'returns': 'WD40AddressTranslation',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'struct': 'WD40MemoryTransactionAttributes'",
            "'struct': 'WD40AddressTranslation'",
            "'command': 'x-wd40-translate-address'",
        ),
    )

    ensure_include(
        "system/physmem-qmp-cmds.c",
        '#include "qemu/target-info.h"\n',
        '#include "hw/core/cpu.h"\n',
    )

    insert_before_once(
        "system/physmem-qmp-cmds.c",
        """void qmp_memsave(uint64_t addr, uint64_t size, const char *filename,
""",
        r"""WD40AddressTranslation *
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

""",
        owned_markers=(
            "qmp_x_wd40_translate_address",
            "WD40MemoryTransactionAttributes",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Cross-architecture CPU register snapshots
-----------------------------------------
""",
        """Typed virtual-to-physical translation
-------------------------------------

``x-wd40-translate-address`` exposes QEMU's common CPU debugger translation
hook without parsing target-specific monitor text.  The result identifies the
selected CPU and target, reports an ordinary translation miss as structured
state, and returns the physical address, CPU address-space index, aligned block
size, and raw transaction attributes after a successful translation.

The aligned block size describes the range for which the CPU translation and
attributes remain valid.  It does not prove that the resulting physical
address is backed by RAM or a device.  Frontends can combine this command with
``x-wd40-read-memory`` when they need both MMU provenance and bytes.

The command synchronizes accelerator state but does not pause a running guest.
Clients should stop the machine before combining translations, memory reads,
and register snapshots that must describe one coherent point in time.

""",
        owned_markers=(
            "Typed virtual-to-physical translation",
            "x-wd40-translate-address",
        ),
    )


if __name__ == "__main__":
    main()
