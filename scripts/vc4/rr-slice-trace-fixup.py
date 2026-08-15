#!/usr/bin/env python3
"""Add bounded diagnostics around single-threaded TCG RR CPU slices.

This is a workflow-only diagnostic. The generated scheduler source must never
be committed. It records the CPU selected for each execution slice, the result
returned by ``tcg_cpu_exec()``, and host-timer kick callbacks. Output is bounded
to keep the useful tail visible in CI logs.

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
        r"^static CPUState \*rr_current_cpu;[ \t]*$",
        text,
        re.MULTILINE,
    )
    if declaration is None:
        raise SystemExit("could not locate current RR CPU declaration")
    counters = """

/* VC4_RR_SLICE_TRACE: bounded workflow-only diagnostics. */
static unsigned rr_trace_slice_count;
static unsigned rr_trace_kick_count;
"""
    text = text[: declaration.end()] + counters + text[declaration.end() :]

    callback = re.search(
        r"^static void rr_kick_thread\(void \*opaque\)\n\{\n",
        text,
        re.MULTILINE,
    )
    if callback is None:
        raise SystemExit("could not locate RR host-timer callback")
    callback_trace = """    CPUState *trace_cpu = qatomic_read(&rr_current_cpu);

    (void)opaque;
    if (rr_trace_kick_count < 256) {
        fprintf(stderr,
                "VC4_RR_KICK seq=%u cpu=%d halted=%d stop=%d "
                "stopped=%d exit=%d kicked=%d\\n",
                rr_trace_kick_count,
                trace_cpu ? trace_cpu->cpu_index : -1,
                trace_cpu ? trace_cpu->halted : -1,
                trace_cpu ? trace_cpu->stop : -1,
                trace_cpu ? cpu_is_stopped(trace_cpu) : -1,
                trace_cpu ? qatomic_read(&trace_cpu->exit_request) : -1,
                trace_cpu ? qatomic_read(&trace_cpu->thread_kicked) : -1);
    }
    rr_trace_kick_count++;

"""
    text = text[: callback.end()] + callback_trace + text[callback.end() :]

    call = re.search(
        r"^(?P<indent>[ \t]*)(?P<result>[A-Za-z_][A-Za-z0-9_]*)"
        r"[ \t]*=[ \t]*tcg_cpu_exec\(cpu\);[ \t]*$",
        text,
        re.MULTILINE,
    )
    if call is None:
        raise SystemExit("could not locate tcg_cpu_exec(cpu) statement")
    indent = call.group("indent")
    result = call.group("result")
    replacement = f"""{indent}if (rr_trace_slice_count < 512) {{
{indent}    fprintf(stderr,
{indent}            "VC4_RR_SLICE enter seq=%u cpu=%d type=%s "
{indent}            "halted=%d stop=%d stopped=%d exit=%d kicked=%d\\n",
{indent}            rr_trace_slice_count, cpu->cpu_index,
{indent}            object_get_typename(OBJECT(cpu)), cpu->halted,
{indent}            cpu->stop, cpu_is_stopped(cpu),
{indent}            qatomic_read(&cpu->exit_request),
{indent}            qatomic_read(&cpu->thread_kicked));
{indent}}}
{indent}{result} = tcg_cpu_exec(cpu);
{indent}if (rr_trace_slice_count < 512) {{
{indent}    fprintf(stderr,
{indent}            "VC4_RR_SLICE leave seq=%u cpu=%d result=%d "
{indent}            "halted=%d stop=%d stopped=%d exit=%d kicked=%d\\n",
{indent}            rr_trace_slice_count, cpu->cpu_index, {result},
{indent}            cpu->halted, cpu->stop, cpu_is_stopped(cpu),
{indent}            qatomic_read(&cpu->exit_request),
{indent}            qatomic_read(&cpu->thread_kicked));
{indent}}}
{indent}rr_trace_slice_count++;"""
    text = text[: call.start()] + replacement + text[call.end() :]

    PATH.write_text(text, encoding="utf-8")
    print("Materialized bounded RR slice diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
