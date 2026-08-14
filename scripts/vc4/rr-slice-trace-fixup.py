#!/usr/bin/env python3
"""Add bounded diagnostics around single-threaded TCG RR CPU slices.

This workflow-only materializer records external and timer kick provenance,
CPU publication, stale exit requests, execution slices, and rotation.  It is
kept separate from production source so the tracing itself can never become a
permanent scheduler dependency.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
EXTERNAL_SIGNATURE = "void rr_kick_vcpu_thread(CPUState *unused)"
TIMER_SIGNATURE = "static void rr_kick_thread(void *opaque)"
MARKERS = (
    "VC4_RR_SLICE_TRACE_HELPER",
    "VC4_RR_SLICE_TRACE_EXTERNAL",
    "VC4_RR_SLICE_TRACE_TIMER",
    "VC4_RR_SLICE_TRACE_PUBLISH",
    "VC4_RR_SLICE_TRACE_PREEXIT",
    "VC4_RR_SLICE_TRACE_EXEC",
    "VC4_RR_SLICE_TRACE_ROTATE",
    "VC4_RR_SLICE_TRACE_CLEAR",
)


def matching_brace(text: str, opening: int) -> int:
    """Return the matching closing brace while ignoring C comments/strings."""
    if opening >= len(text) or text[opening] != "{":
        raise ValueError("opening does not point at a brace")

    depth = 0
    state = "code"
    index = opening
    while index < len(text):
        char = text[index]
        pair = text[index : index + 2]

        if state == "code":
            if pair == "//":
                state = "line-comment"
                index += 2
                continue
            if pair == "/*":
                state = "block-comment"
                index += 2
                continue
            if char == '"':
                state = "string"
            elif char == "'":
                state = "character"
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    return index
        elif state == "line-comment":
            if char == "\n":
                state = "code"
        elif state == "block-comment":
            if pair == "*/":
                state = "code"
                index += 2
                continue
        elif state in ("string", "character"):
            if char == "\\":
                index += 2
                continue
            if (state == "string" and char == '"') or (
                state == "character" and char == "'"
            ):
                state = "code"

        index += 1

    raise ValueError("unterminated function body")


def find_function(text: str, signature: str) -> tuple[int, int, int]:
    matches = list(re.finditer(rf"^{re.escape(signature)}\n\{{", text, re.MULTILINE))
    if len(matches) != 1:
        raise SystemExit(
            f"expected one {signature!r} definition, found {len(matches)}"
        )
    opening = text.find("{", matches[0].start(), matches[0].end())
    closing = matching_brace(text, opening)
    return matches[0].start(), opening, closing


def replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"expected one {description}, found {count}")
    return text.replace(old, new, 1)


def instrument_function(
    text: str,
    signature: str,
    entry: str,
    leave: str,
) -> str:
    _, opening, closing = find_function(text, signature)
    body = text[opening + 1 : closing]
    return text[: opening + 1] + entry + body + leave + text[closing:]


def add_helper(text: str) -> str:
    external_start, _, _ = find_function(text, EXTERNAL_SIGNATURE)
    prototype = """/* VC4_RR_SLICE_TRACE_HELPER: workflow-only declaration. */
static void vc4_rr_trace_event(const char *event, CPUState *requested,
                               CPUState *subject, uint64_t pc, int result);

