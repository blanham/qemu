#!/usr/bin/env python3
"""Materialize one external RR kick policy for workflow experiments.

The single-threaded TCG accelerator receives the CPU requested by
``qemu_cpu_kick()`` but historically broadcasts the kick to every emulated
CPU.  That broadcast also sets every CPU's ``exit_request`` flag.  This helper
lets CI compare the existing policy with targeted alternatives without
committing generated scheduler source.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
PATH = ROOT / "accel/tcg/tcg-accel-ops-rr.c"
SIGNATURE = "void rr_kick_vcpu_thread(CPUState *unused)"
POLICY_MARKER = "VC4_RR_EXTERNAL_KICK_POLICY"
HELPER_MARKER = "VC4_RR_EXTERNAL_KICK_HELPER"
HELPER_NAME = "vc4_rr_external_active_cpu"
POLICIES = ("all", "active", "passed", "active-or-passed")


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


def find_function(text: str) -> tuple[int, int]:
    matches = list(re.finditer(rf"^{re.escape(SIGNATURE)}\n\{{", text, re.MULTILINE))
    if len(matches) != 1:
        raise SystemExit(
            f"expected one {SIGNATURE!r} definition, found {len(matches)}"
        )
    start = matches[0].start()
    opening = text.find("{", matches[0].start(), matches[0].end())
    closing = matching_brace(text, opening)
    return start, closing + 1


def remove_generated_helper(text: str) -> str:
    prototype = (
        f"/* {HELPER_MARKER}: declaration. */\n"
        f"static CPUState *{HELPER_NAME}(void);\n\n"
    )
    text = text.replace(prototype, "")

    definition_re = re.compile(
        rf"\n/\* {HELPER_MARKER}: definition\. \*/\n"
        rf"static CPUState \*{HELPER_NAME}\(void\)\n"
        r"\{\n"
        r"    return qatomic_read\(&rr_current_cpu\);\n"
        r"\}\n",
    )
    text, count = definition_re.subn("", text)
    if count > 1:
        raise SystemExit("found multiple generated active-CPU helpers")
    return text


def function_for(policy: str) -> str:
    if policy == "all":
        body = """    CPUState *cpu;

    (void)unused;
    CPU_FOREACH(cpu) {
        tcg_kick_vcpu_thread(cpu);
    }
"""
    elif policy == "active":
        body = f"""    CPUState *cpu = {HELPER_NAME}();

    (void)unused;
    if (cpu) {{
        tcg_kick_vcpu_thread(cpu);
    }}
"""
    elif policy == "passed":
        body = """    if (unused) {
        tcg_kick_vcpu_thread(unused);
    }
"""
    elif policy == "active-or-passed":
        body = f"""    CPUState *cpu = {HELPER_NAME}();

    if (!cpu) {{
        cpu = unused;
    }}
    if (cpu) {{
        tcg_kick_vcpu_thread(cpu);
    }}
"""
    else:  # pragma: no cover - argparse enforces the choices
        raise ValueError(policy)

    return (
        f"{SIGNATURE}\n"
        "{\n"
        f"    /* {POLICY_MARKER}: {policy}. */\n"
        f"{body}"
        "}"
    )


def add_active_helper(text: str) -> str:
    definition_anchor = "static CPUState *rr_current_cpu;\n"
    if text.count(definition_anchor) != 1:
        raise SystemExit("could not uniquely locate rr_current_cpu declaration")

    helper_definition = f"""

/* {HELPER_MARKER}: definition. */
static CPUState *{HELPER_NAME}(void)
{{
    return qatomic_read(&rr_current_cpu);
}}
"""
    text = text.replace(
        definition_anchor,
        definition_anchor + helper_definition,
        1,
    )

    start, _ = find_function(text)
    prototype = (
        f"/* {HELPER_MARKER}: declaration. */\n"
        f"static CPUState *{HELPER_NAME}(void);\n\n"
    )
    return text[:start] + prototype + text[start:]


def validate(text: str, policy: str) -> None:
    marker = f"{POLICY_MARKER}: {policy}."
    if text.count(marker) != 1:
        raise SystemExit(f"materialized source does not contain exactly one {marker}")

    helper_uses = policy in ("active", "active-or-passed")
    prototype = f"static CPUState *{HELPER_NAME}(void);"
    definition = f"static CPUState *{HELPER_NAME}(void)"
    if helper_uses:
        if text.count(prototype) != 1 or text.count(definition) != 2:
            # The definition spelling also contains the prototype spelling.
            raise SystemExit("active policy helper was not materialized exactly once")
    elif HELPER_MARKER in text or HELPER_NAME in text:
        raise SystemExit("inactive policy unexpectedly retained the active helper")

    start, end = find_function(text)
    function = text[start:end]
    if policy == "all" and "CPU_FOREACH(cpu)" not in function:
        raise SystemExit("all policy no longer broadcasts to the CPU list")
    if policy == "passed" and "tcg_kick_vcpu_thread(unused);" not in function:
        raise SystemExit("passed policy does not kick the requested CPU")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("policy", choices=POLICIES)
    args = parser.parse_args()

    text = PATH.read_text(encoding="utf-8")
    text = remove_generated_helper(text)

    start, end = find_function(text)
    text = text[:start] + function_for(args.policy) + text[end:]

    if args.policy in ("active", "active-or-passed"):
        text = add_active_helper(text)

    validate(text, args.policy)
    PATH.write_text(text, encoding="utf-8")
    print(f"Materialized RR external kick policy: {args.policy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
