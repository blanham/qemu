#!/usr/bin/env python3
"""Temporarily expose the TCG operand that dies during VC4 translation."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "tcg/tcg.c"

text = PATH.read_text(encoding="utf-8")
start = text.find("static void temp_load(")
end = text.find("/* Save a temporary to memory.", start)
if start < 0 or end < 0:
    raise SystemExit("could not delimit temp_load()")

chunk = text[start:end]
old = """    case TEMP_VAL_DEAD:
    default:
        g_assert_not_reached();
"""
new = """    case TEMP_VAL_DEAD:
        fprintf(stderr,
                "TCG dead input: idx=%zu kind=%u type=%u base=%u "
                "name=%s nb_globals=%d nb_temps=%d\\n",
                temp_idx(ts), (unsigned)ts->kind, (unsigned)ts->type,
                (unsigned)ts->base_type, ts->name ? ts->name : "<unnamed>",
                s->nb_globals, s->nb_temps);
        tcg_dump_ops(s, stderr, true);
        abort();
    default:
        g_assert_not_reached();
"""

if old in chunk:
    chunk = chunk.replace(old, new, 1)
elif new not in chunk:
    raise SystemExit("could not locate TEMP_VAL_DEAD arm in temp_load()")

PATH.write_text(text[:start] + chunk + text[end:], encoding="utf-8")