"""
    text = text[:external_start] + prototype + text[external_start:]

    declaration = "static CPUState *rr_current_cpu;\n"
    helper = r'''

/* VC4_RR_SLICE_TRACE_HELPER: bounded workflow-only diagnostics. */
static unsigned rr_trace_event_count;

static void vc4_rr_trace_event(const char *event, CPUState *requested,
                               CPUState *subject, uint64_t pc, int result)
{
    unsigned sequence = qatomic_fetch_inc(&rr_trace_event_count);
    CPUState *active;

    if (sequence >= 16384) {
        return;
    }

    active = qatomic_read(&rr_current_cpu);
    fprintf(stderr,
            "VC4_RR_TRACE seq=%u event=%s requested=%d subject=%d "
            "active=%d pc=0x%016" PRIx64 " halted=%d stop=%d "
            "stopped=%d exit=%d kicked=%d result=%d\n",
            sequence, event,
            requested ? requested->cpu_index : -1,
            subject ? subject->cpu_index : -1,
            active ? active->cpu_index : -1,
            pc,
            subject ? subject->halted : -1,
            subject ? subject->stop : -1,
            subject ? subject->stopped : -1,
            subject ? qatomic_read(&subject->exit_request) : -1,
            subject ? qatomic_read(&subject->thread_kicked) : -1,
            result);
}
'''
    return replace_once(
        text,
        declaration,
        declaration + helper,
        "rr_current_cpu declaration",
    )


def materialize(text: str) -> str:
    text = add_helper(text)

    external_entry = r'''
    CPUState *vc4_rr_trace_cpu;

    /* VC4_RR_SLICE_TRACE_EXTERNAL: before external kick policy. */
    vc4_rr_trace_event("external-enter", unused, NULL, UINT64_MAX, -1);
'''
    external_leave = r'''

    /* VC4_RR_SLICE_TRACE_EXTERNAL: snapshot every CPU after the kick. */
    CPU_FOREACH(vc4_rr_trace_cpu) {
        vc4_rr_trace_event("external-after", unused, vc4_rr_trace_cpu,
                           UINT64_MAX, -1);
    }
'''
    text = instrument_function(
        text,
        EXTERNAL_SIGNATURE,
        external_entry,
        external_leave,
    )

    timer_entry = r'''
    /* VC4_RR_SLICE_TRACE_TIMER: timer callback provenance. */
    vc4_rr_trace_event("timer-before", NULL,
                       qatomic_read(&rr_current_cpu), UINT64_MAX, -1);
'''
    timer_leave = r'''

    vc4_rr_trace_event("timer-after", NULL,
                       qatomic_read(&rr_current_cpu), UINT64_MAX, -1);
'''
    text = instrument_function(
        text,
        TIMER_SIGNATURE,
        timer_entry,
        timer_leave,
    )

    publish = "            qatomic_set_mb(&rr_current_cpu, cpu);\n"
    text = replace_once(
        text,
        publish,
        publish
        + "            /* VC4_RR_SLICE_TRACE_PUBLISH */\n"
        + "            vc4_rr_trace_event(\"publish\", NULL, cpu,\n"
        + "                               UINT64_MAX, -1);\n",
        "RR current-CPU publication",
    )

    preexit = """            if (qatomic_load_acquire(&cpu->exit_request)) {
                break;
            }
"""
    traced_preexit = """            if (qatomic_load_acquire(&cpu->exit_request)) {
                /* VC4_RR_SLICE_TRACE_PREEXIT */
                vc4_rr_trace_event("preexisting-exit", NULL, cpu,
                                   UINT64_MAX, -1);
                break;
            }
"""
    text = replace_once(text, preexit, traced_preexit, "preexisting-exit gate")

    execute = "                r = tcg_cpu_exec(cpu);\n"
    traced_execute = """                /* VC4_RR_SLICE_TRACE_EXEC */
                vc4_rr_trace_event("exec-before", NULL, cpu,
                                   UINT64_MAX, -1);
                r = tcg_cpu_exec(cpu);
                vc4_rr_trace_event("exec-after", NULL, cpu,
                                   UINT64_MAX, r);
"""
    text = replace_once(text, execute, traced_execute, "tcg_cpu_exec call")

    rotate = "            cpu = CPU_NEXT(cpu);\n"
    positions = [match.start() for match in re.finditer(re.escape(rotate), text)]
    if len(positions) < 2:
        raise SystemExit(
            "could not distinguish the terminal RR rotation from unplug handling"
        )
    position = positions[-1]
    traced_rotate = rotate + """            /* VC4_RR_SLICE_TRACE_ROTATE */
            vc4_rr_trace_event("rotate", NULL, cpu, UINT64_MAX, -1);
"""
    text = text[:position] + traced_rotate + text[position + len(rotate) :]

    clear = "        qatomic_set(&rr_current_cpu, NULL);\n"
    text = replace_once(
        text,
        clear,
        clear
        + "        /* VC4_RR_SLICE_TRACE_CLEAR */\n"
        + "        vc4_rr_trace_event(\"clear\", NULL, NULL, UINT64_MAX, -1);\n",
        "RR current-CPU clear",
    )

    return text


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    present = tuple(marker in text for marker in MARKERS)
    if all(present):
        print("RR slice trace is already materialized.")
        return 0
    if any(present):
        missing = [
            marker for marker, is_present in zip(MARKERS, present) if not is_present
        ]
        raise SystemExit(
            "refusing partially materialized RR trace; missing: "
            + ", ".join(missing)
        )

    text = materialize(text)
    missing = [marker for marker in MARKERS if marker not in text]
    if missing:
        raise SystemExit("trace materialization lost markers: " + ", ".join(missing))

    PATH.write_text(text, encoding="utf-8")
    print("Materialized bounded RR scheduler diagnostics.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
