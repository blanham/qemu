#!/usr/bin/env python3
"""Materialize the measured VC4 coordinate/vertex QPU execution subset.

The transformation is intentionally structural and idempotent.  Every source
replacement must match exactly once; an unexpected source tree fails closed
instead of applying a fuzzy patch.
"""

from __future__ import annotations

import argparse
from pathlib import Path


NEW_MARKERS = (
    "#define VC4_QPU_LAST_THREAD_SWITCH_SIGNAL 6",
    "#define VC4_QPU_ADD_FADD                  1",
    "#define VC4_QPU_ADD_FMAX                  4",
    "#define VC4_QPU_MUL_V8MIN                 4",
    "#define VC4_QPU_REG_SFU_RECIP             52",
    "bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,",
    "case VC4_QPU_LAST_THREAD_SWITCH_SIGNAL:",
    "case VC4_QPU_REG_SFU_RECIP:",
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{label}: expected exactly one source match, found {count}"
        )
    return text.replace(old, new, 1)


def verify_materialized(text: str) -> None:
    missing = [marker for marker in NEW_MARKERS if marker not in text]
    if missing:
        raise SystemExit(
            "materialized source is missing markers: " + ", ".join(missing)
        )
    if "lane < VC4_QPU_LANES" in text[
        text.index("static bool vc4_qpu_exec_add"):
        text.index("static bool vc4_qpu_exec_write_general")
    ]:
        raise SystemExit("ALU implementation still executes inactive lanes")


