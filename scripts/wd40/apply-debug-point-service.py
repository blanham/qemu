#!/usr/bin/env python3
"""Expose accelerator-backed breakpoints and watchpoints through QMP."""

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
            f"{path}: partially applied debug-point block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected one insertion site, found {count}")
    store(file_path, text.replace(anchor, block + anchor, 1))


def main() -> None:
    insert_before_once(
        "include/exec/gdbstub.h",
        """void gdb_set_stop_cpu(CPUState *cpu);\n""",
        """/**
 * gdb_breakpoint_insert:
 * @cpu: anchor CPU for the current accelerator's guest-debug hooks
 * @type: software breakpoint, hardware breakpoint, or watchpoint kind
 * @addr: guest virtual address
 * @len: architecture or watchpoint length
 *
 * Install one debugger point through the active accelerator.
 *
 * Returns: zero on success or a negative errno value.
 */
int gdb_breakpoint_insert(CPUState *cpu, GdbBreakpointType type,
                          vaddr addr, vaddr len);

/**
 * gdb_breakpoint_remove:
 *
 * Remove one debugger point previously installed with the same tuple.
 *
 * Returns: zero on success or a negative errno value.
 */
int gdb_breakpoint_remove(CPUState *cpu, GdbBreakpointType type,
                          vaddr addr, vaddr len);

void gdb_breakpoint_remove_all(CPUState *cpu);

""",
        owned_markers=(
            " * gdb_breakpoint_insert:\n",
            "int gdb_breakpoint_insert(CPUState *cpu",
            "int gdb_breakpoint_remove(CPUState *cpu",
        ),
    )

    insert_before_once(
        "include/exec/gdbstub.h",
        """typedef struct GDBFeature {\n""",
        """#include "exec/vaddr.h"
#include "gdbstub/enums.h"

""",
        owned_markers=(
            '#include "exec/vaddr.h"',
            '#include "gdbstub/enums.h"',
        ),
    )

    insert_before_once(
        "qapi/misc.json",
        """##\n# @LogCategoryInfo:\n""",
        """##
# @WD40DebugPointType:
#
# Accelerator-backed debugger point kind.
#
# @software-breakpoint: software execution breakpoint
#
# @hardware-breakpoint: hardware execution breakpoint
#
# @write-watchpoint: stop on a guest write
#
# @read-watchpoint: stop on a guest read
#
# @access-watchpoint: stop on a guest read or write
#
# Since: 11.2
##
{ 'enum': 'WD40DebugPointType',
  'data': [ 'software-breakpoint', 'hardware-breakpoint',
            'write-watchpoint', 'read-watchpoint',
            'access-watchpoint' ] }

##
# @WD40DebugPoint:
#
# One accelerator-backed debugger point tuple.
#
# @type: debugger point kind
#
# @address: guest virtual address
#
# @length: nonzero architecture or watchpoint length
#
# Since: 11.2
##
{ 'struct': 'WD40DebugPoint',
  'data': { 'type': 'WD40DebugPointType',
            'address': 'uint64', 'length': 'uint64' } }

##
# @x-wd40-insert-debug-point:
#
# Install a breakpoint or watchpoint through the active accelerator's
# GDB guest-debug hooks.  The guest must be stopped.
#
# @type: debugger point kind
#
# @address: guest virtual address
#
# @length: nonzero architecture or watchpoint length
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: the installed debugger point tuple
#
# Since: 11.2
##
{ 'command': 'x-wd40-insert-debug-point',
  'data': { 'type': 'WD40DebugPointType',
            'address': 'uint64', 'length': 'uint64' },
  'returns': 'WD40DebugPoint',
  'features': [ 'unstable' ] }

##
# @x-wd40-remove-debug-point:
#
# Remove one breakpoint or watchpoint through the active accelerator's
# GDB guest-debug hooks.  The guest must be stopped.  Removal is not
# idempotent: a missing point is an error.
#
# @type: debugger point kind
#
# @address: guest virtual address
#
# @length: exact length used when the point was installed
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: the removed debugger point tuple
#
# Since: 11.2
##
{ 'command': 'x-wd40-remove-debug-point',
  'data': { 'type': 'WD40DebugPointType',
            'address': 'uint64', 'length': 'uint64' },
  'returns': 'WD40DebugPoint',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'enum': 'WD40DebugPointType'",
            "'struct': 'WD40DebugPoint'",
            "'command': 'x-wd40-insert-debug-point'",
            "'command': 'x-wd40-remove-debug-point'",
        ),
    )

    insert_before_once(
        "monitor/qmp-cmds.c",
        """static LogCategoryInfoList *qmp_log_category_info_list(void)\n""",
        r'''static bool wd40_debug_point_type_to_gdb(WD40DebugPointType type,
                                         GdbBreakpointType *gdb_type,
                                         Error **errp)
{
    switch (type) {
    case WD40_DEBUG_POINT_TYPE_SOFTWARE_BREAKPOINT:
        *gdb_type = GDB_BREAKPOINT_SW;
        return true;
    case WD40_DEBUG_POINT_TYPE_HARDWARE_BREAKPOINT:
        *gdb_type = GDB_BREAKPOINT_HW;
        return true;
    case WD40_DEBUG_POINT_TYPE_WRITE_WATCHPOINT:
        *gdb_type = GDB_WATCHPOINT_WRITE;
        return true;
    case WD40_DEBUG_POINT_TYPE_READ_WATCHPOINT:
        *gdb_type = GDB_WATCHPOINT_READ;
        return true;
    case WD40_DEBUG_POINT_TYPE_ACCESS_WATCHPOINT:
        *gdb_type = GDB_WATCHPOINT_ACCESS;
        return true;
    default:
        error_setg(errp, "Unsupported debugger point type");
        return false;
    }
}

static bool wd40_debug_point_validate(WD40DebugPointType type,
                                      uint64_t address, uint64_t length,
                                      GdbBreakpointType *gdb_type,
                                      Error **errp)
{
    uint64_t max_address = VADDR_MAX;

    if (length == 0) {
        error_setg(errp, "Debugger point length must be nonzero");
        return false;
    }
    if (address > max_address || length > max_address ||
        address > max_address - (length - 1)) {
        error_setg(errp,
                   "Debugger point range 0x%" PRIx64 "/%" PRIu64
                   " exceeds the guest virtual-address container",
                   address, length);
        return false;
    }
    return wd40_debug_point_type_to_gdb(type, gdb_type, errp);
}

static WD40DebugPoint *
wd40_change_debug_point(bool insert, WD40DebugPointType type,
                        uint64_t address, uint64_t length, Error **errp)
{
    GdbBreakpointType gdb_type;
    WD40DebugPoint *result;
    int ret;

    if (!first_cpu) {
        error_setg(errp, "No realized CPU is available");
        return NULL;
    }
    if (runstate_is_running()) {
        error_setg(errp,
                   "The guest must be stopped before changing debugger "
                   "points");
        return NULL;
    }
    if (!wd40_debug_point_validate(type, address, length, &gdb_type, errp)) {
        return NULL;
    }

    if (insert) {
        ret = gdb_breakpoint_insert(first_cpu, gdb_type,
                                    (vaddr)address, (vaddr)length);
    } else {
        ret = gdb_breakpoint_remove(first_cpu, gdb_type,
                                    (vaddr)address, (vaddr)length);
    }
    if (ret == -ENOSYS) {
        error_setg(errp,
                   "The current accelerator does not support guest "
                   "debugger points");
        return NULL;
    }
    if (!insert && ret == -ENOENT) {
        error_setg(errp,
                   "The requested debugger point is not installed");
        return NULL;
    }
    if (ret < 0) {
        error_setg_errno(errp, -ret, "Could not %s debugger point",
                         insert ? "insert" : "remove");
        return NULL;
    }

    result = g_new0(WD40DebugPoint, 1);
    result->type = type;
    result->address = address;
    result->length = length;
    return result;
}

WD40DebugPoint *
qmp_x_wd40_insert_debug_point(WD40DebugPointType type,
                               uint64_t address, uint64_t length,
                               Error **errp)
{
    return wd40_change_debug_point(true, type, address, length, errp);
}

WD40DebugPoint *
qmp_x_wd40_remove_debug_point(WD40DebugPointType type,
                               uint64_t address, uint64_t length,
                               Error **errp)
{
    return wd40_change_debug_point(false, type, address, length, errp);
}

''',
        owned_markers=(
            "static bool wd40_debug_point_type_to_gdb",
            "static WD40DebugPoint *\nwd40_change_debug_point",
            "qmp_x_wd40_insert_debug_point",
            "qmp_x_wd40_remove_debug_point",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Structured log-category control
-------------------------------
""",
        """Typed breakpoints and watchpoints
--------------------------------

``x-wd40-insert-debug-point`` and ``x-wd40-remove-debug-point`` expose
software and hardware execution breakpoints plus read, write, and access
watchpoints through QEMU's existing accelerator guest-debug hooks.  This keeps
TCG, KVM, and other accelerator-specific semantics behind one typed interface
rather than modifying CPU breakpoint lists directly.

The guest must be stopped before either operation.  Addresses and lengths are
unsigned guest virtual-address values; lengths must be nonzero and the complete
range must fit QEMU's ``vaddr`` container.  Removal uses the same type, address,
and length tuple supplied at insertion and reports a missing point as an error.

These commands share the accelerator's GDB debug-point plane.  An attached GDB
may therefore remove a point created through QMP, and accelerator-specific
aliases remain visible: for example, TCG currently implements software and
hardware execution breakpoints with the same translated breakpoint mechanism.
Clients should coordinate ownership and pair successful insertions with exact
removals.  A failed accelerator operation is not a cross-CPU rollback
guarantee.

""",
        owned_markers=(
            "Typed breakpoints and watchpoints",
            "x-wd40-insert-debug-point",
            "share the accelerator's GDB debug-point plane",
        ),
    )


if __name__ == "__main__":
    main()
