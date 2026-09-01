#!/usr/bin/env python3
"""Expose one-instruction execution and typed debug-stop provenance."""

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
            f"{path}: partially applied execution-control block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected one insertion site, found {count}")
    store(file_path, text.replace(anchor, block + anchor, 1))


def replace_once(
    path: str,
    old: str,
    new: str,
    *,
    applied_marker: str,
) -> None:
    file_path, text = load(path)
    if text.count(applied_marker) == 1:
        return
    if text.count(applied_marker) != 0:
        raise RuntimeError(
            f"{path}: expected at most one {applied_marker!r}"
        )
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one replacement site, found {count}"
        )
    store(file_path, text.replace(old, new, 1))


def main() -> None:
    insert_before_once(
        "qapi/run-state.json",
        """##
# @ShutdownCause:
""",
        """##
# @WD40DebugStopReason:
#
# Cause of a guest-debug stop observed by the WD40 execution service.
#
# @single-step: completion of an outstanding WD40 step request
#
# @breakpoint: execution breakpoint or another non-watchpoint trap
#
# @watchpoint: guest memory watchpoint
#
# Since: 11.2
##
{ 'enum': 'WD40DebugStopReason',
  'data': [ 'single-step', 'breakpoint', 'watchpoint' ] }

##
# @WD40WatchpointAccess:
#
# Guest access that triggered a watchpoint stop.
#
# @read: guest read
#
# @write: guest write
#
# @access: guest read or write
#
# Since: 11.2
##
{ 'enum': 'WD40WatchpointAccess',
  'data': [ 'read', 'write', 'access' ] }

##
# @WD40StepRequest:
#
# Accepted request to execute exactly one instruction on one virtual CPU.
#
# @sequence: nonzero request identifier used to correlate the stop event
#
# @cpu-index: selected virtual CPU
#
# Since: 11.2
##
{ 'struct': 'WD40StepRequest',
  'data': { 'sequence': 'uint64', 'cpu-index': 'int' } }

##
# @WD40DebugStop:
#
# Most recently published guest-debug stop.
#
# @generation: nonzero monotonically advancing stop identifier
#
# @step-sequence: matching WD40 step request, when one was outstanding
#
# @cpu-index: virtual CPU that reported the guest-debug trap
#
# @reason: classified stop cause
#
# @pc: architecture-defined program counter, when the CPU exposes one
#
# @watch-address: watched guest virtual address, for a watchpoint stop
#
# @watch-access: triggering access class, for a watchpoint stop
#
# Since: 11.2
##
{ 'struct': 'WD40DebugStop',
  'data': { 'generation': 'uint64', '*step-sequence': 'uint64',
            'cpu-index': 'int', 'reason': 'WD40DebugStopReason',
            '*pc': 'uint64', '*watch-address': 'uint64',
            '*watch-access': 'WD40WatchpointAccess' } }

##
# @WD40DebugStopState:
#
# Availability and value of the most recently published debug stop.
#
# @available: whether a stop has been published
#
# @stop: most recently published stop, when available is true
#
# Since: 11.2
##
{ 'struct': 'WD40DebugStopState',
  'data': { 'available': 'bool', '*stop': 'WD40DebugStop' } }

##
# @x-wd40-step-cpu:
#
# Resume exactly one selected virtual CPU for one guest instruction.
# The machine must be stopped in prelaunch, paused, or debug state.
# Interrupts and timers are suppressed when the accelerator supports
# those single-step controls.
#
# @cpu-index: virtual CPU to execute.  It defaults to CPU 0.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: accepted request identifier and selected CPU
#
# Since: 11.2
##
{ 'command': 'x-wd40-step-cpu',
  'data': { '*cpu-index': 'int' },
  'returns': 'WD40StepRequest',
  'features': [ 'unstable' ] }

##
# @x-wd40-query-last-debug-stop:
#
# Query the most recently published WD40 guest-debug stop.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: debug-stop availability and the latest stop record
#
# Since: 11.2
##
{ 'command': 'x-wd40-query-last-debug-stop',
  'returns': 'WD40DebugStopState',
  'features': [ 'unstable' ] }

##
# @WD40_DEBUG_STOP:
#
# Emitted after the VM enters debug state for a guest-debug trap.
#
# @generation: nonzero monotonically advancing stop identifier
#
# @step-sequence: matching WD40 step request, when one was outstanding
#
# @cpu-index: virtual CPU that reported the guest-debug trap
#
# @reason: classified stop cause
#
# @pc: architecture-defined program counter, when the CPU exposes one
#
# @watch-address: watched guest virtual address, for a watchpoint stop
#
# @watch-access: triggering access class, for a watchpoint stop
#
# Since: 11.2
##
{ 'event': 'WD40_DEBUG_STOP',
  'data': { 'generation': 'uint64', '*step-sequence': 'uint64',
            'cpu-index': 'int', 'reason': 'WD40DebugStopReason',
            '*pc': 'uint64', '*watch-address': 'uint64',
            '*watch-access': 'WD40WatchpointAccess' } }

""",
        owned_markers=(
            "'enum': 'WD40DebugStopReason'",
            "'enum': 'WD40WatchpointAccess'",
            "'struct': 'WD40StepRequest'",
            "'struct': 'WD40DebugStop'",
            "'struct': 'WD40DebugStopState'",
            "'command': 'x-wd40-step-cpu'",
            "'command': 'x-wd40-query-last-debug-stop'",
            "'event': 'WD40_DEBUG_STOP'",
        ),
    )

    ensure_include(
        "system/runstate.c",
        '#include "system/hw_accel.h"\n',
        '#include "system/cpu-timers.h"\n',
    )

    insert_before_once(
        "system/runstate.c",
        """StatusInfo *qmp_query_status(Error **errp)
""",
        r'''typedef struct WD40DebugStopRecord {
    bool valid;
    uint64_t generation;
    bool has_step_sequence;
    uint64_t step_sequence;
    int64_t cpu_index;
    WD40DebugStopReason reason;
    bool has_pc;
    uint64_t pc;
    bool has_watch_address;
    uint64_t watch_address;
    bool has_watch_access;
    WD40WatchpointAccess watch_access;
} WD40DebugStopRecord;

static uint64_t wd40_debug_next_step_sequence;
static uint64_t wd40_debug_next_stop_generation;
static CPUState *wd40_debug_step_cpu;
static uint64_t wd40_debug_step_sequence;
static WD40DebugStopRecord wd40_debug_pending_stop;
static WD40DebugStopRecord wd40_debug_last_stop;

static uint64_t wd40_debug_next_nonzero(uint64_t *counter)
{
    *counter += 1;
    if (*counter == 0) {
        *counter = 1;
    }
    return *counter;
}

static CPUState *
wd40_debug_cpu_by_index(bool has_cpu_index, int64_t cpu_index)
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

static WD40DebugStop *
wd40_debug_stop_to_qapi(const WD40DebugStopRecord *record)
{
    WD40DebugStop *stop = g_new0(WD40DebugStop, 1);

    stop->generation = record->generation;
    stop->has_step_sequence = record->has_step_sequence;
    stop->step_sequence = record->step_sequence;
    stop->cpu_index = record->cpu_index;
    stop->reason = record->reason;
    stop->has_pc = record->has_pc;
    stop->pc = record->pc;
    stop->has_watch_address = record->has_watch_address;
    stop->watch_address = record->watch_address;
    stop->has_watch_access = record->has_watch_access;
    stop->watch_access = record->watch_access;
    return stop;
}

static void wd40_debug_step_cancel(void)
{
    if (wd40_debug_step_cpu) {
        cpu_single_step(wd40_debug_step_cpu, 0);
    }
    wd40_debug_step_cpu = NULL;
    wd40_debug_step_sequence = 0;
}

void wd40_debug_stop_notify(CPUState *cpu)
{
    WD40DebugStopRecord record = { 0 };

    if (wd40_debug_pending_stop.valid) {
        return;
    }

    record.valid = true;
    record.generation =
        wd40_debug_next_nonzero(&wd40_debug_next_stop_generation);
    record.cpu_index = cpu->cpu_index;

    if (wd40_debug_step_cpu == cpu) {
        record.has_step_sequence = true;
        record.step_sequence = wd40_debug_step_sequence;
    }

    if (cpu->watchpoint_hit) {
        record.reason = WD40_DEBUG_STOP_REASON_WATCHPOINT;
        record.has_watch_address = true;
        record.watch_address = cpu->watchpoint_hit->vaddr;
        record.has_watch_access = true;
        switch (cpu->watchpoint_hit->flags & BP_MEM_ACCESS) {
        case BP_MEM_READ:
            record.watch_access = WD40_WATCHPOINT_ACCESS_READ;
            break;
        case BP_MEM_WRITE:
            record.watch_access = WD40_WATCHPOINT_ACCESS_WRITE;
            break;
        default:
            record.watch_access = WD40_WATCHPOINT_ACCESS_ACCESS;
            break;
        }
    } else if (record.has_step_sequence) {
        record.reason = WD40_DEBUG_STOP_REASON_SINGLE_STEP;
    } else {
        record.reason = WD40_DEBUG_STOP_REASON_BREAKPOINT;
    }

    if (cpu->cc->get_pc) {
        record.has_pc = true;
        record.pc = cpu->cc->get_pc(cpu);
    }

    wd40_debug_pending_stop = record;
    if (record.has_step_sequence) {
        wd40_debug_step_cancel();
    }
}

static void wd40_debug_stop_publish(RunState state)
{
    WD40DebugStopRecord *record = &wd40_debug_pending_stop;

    if (state != RUN_STATE_DEBUG) {
        record->valid = false;
        wd40_debug_step_cancel();
        return;
    }
    if (!record->valid) {
        return;
    }

    wd40_debug_last_stop = *record;
    record->valid = false;
    qapi_event_send_wd40_debug_stop(
        wd40_debug_last_stop.generation,
        wd40_debug_last_stop.has_step_sequence,
        wd40_debug_last_stop.step_sequence,
        wd40_debug_last_stop.cpu_index,
        wd40_debug_last_stop.reason,
        wd40_debug_last_stop.has_pc,
        wd40_debug_last_stop.pc,
        wd40_debug_last_stop.has_watch_address,
        wd40_debug_last_stop.watch_address,
        wd40_debug_last_stop.has_watch_access,
        wd40_debug_last_stop.watch_access);
}

WD40StepRequest *
qmp_x_wd40_step_cpu(bool has_cpu_index, int64_t cpu_index, Error **errp)
{
    RunState state = runstate_get();
    CPUState *cpu;
    WD40StepRequest *request;
    unsigned int flags;
    uint64_t sequence;

    if (wd40_debug_step_cpu) {
        error_setg(errp, "A WD40 single-step request is already pending");
        return NULL;
    }
    if (state != RUN_STATE_PRELAUNCH &&
        state != RUN_STATE_PAUSED &&
        state != RUN_STATE_DEBUG) {
        error_setg(errp,
                   "Single-step requires prelaunch, paused, or debug state; "
                   "current state is '%s'",
                   RunState_str(state));
        return NULL;
    }
    if (vm_get_suspended()) {
        error_setg(errp, "A suspended VM cannot be single-stepped");
        return NULL;
    }

    cpu = wd40_debug_cpu_by_index(has_cpu_index, cpu_index);
    if (!cpu) {
        if (has_cpu_index) {
            error_setg(errp, "CPU index %" PRId64 " does not exist",
                       cpu_index);
        } else {
            error_setg(errp, "No realized CPU is available");
        }
        return NULL;
    }

    flags = SSTEP_ENABLE | SSTEP_NOIRQ | SSTEP_NOTIMER;
    flags &= current_accel()->gdbstub.sstep_flags;
    if (!(flags & SSTEP_ENABLE)) {
        error_setg(errp,
                   "The current accelerator does not support single-step");
        return NULL;
    }
    if (vm_prepare_start(true) != 0) {
        error_setg(errp, "Could not enter running state for single-step");
        return NULL;
    }

    sequence = wd40_debug_next_nonzero(&wd40_debug_next_step_sequence);
    wd40_debug_step_cpu = cpu;
    wd40_debug_step_sequence = sequence;
    cpu_single_step(cpu, flags);
    cpu_resume(cpu);
    qemu_clock_enable(QEMU_CLOCK_VIRTUAL, true);

    request = g_new0(WD40StepRequest, 1);
    request->sequence = sequence;
    request->cpu_index = cpu->cpu_index;
    return request;
}

WD40DebugStopState *
qmp_x_wd40_query_last_debug_stop(Error **errp)
{
    WD40DebugStopState *state = g_new0(WD40DebugStopState, 1);

    state->available = wd40_debug_last_stop.valid;
    if (state->available) {
        state->has_stop = true;
        state->stop = wd40_debug_stop_to_qapi(&wd40_debug_last_stop);
    }
    return state;
}

''',
        owned_markers=(
            "typedef struct WD40DebugStopRecord",
            "void wd40_debug_stop_notify(CPUState *cpu)",
            "qmp_x_wd40_step_cpu",
            "qmp_x_wd40_query_last_debug_stop",
        ),
    )

    insert_before_once(
        "include/system/runstate.h",
        """void qemu_system_debug_request(void);
""",
        """/**
 * wd40_debug_stop_notify:
 * @cpu: virtual CPU reporting a guest-debug trap
 *
 * Capture immutable stop provenance before GDB or another frontend can
 * consume transient watchpoint state.  Publication occurs after the VM
 * reaches debug state.
 */
void wd40_debug_stop_notify(CPUState *cpu);

""",
        owned_markers=(
            " * wd40_debug_stop_notify:\n",
            "void wd40_debug_stop_notify(CPUState *cpu);",
        ),
    )

    replace_once(
        "system/cpus.c",
        """    } else {
        gdb_set_stop_cpu(cpu);
        qemu_system_debug_request();
""",
        """    } else {
        wd40_debug_stop_notify(cpu);
        gdb_set_stop_cpu(cpu);
        qemu_system_debug_request();
""",
        applied_marker="        wd40_debug_stop_notify(cpu);",
    )

    replace_once(
        "system/runstate.c",
        """        if (send_stop) {
            qapi_event_send_stop();
        }
""",
        """        if (send_stop) {
            qapi_event_send_stop();
        }
        wd40_debug_stop_publish(state);
""",
        applied_marker="        wd40_debug_stop_publish(state);",
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Structured log-category control
-------------------------------
""",
        """Single-step execution and debug-stop provenance
-----------------------------------------------

``x-wd40-step-cpu`` resumes exactly one selected virtual CPU for one guest
instruction.  It accepts only the stable debugger states ``prelaunch``,
``paused``, and ``debug``; other virtual CPUs remain stopped.  The command
returns a nonzero sequence immediately after accepting the request.  It does
not wait for execution to finish.

Completion arrives through ``WD40_DEBUG_STOP`` after the VM has entered debug
state.  The event identifies the stopping CPU, a monotonically advancing stop
generation, the matching step sequence when present, the architecture-defined
program counter when available, and watchpoint address and access provenance.
``x-wd40-query-last-debug-stop`` returns the same most-recent record for
frontends recovering after a lost or reordered event.

The common guest-debug path copies watchpoint details before the GDB frontend
can clear ``CPUState::watchpoint_hit``.  GDB retains its existing stop packet
and ownership semantics.  A non-watchpoint trap while a WD40 request is
outstanding is classified as ``single-step`` because accelerators do not expose
a portable distinction between completion of that step and a coincident
execution breakpoint.

WD40 uses the accelerator's supported subset of ``SSTEP_ENABLE``,
``SSTEP_NOIRQ``, and ``SSTEP_NOTIMER``.  A stop for another reason cancels an
outstanding step and clears its one-shot CPU state.  Clients should correlate
on the returned sequence rather than assuming every later debug stop completed
their request.

""",
        owned_markers=(
            "Single-step execution and debug-stop provenance",
            "x-wd40-step-cpu",
            "WD40_DEBUG_STOP",
        ),
    )


if __name__ == "__main__":
    main()
