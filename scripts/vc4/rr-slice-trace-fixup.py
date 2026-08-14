#!/usr/bin/env python3
"""Add bounded diagnostics around single-threaded TCG RR CPU slices.

This is a workflow-only diagnostic.  The generated scheduler source must never
be committed.  It records the CPU chosen for each execution slice, its
runnable-state fields, the result returned by ``tcg_cpus_exec()``, and realtime
kick callbacks.  Output is bounded to avoid hiding the useful tail in CI logs.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
MARKER = "VC4_RR_SLICE_TRACE"


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if MARKER in text:
        print("RR slice trace is already materialized.")
        return 0

    declaration = re.search(
        r"^(static QEMUTimer \*rr_kick_vcpu_timer;.*)$",
        text,
        re.MULTILINE,
    )
    if declaration is None:
        raise SystemExit("could not locate RR kick timer declaration")
    counters = """

/* VC4_RR_SLICE_TRACE: bounded workflow-only diagnostics. */
static unsigned rr_trace_slice_count;
static unsigned rr_trace_kick_count;
"""
    text = (
        text[: declaration.end()]
        + counters
        + text[declaration.end() :]
    )

    callback = re.search(
        r"static void rr_kick_vcpu_thread\(void \*opaque\)\n\{\n",
        text,
    )
    if callback is None:
        raise SystemExit("could not locate RR kick callback")
    active_expression = "NULL"
    if "static CPUState *rr_kick_cpu;" in text:
        active_expression = "qatomic_read(&rr_kick_cpu)"
    callback_trace = f"""    CPUState *trace_owner = opaque;
    CPUState *trace_active = {active_expression};

    if (rr_trace_kick_count < 256) {{
        fprintf(stderr,
                \"VC4_RR_KICK seq=%u owner=%d active=%d \"
                \"owner_halted=%d owner_stop=%d owner_stopped=%d \"
                \"owner_exit=%d owner_kicked=%d\\n\",
                rr_trace_kick_count,
                trace_owner ? trace_owner->cpu_index : -1,
                trace_active ? trace_active->cpu_index : -1,
                trace_owner ? trace_owner->halted : -1,
                trace_owner ? trace_owner->stop : -1,
                trace_owner ? trace_owner->stopped : -1,
                trace_owner ? qatomic_read(&trace_owner->exit_request) : -1,
                trace_owner ? qatomic_read(&trace_owner->thread_kicked) : -1);
    }}
    rr_trace_kick_count++;
"""
    text = text[: callback.end()] + callback_trace + text[callback.end() :]

    call = re.search(
        r"^(?P<indent>[ \t]*)(?P<prefix>[^\n;]*?)"
        r"tcg_cpus_exec\(cpu\)(?P<suffix>[^\n;]*;)[ \t]*$",
        text,
        re.MULTILINE,
    )
    if call is None:
        raise SystemExit("could not locate tcg_cpus_exec(cpu) statement")
    indent = call.group("indent")
    original = call.group(0).lstrip()
    enter = f"""{indent}if (rr_trace_slice_count < 512) {{
{indent}    fprintf(stderr,
{indent}            \"VC4_RR_SLICE enter seq=%u cpu=%d type=%s \"
{indent}            \"halted=%d stop=%d stopped=%d exit=%d kicked=%d\\n\",
{indent}            rr_trace_slice_count, cpu->cpu_index,
{indent}            object_get_typename(OBJECT(cpu)), cpu->halted,
{indent}            cpu->stop, cpu->stopped,
{indent}            qatomic_read(&cpu->exit_request),
{indent}            qatomic_read(&cpu->thread_kicked));
{indent}}}
{indent}{original}
{indent}if (rr_trace_slice_count < 512) {{
{indent}    fprintf(stderr,
{indent}            \"VC4_RR_SLICE leave seq=%u cpu=%d \"
{indent}            \"halted=%d stop=%d stopped=%d exit=%d kicked=%d\\n\",
{indent}            rr_trace_slice_count, cpu->cpu_index, cpu->halted,
{indent}            cpu->stop, cpu->stopped,
{indent}            qatomic_read(&cpu->exit_request),
{indent}            qatomic_read(&cpu->thread_kicked));
{indent}}}
{indent}rr_trace_slice_count++;"""
    text = text[: call.start()] + enter + text[call.end() :]

    PATH.write_text(text, encoding="utf-8")
    print("Materialized bounded RR slice diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