def materialize(text: str) -> str:
    if all(marker in text for marker in NEW_MARKERS):
        verify_materialized(text)
        return text

    text = replace_once(
        text,
        "#define VC4_QPU_PROGRAM_END_SIGNAL       3\n"
        "#define VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL 5\n",
        "#define VC4_QPU_PROGRAM_END_SIGNAL       3\n"
        "#define VC4_QPU_LAST_THREAD_SWITCH_SIGNAL 6\n"
        "#define VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL 5\n",
        "last-thread-switch signal",
    )
    text = replace_once(
        text,
        "#define VC4_QPU_ADD_NOP                   0\n"
        "#define VC4_QPU_ADD_FSUB                  2\n"
        "#define VC4_QPU_ADD_FTOI                  7\n"
        "#define VC4_QPU_ADD_OR                   21\n",
        "#define VC4_QPU_ADD_NOP                   0\n"
        "#define VC4_QPU_ADD_FADD                  1\n"
        "#define VC4_QPU_ADD_FSUB                  2\n"
        "#define VC4_QPU_ADD_FMAX                  4\n"
        "#define VC4_QPU_ADD_FTOI                  7\n"
        "#define VC4_QPU_ADD_OR                   21\n",
        "measured add operations",
    )
    text = replace_once(
        text,
        "#define VC4_QPU_MUL_NOP                   0\n"
        "#define VC4_QPU_MUL_FMUL                  1\n",
        "#define VC4_QPU_MUL_NOP                   0\n"
        "#define VC4_QPU_MUL_FMUL                  1\n"
        "#define VC4_QPU_MUL_V8MIN                 4\n",
        "measured mul operations",
    )
    text = replace_once(
        text,
        "#define VC4_QPU_REG_VPM                  48\n"
        "#define VC4_QPU_REG_VPM_SETUP            49\n",
        "#define VC4_QPU_REG_VPM                  48\n"
        "#define VC4_QPU_REG_VPM_SETUP            49\n"
        "#define VC4_QPU_REG_SFU_RECIP             52\n",
        "reciprocal SFU register",
    )

    old_add = '''static bool vc4_qpu_exec_add(VC4QPUExecState *state, unsigned operation,
                             const VC4QPUVector *a,
                             const VC4QPUVector *b,
                             VC4QPUVector *result)
{
    switch (operation) {
    case VC4_QPU_ADD_NOP:
        memset(result, 0, sizeof(*result));
        return true;
    case VC4_QPU_ADD_FSUB:
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]) -
                          vc4_qpu_bits_to_float(b->lane[lane]);

            result->lane[lane] = vc4_qpu_float_to_bits(value);
        }
        return true;
    case VC4_QPU_ADD_FTOI:
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]);

            /* The positive limit is exclusive; -2^31 is representable. */
            if (!isfinite(value) || value < -0x1p31f ||
                value >= 0x1p31f) {
                return vc4_qpu_exec_set_fault(
                    state, VC4_QPU_EXEC_FAULT_FLOAT_CONVERSION, lane);
            }
            result->lane[lane] = (uint32_t)(int32_t)value;
        }
        return true;
    case VC4_QPU_ADD_OR:
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            result->lane[lane] = a->lane[lane] | b->lane[lane];
        }
        return true;
    default:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_ADD_OP, operation);
    }
}
'''
    new_add = '''static bool vc4_qpu_exec_add(VC4QPUExecState *state, unsigned operation,
                             const VC4QPUVector *a,
                             const VC4QPUVector *b,
                             VC4QPUVector *result)
{
    memset(result, 0, sizeof(*result));

    switch (operation) {
    case VC4_QPU_ADD_NOP:
        return true;
    case VC4_QPU_ADD_FADD:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]) +
                          vc4_qpu_bits_to_float(b->lane[lane]);

            result->lane[lane] = vc4_qpu_float_to_bits(value);
        }
        return true;
    case VC4_QPU_ADD_FSUB:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]) -
                          vc4_qpu_bits_to_float(b->lane[lane]);

            result->lane[lane] = vc4_qpu_float_to_bits(value);
        }
        return true;
    case VC4_QPU_ADD_FMAX:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            float value = fmaxf(vc4_qpu_bits_to_float(a->lane[lane]),
                                vc4_qpu_bits_to_float(b->lane[lane]));

            result->lane[lane] = vc4_qpu_float_to_bits(value);
        }
        return true;
    case VC4_QPU_ADD_FTOI:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]);

            /* The positive limit is exclusive; -2^31 is representable. */
            if (!isfinite(value) || value < -0x1p31f ||
                value >= 0x1p31f) {
                return vc4_qpu_exec_set_fault(
                    state, VC4_QPU_EXEC_FAULT_FLOAT_CONVERSION, lane);
            }
            result->lane[lane] = (uint32_t)(int32_t)value;
        }
        return true;
    case VC4_QPU_ADD_OR:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            result->lane[lane] = a->lane[lane] | b->lane[lane];
        }
        return true;
    default:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_ADD_OP, operation);
    }
}
'''
    text = replace_once(text, old_add, new_add, "add execution function")

    old_mul = '''static bool vc4_qpu_exec_mul(VC4QPUExecState *state, unsigned operation,
                             const VC4QPUVector *a,
                             const VC4QPUVector *b,
                             VC4QPUVector *result)
{
    switch (operation) {
    case VC4_QPU_MUL_NOP:
        memset(result, 0, sizeof(*result));
        return true;
    case VC4_QPU_MUL_FMUL:
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]) *
                          vc4_qpu_bits_to_float(b->lane[lane]);

            result->lane[lane] = vc4_qpu_float_to_bits(value);
        }
        return true;
    default:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_MUL_OP, operation);
    }
}
'''
    new_mul = '''static bool vc4_qpu_exec_mul(VC4QPUExecState *state, unsigned operation,
                             const VC4QPUVector *a,
                             const VC4QPUVector *b,
                             VC4QPUVector *result)
{
    memset(result, 0, sizeof(*result));

    switch (operation) {
    case VC4_QPU_MUL_NOP:
        return true;
    case VC4_QPU_MUL_FMUL:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            float value = vc4_qpu_bits_to_float(a->lane[lane]) *
                          vc4_qpu_bits_to_float(b->lane[lane]);

            result->lane[lane] = vc4_qpu_float_to_bits(value);
        }
        return true;
    case VC4_QPU_MUL_V8MIN:
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            uint32_t value = 0;

            for (unsigned byte = 0; byte < sizeof(uint32_t); byte++) {
                unsigned shift = byte * 8;
                uint32_t a_byte = (a->lane[lane] >> shift) & 0xff;
                uint32_t b_byte = (b->lane[lane] >> shift) & 0xff;

                value |= MIN(a_byte, b_byte) << shift;
            }
            result->lane[lane] = value;
        }
        return true;
    default:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_MUL_OP, operation);
    }
}
'''
    text = replace_once(text, old_mul, new_mul, "mul execution function")

    text = replace_once(
        text,
        "    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {\n"
        "        uint32_t packed = value->lane[lane] & 0xffff;\n",
        "    for (unsigned lane = 0; lane < state->active_lanes; lane++) {\n"
        "        uint32_t packed = value->lane[lane] & 0xffff;\n",
        "active-lane packed write",
    )

    text = replace_once(
        text,
        "    case VC4_QPU_REG_TLB_COLOR_ALL:\n"
        "        state->tlb_color_all = *value;\n"
        "        state->tlb_color_all_valid = true;\n"
        "        return true;\n"
        "    case VC4_QPU_REG_VPM:\n",
        "    case VC4_QPU_REG_TLB_COLOR_ALL:\n"
        "        state->tlb_color_all = *value;\n"
        "        state->tlb_color_all_valid = true;\n"
        "        return true;\n"
        "    case VC4_QPU_REG_SFU_RECIP:\n"
        "        memset(&state->accumulator[4], 0,\n"
        "               sizeof(state->accumulator[4]));\n"
        "        for (unsigned lane = 0; lane < state->active_lanes; lane++) {\n"
        "            float input = vc4_qpu_bits_to_float(value->lane[lane]);\n"
        "\n"
        "            state->accumulator[4].lane[lane] =\n"
        "                vc4_qpu_float_to_bits(1.0f / input);\n"
        "        }\n"
        "        return true;\n"
        "    case VC4_QPU_REG_VPM:\n",
        "reciprocal SFU execution",
    )

    text = replace_once(
        text,
        "void vc4_qpu_exec_init(VC4QPUExecState *state, uint32_t uniform_address)\n"
        "{\n"
        "    memset(state, 0, sizeof(*state));\n"
        "    state->uniform_address = uniform_address;\n"
        "}\n",
        "void vc4_qpu_exec_init(VC4QPUExecState *state, uint32_t uniform_address)\n"
        "{\n"
        "    memset(state, 0, sizeof(*state));\n"
        "    state->uniform_address = uniform_address;\n"
        "    state->active_lanes = VC4_QPU_LANES;\n"
        "}\n"
        "\n"
        "bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,\n"
        "                                   unsigned active_lanes)\n"
        "{\n"
        "    if (state == NULL || active_lanes == 0 ||\n"
        "        active_lanes > VC4_QPU_LANES) {\n"
        "        return false;\n"
        "    }\n"
        "\n"
        "    state->active_lanes = active_lanes;\n"
        "    return true;\n"
        "}\n",
        "active-lane API",
    )

    text = replace_once(
        text,
        "    state->scoreboard_unlocked = false;\n"
        "    state->tlb_color_all_valid = false;\n",
        "    state->scoreboard_unlocked = false;\n"
        "    state->tlb_color_all_valid = false;\n"
        "    state->last_thread_switch = false;\n",
        "last-thread-switch reset",
    )
    text = replace_once(
        text,
        "        case VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL:\n"
        "        case VC4_QPU_SMALL_IMMEDIATE_SIGNAL:\n",
        "        case VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL:\n"
        "        case VC4_QPU_LAST_THREAD_SWITCH_SIGNAL:\n"
        "        case VC4_QPU_SMALL_IMMEDIATE_SIGNAL:\n",
        "last-thread-switch dispatch",
    )
    text = replace_once(
        text,
        "        if (instruction.signal == VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL) {\n"
        "            state->scoreboard_unlocked = true;\n"
        "        }\n"
        "        if (saw_program_end) {\n",
        "        if (instruction.signal == VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL) {\n"
        "            state->scoreboard_unlocked = true;\n"
        "        }\n"
        "        if (instruction.signal == VC4_QPU_LAST_THREAD_SWITCH_SIGNAL) {\n"
        "            state->last_thread_switch = true;\n"
        "        }\n"
        "        if (saw_program_end) {\n",
        "last-thread-switch result",
    )

    verify_materialized(text)
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        type=Path,
        default=Path("hw/display/vc4_qpu_exec.c"),
    )
    args = parser.parse_args()

    original = args.path.read_text(encoding="utf-8")
    result = materialize(original)
    if result != original:
        args.path.write_text(result, encoding="utf-8")
    print(
        f"measured transform source {'updated' if result != original else 'verified'}: "
        f"{args.path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
