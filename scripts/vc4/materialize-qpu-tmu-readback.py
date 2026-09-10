#!/usr/bin/env python3
"""Materialize the bounded VC4 QPU fragment/TMU readback contract.

The transformation is architectural rather than workload-address based:

* VARYING_READ returns VP immediately and commits C to r5 after the current
  instruction, matching the QPU read latency used by consecutive varyings.
* TMU0 T/S writes form one bounded request and consume the two architected
  texture-configuration uniforms.
* LOAD_TMU0 obtains a 16-lane result through a caller-supplied sampler.
* PM=1 byte unpack from r4 and MUL 8A..8D pack implement the measured VC4
  normalized-color path.

Every edit is anchored exactly and the resulting source is idempotently
verified.  Unsupported modes continue to fail closed.
"""

from __future__ import annotations

import argparse
from pathlib import Path


HEADER = Path("include/hw/display/vc4_qpu_exec.h")
SOURCE = Path("hw/display/vc4_qpu_exec.c")
TEST = Path("tests/unit/test-vc4-qpu.c")

MARKERS = (
    "typedef bool (*VC4QPUTMULoadFunc)",
    "VC4_QPU_EXEC_FAULT_TMU_CONFIG_READ",
    "VC4_QPU_EXEC_FAULT_TMU_REQUEST",
    "VC4_QPU_EXEC_FAULT_TMU_LOAD",
    "bool varying_c_pending;",
    "bool tmu0_request_pending;",
    "bool vc4_qpu_exec_set_tmu0(",
    "#define VC4_QPU_TMU0_LOAD_SIGNAL      10",
    "#define VC4_QPU_REG_TMU0_S             56",
    "static bool vc4_qpu_exec_load_tmu0(",
    "static bool vc4_qpu_exec_unpack_r4(",
    "static void test_mesa_readback_fragment_tmu(void)",
    'g_test_add_func("/vc4/qpu/mesa-readback-tmu"',
)


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def replace_block(text: str, start: str, end: str, block: str,
                  label: str) -> str:
    first = text.find(start)
    if first < 0:
        raise SystemExit(f"{label}: start marker not found")
    second = text.find(end, first + len(start))
    if second < 0:
        raise SystemExit(f"{label}: end marker not found")
    if text.find(start, first + 1) >= 0:
        raise SystemExit(f"{label}: start marker is not unique")
    return text[:first] + block + text[second:]


def verify(header: str, source: str, test: str) -> None:
    combined = header + "\n" + source + "\n" + test
    missing = [marker for marker in MARKERS if marker not in combined]
    if missing:
        raise SystemExit("materialized source is missing: " + ", ".join(missing))
    forbidden = (
        "0xc320b000",
        "0x100049e0203e303eULL ==",
        "shader fingerprint",
        "if (code_address ==",
    )
    found = [marker for marker in forbidden if marker in source]
    if found:
        raise SystemExit("workload-specific dispatch detected: " + ", ".join(found))


def materialize_header(text: str) -> str:
    if all(marker in text for marker in MARKERS[:7]):
        return text

    text = replace_once(
        text,
        "typedef struct VC4QPUVector {\n"
        "    uint32_t lane[VC4_QPU_LANES];\n"
        "} VC4QPUVector;\n",
        "typedef struct VC4QPUVector {\n"
        "    uint32_t lane[VC4_QPU_LANES];\n"
        "} VC4QPUVector;\n\n"
        "typedef bool (*VC4QPUTMULoadFunc)(\n"
        "    void *opaque, const VC4QPUVector *s, const VC4QPUVector *t,\n"
        "    uint32_t config_p0, uint32_t config_p1,\n"
        "    VC4QPUVector *result);\n",
        "TMU callback type",
    )
    text = replace_once(
        text,
        "    VC4_QPU_EXEC_FAULT_UNIFORM_READ,\n"
        "    VC4_QPU_EXEC_FAULT_VARYING_READ,\n"
        "    VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT,\n",
        "    VC4_QPU_EXEC_FAULT_UNIFORM_READ,\n"
        "    VC4_QPU_EXEC_FAULT_VARYING_READ,\n"
        "    VC4_QPU_EXEC_FAULT_TMU_CONFIG_READ,\n"
        "    VC4_QPU_EXEC_FAULT_TMU_REQUEST,\n"
        "    VC4_QPU_EXEC_FAULT_TMU_LOAD,\n"
        "    VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT,\n",
        "TMU fault enum",
    )
    text = replace_once(
        text,
        "    const VC4QPUVector *varying_c;\n"
        "    unsigned varying_count;\n"
        "    unsigned varying_index;\n\n"
        "    uint32_t pc;\n",
        "    const VC4QPUVector *varying_c;\n"
        "    unsigned varying_count;\n"
        "    unsigned varying_index;\n"
        "    VC4QPUVector varying_pending_c;\n"
        "    bool varying_c_pending;\n\n"
        "    /* One bounded two-dimensional TMU0 request. */\n"
        "    VC4QPUTMULoadFunc tmu0_load_func;\n"
        "    void *tmu0_opaque;\n"
        "    VC4QPUVector tmu0_s;\n"
        "    VC4QPUVector tmu0_t;\n"
        "    uint32_t tmu0_config_p0;\n"
        "    uint32_t tmu0_config_p1;\n"
        "    bool tmu0_s_valid;\n"
        "    bool tmu0_t_valid;\n"
        "    bool tmu0_request_pending;\n\n"
        "    uint32_t pc;\n",
        "QPU state",
    )
    text = replace_once(
        text,
        "bool vc4_qpu_exec_set_varyings(VC4QPUExecState *state,\n"
        "                                const VC4QPUVector *partial,\n"
        "                                const VC4QPUVector *c, unsigned count);\n\n"
        "bool vc4_qpu_execute",
        "bool vc4_qpu_exec_set_varyings(VC4QPUExecState *state,\n"
        "                                const VC4QPUVector *partial,\n"
        "                                const VC4QPUVector *c, unsigned count);\n\n"
        "bool vc4_qpu_exec_set_tmu0(VC4QPUExecState *state,\n"
        "                            VC4QPUTMULoadFunc load_func,\n"
        "                            void *opaque);\n\n"
        "bool vc4_qpu_execute",
        "TMU setter declaration",
    )
    return text


def materialize_source(text: str) -> str:
    if all(marker in text for marker in MARKERS[7:11]):
        return text

    text = replace_once(
        text,
        "#define VC4_QPU_LAST_THREAD_SWITCH_SIGNAL 6\n"
        "#define VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL 5\n"
        "#define VC4_QPU_SMALL_IMMEDIATE_SIGNAL  13\n",
        "#define VC4_QPU_LAST_THREAD_SWITCH_SIGNAL 6\n"
        "#define VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL 5\n"
        "#define VC4_QPU_TMU0_LOAD_SIGNAL      10\n"
        "#define VC4_QPU_SMALL_IMMEDIATE_SIGNAL  13\n",
        "TMU signal",
    )
    text = replace_once(
        text,
        "#define VC4_QPU_REG_SFU_RECIP             52\n",
        "#define VC4_QPU_REG_SFU_RECIP             52\n"
        "#define VC4_QPU_REG_TMU0_S                56\n"
        "#define VC4_QPU_REG_TMU0_T                57\n"
        "#define VC4_QPU_REG_TMU0_R                58\n"
        "#define VC4_QPU_REG_TMU0_B                59\n\n"
        "#define VC4_QPU_UNPACK_8A                  4\n"
        "#define VC4_QPU_UNPACK_8D                  7\n"
        "#define VC4_QPU_PACK_MUL_8A                4\n"
        "#define VC4_QPU_PACK_MUL_8D                7\n",
        "TMU registers and byte modes",
    )

    old_varying = '''static bool vc4_qpu_exec_read_varying(VC4QPUExecState *state,
                                          VC4QPUVector *value)
{
    unsigned index = state->varying_index;

    if (index >= state->varying_count ||
        state->varying_partial == NULL || state->varying_c == NULL) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_VARYING_READ, index);
    }

    *value = state->varying_partial[index];
    state->accumulator[5] = state->varying_c[index];
    state->varying_index = index + 1;
    return true;
}
'''
    new_varying = '''static bool vc4_qpu_exec_read_varying(VC4QPUExecState *state,
                                          VC4QPUVector *value)
{
    unsigned index = state->varying_index;

    if (index >= state->varying_count ||
        state->varying_partial == NULL || state->varying_c == NULL ||
        state->varying_c_pending) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_VARYING_READ, index);
    }

    /*
     * The VP term is available to this instruction.  The matching C term is
     * the r5 input of the following instruction, so commit it only after the
     * issuing ALU instruction has completed.
     */
    *value = state->varying_partial[index];
    state->varying_pending_c = state->varying_c[index];
    state->varying_c_pending = true;
    state->varying_index = index + 1;
    return true;
}
'''
    text = replace_once(text, old_varying, new_varying,
                        "delayed varying coefficient")

    helper_anchor = "static bool vc4_qpu_exec_read_port(VC4QPUReadFunc read_func, void *opaque,\n"
    helpers = r'''static bool vc4_qpu_exec_unpack_r4(VC4QPUExecState *state,
                                      unsigned unpack,
                                      VC4QPUVector *value)
{
    unsigned shift;

    if (unpack < VC4_QPU_UNPACK_8A || unpack > VC4_QPU_UNPACK_8D) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_UNPACK, unpack);
    }
    shift = (unpack - VC4_QPU_UNPACK_8A) * 8;
    memset(value, 0, sizeof(*value));
    for (unsigned lane = 0; lane < state->active_lanes; lane++) {
        float normalized =
            ((state->accumulator[4].lane[lane] >> shift) & 0xff) / 255.0f;

        value->lane[lane] = vc4_qpu_float_to_bits(normalized);
    }
    return true;
}

static uint8_t vc4_qpu_exec_pack_unorm8(uint32_t bits)
{
    float value = vc4_qpu_bits_to_float(bits);

    if (isnan(value) || value <= 0.0f) {
        return 0;
    }
    if (value >= 1.0f) {
        return 0xff;
    }
    return (uint8_t)lrintf(value * 255.0f);
}

static bool vc4_qpu_exec_pack_mul_byte(VC4QPUExecState *state,
                                       VC4QPUVector *destination,
                                       const VC4QPUVector *value,
                                       unsigned pack)
{
    unsigned shift;
    uint32_t mask;

    if (pack < VC4_QPU_PACK_MUL_8A || pack > VC4_QPU_PACK_MUL_8D) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, pack);
    }
    shift = (pack - VC4_QPU_PACK_MUL_8A) * 8;
    mask = 0xffu << shift;
    for (unsigned lane = 0; lane < state->active_lanes; lane++) {
        uint32_t packed = vc4_qpu_exec_pack_unorm8(value->lane[lane]);

        destination->lane[lane] =
            (destination->lane[lane] & ~mask) | (packed << shift);
    }
    return true;
}

static bool vc4_qpu_exec_prepare_tmu0(VC4QPUReadFunc read_func,
                                      void *opaque,
                                      VC4QPUExecState *state,
                                      const VC4QPUVector *s)
{
    uint32_t address = state->uniform_address;

    if (state->tmu0_request_pending) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_TMU_REQUEST, 1);
    }
    if (!vc4_qpu_exec_read_u32(
            read_func, opaque, address, &state->tmu0_config_p0) ||
        !vc4_qpu_exec_read_u32(
            read_func, opaque, address + 4, &state->tmu0_config_p1)) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_TMU_CONFIG_READ, address);
    }

    state->uniform_address += 8;
    state->tmu0_s = *s;
    state->tmu0_s_valid = true;
    state->tmu0_request_pending = true;
    return true;
}

static bool vc4_qpu_exec_load_tmu0(VC4QPUExecState *state)
{
    VC4QPUVector result = { 0 };

    if (!state->tmu0_request_pending ||
        !state->tmu0_s_valid || !state->tmu0_t_valid) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_TMU_REQUEST,
            (state->tmu0_request_pending ? 1u : 0u) |
            (state->tmu0_s_valid ? 2u : 0u) |
            (state->tmu0_t_valid ? 4u : 0u));
    }
    if (state->tmu0_load_func == NULL ||
        !state->tmu0_load_func(
            state->tmu0_opaque, &state->tmu0_s, &state->tmu0_t,
            state->tmu0_config_p0, state->tmu0_config_p1, &result)) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_TMU_LOAD,
            state->tmu0_config_p0);
    }

    state->accumulator[4] = result;
    state->tmu0_s_valid = false;
    state->tmu0_t_valid = false;
    state->tmu0_request_pending = false;
    return true;
}

'''
    text = replace_once(text, helper_anchor, helpers + helper_anchor,
                        "TMU and byte helpers")

    write_start = "static bool vc4_qpu_exec_write(VC4QPUExecState *state, bool file_a,\n"
    write_end = "static bool vc4_qpu_exec_condition(VC4QPUExecState *state,\n"
    new_write = r'''static bool vc4_qpu_exec_write(VC4QPUReadFunc read_func, void *opaque,
                               VC4QPUExecState *state, bool file_a,
                               unsigned address, const VC4QPUVector *value,
                               unsigned pack)
{
    if (address < VC4_QPU_REGFILE_SIZE) {
        if (pack != 0) {
            return vc4_qpu_exec_set_fault(
                state, VC4_QPU_EXEC_FAULT_PACK, pack);
        }
        return vc4_qpu_exec_write_general(
            state, file_a, address, value, pack);
    }
    if (address >= 32 && address <= 35) {
        VC4QPUVector *destination = &state->accumulator[address - 32];

        if (pack == 0) {
            *destination = *value;
            return true;
        }
        return vc4_qpu_exec_pack_mul_byte(
            state, destination, value, pack);
    }
    if (address == VC4_QPU_REG_NULL) {
        return true;
    }
    if (pack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, pack);
    }

    switch (address) {
    case VC4_QPU_REG_NULL:
        return true;
    case VC4_QPU_REG_TLB_COLOR_ALL:
        state->tlb_color_all = *value;
        state->tlb_color_all_valid = true;
        return true;
    case VC4_QPU_REG_SFU_RECIP:
        memset(&state->accumulator[4], 0,
               sizeof(state->accumulator[4]));
        for (unsigned lane = 0; lane < state->active_lanes; lane++) {
            float input = vc4_qpu_bits_to_float(value->lane[lane]);

            state->accumulator[4].lane[lane] =
                vc4_qpu_float_to_bits(1.0f / input);
        }
        return true;
    case VC4_QPU_REG_TMU0_T:
        if (state->tmu0_request_pending) {
            return vc4_qpu_exec_set_fault(
                state, VC4_QPU_EXEC_FAULT_TMU_REQUEST, 2);
        }
        state->tmu0_t = *value;
        state->tmu0_t_valid = true;
        return true;
    case VC4_QPU_REG_TMU0_S:
        return vc4_qpu_exec_prepare_tmu0(
            read_func, opaque, state, value);
    case VC4_QPU_REG_TMU0_R:
    case VC4_QPU_REG_TMU0_B:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_WRITE_ADDRESS,
            (file_a ? 0x100 : 0x200) | address);
    case VC4_QPU_REG_VPM:
        return vc4_qpu_exec_write_vpm(state, value);
    case VC4_QPU_REG_VPM_SETUP:
        return file_a ?
            vc4_qpu_exec_setup_vpm_read(state, value->lane[0]) :
            vc4_qpu_exec_setup_vpm_write(state, value->lane[0]);
    default:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_WRITE_ADDRESS,
            (file_a ? 0x100 : 0x200) | address);
    }
}

'''
    text = replace_block(text, write_start, write_end, new_write,
                         "QPU write path")

    load_start = "static bool vc4_qpu_exec_load_immediate(VC4QPUExecState *state,\n"
    load_end = "static bool vc4_qpu_exec_alu(VC4QPUReadFunc read_func, void *opaque,\n"
    old_load = text[text.find(load_start):text.find(load_end)]
    new_load = old_load.replace(
        "static bool vc4_qpu_exec_load_immediate(VC4QPUExecState *state,\n",
        "static bool vc4_qpu_exec_load_immediate(VC4QPUReadFunc read_func,\n"
        "                                                void *opaque,\n"
        "                                                VC4QPUExecState *state,\n",
        1,
    ).replace(
        "!vc4_qpu_exec_write(state, !instruction->ws,",
        "!vc4_qpu_exec_write(read_func, opaque, state, !instruction->ws,",
    ).replace(
        "!vc4_qpu_exec_write(state, instruction->ws,",
        "!vc4_qpu_exec_write(read_func, opaque, state, instruction->ws,",
    )
    text = replace_block(text, load_start, load_end, new_load,
                         "load-immediate write plumbing")

    alu_start = "static bool vc4_qpu_exec_alu(VC4QPUReadFunc read_func, void *opaque,\n"
    alu_end = "void vc4_qpu_exec_init(VC4QPUExecState *state, uint32_t uniform_address)\n"
    new_alu = r'''static bool vc4_qpu_exec_alu(VC4QPUReadFunc read_func, void *opaque,
                             VC4QPUExecState *state,
                             const VC4QPUInstruction *instruction)
{
    VC4QPUVector port_a = { 0 };
    VC4QPUVector port_b = { 0 };
    VC4QPUVector unpacked_r4 = { 0 };
    VC4QPUVector add_result;
    VC4QPUVector mul_result;
    const VC4QPUVector *add_a;
    const VC4QPUVector *add_b;
    const VC4QPUVector *mul_a;
    const VC4QPUVector *mul_b;
    bool add_execute;
    bool mul_execute;
    bool need_a;
    bool need_b;
    bool uses_r4;
    unsigned add_pack;
    unsigned mul_pack;

    state->varying_c_pending = false;
    if (instruction->set_flags) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_FLAGS, 1);
    }
    if (instruction->unpack != 0 &&
        (!instruction->pm ||
         instruction->unpack < VC4_QPU_UNPACK_8A ||
         instruction->unpack > VC4_QPU_UNPACK_8D)) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_UNPACK, instruction->unpack);
    }
    if ((!instruction->pm && instruction->pack > 2) ||
        (instruction->pm && instruction->pack != 0 &&
         (instruction->pack < VC4_QPU_PACK_MUL_8A ||
          instruction->pack > VC4_QPU_PACK_MUL_8D))) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, instruction->pack);
    }
    if (!vc4_qpu_exec_condition(
            state, instruction->cond_add, &add_execute) ||
        !vc4_qpu_exec_condition(
            state, instruction->cond_mul, &mul_execute)) {
        return false;
    }

    add_execute &= instruction->op_add != VC4_QPU_ADD_NOP;
    mul_execute &= instruction->op_mul != VC4_QPU_MUL_NOP;
    if (instruction->pm && instruction->pack != 0 && !mul_execute) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, instruction->pack);
    }

    /* PM=0 packs the regfile-A destination.  PM=1 unpacks r4 and packs the
     * mul pipeline's output into one byte of its destination.
     */
    add_pack = instruction->pm ? 0 :
               (instruction->ws ? 0 : instruction->pack);
    mul_pack = instruction->pm ? instruction->pack :
               (instruction->ws ? instruction->pack : 0);

    if (!instruction->pm &&
        ((add_execute && add_pack &&
          instruction->waddr_add < VC4_QPU_REGFILE_SIZE &&
          instruction->op_add == VC4_QPU_ADD_FSUB) ||
         (mul_execute && mul_pack &&
          instruction->waddr_mul < VC4_QPU_REGFILE_SIZE &&
          instruction->op_mul == VC4_QPU_MUL_FMUL))) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, instruction->pack);
    }

    need_a = (add_execute &&
              (instruction->add_a == VC4_QPU_MUX_A ||
               instruction->add_b == VC4_QPU_MUX_A)) ||
             (mul_execute &&
              (instruction->mul_a == VC4_QPU_MUX_A ||
               instruction->mul_b == VC4_QPU_MUX_A));
    need_b = (add_execute &&
              (instruction->add_a == VC4_QPU_MUX_B ||
               instruction->add_b == VC4_QPU_MUX_B)) ||
             (mul_execute &&
              (instruction->mul_a == VC4_QPU_MUX_B ||
               instruction->mul_b == VC4_QPU_MUX_B));

    if (need_a &&
        !vc4_qpu_exec_read_port(read_func, opaque, state, true,
                                instruction->raddr_a, &port_a)) {
        return false;
    }
    if (need_b) {
        if (instruction->signal == VC4_QPU_SMALL_IMMEDIATE_SIGNAL) {
            if (!vc4_qpu_exec_small_immediate(
                    state, instruction->raddr_b, &port_b)) {
                return false;
            }
        } else if (need_a && instruction->raddr_a == instruction->raddr_b &&
                   (instruction->raddr_a == VC4_QPU_REG_UNIFORM ||
                    instruction->raddr_a == VC4_QPU_REG_VARYING ||
                    instruction->raddr_a == VC4_QPU_REG_NULL ||
                    instruction->raddr_a == VC4_QPU_REG_VPM)) {
            port_b = port_a;
        } else if (!vc4_qpu_exec_read_port(
                       read_func, opaque, state, false,
                       instruction->raddr_b, &port_b)) {
            return false;
        }
    }

    add_a = vc4_qpu_exec_mux(
        state, instruction->add_a, &port_a, &port_b);
    add_b = vc4_qpu_exec_mux(
        state, instruction->add_b, &port_a, &port_b);
    mul_a = vc4_qpu_exec_mux(
        state, instruction->mul_a, &port_a, &port_b);
    mul_b = vc4_qpu_exec_mux(
        state, instruction->mul_b, &port_a, &port_b);

    uses_r4 = add_a == &state->accumulator[4] ||
              add_b == &state->accumulator[4] ||
              mul_a == &state->accumulator[4] ||
              mul_b == &state->accumulator[4];
    if (instruction->unpack != 0) {
        if (!uses_r4 ||
            !vc4_qpu_exec_unpack_r4(
                state, instruction->unpack, &unpacked_r4)) {
            return false;
        }
        if (add_a == &state->accumulator[4]) {
            add_a = &unpacked_r4;
        }
        if (add_b == &state->accumulator[4]) {
            add_b = &unpacked_r4;
        }
        if (mul_a == &state->accumulator[4]) {
            mul_a = &unpacked_r4;
        }
        if (mul_b == &state->accumulator[4]) {
            mul_b = &unpacked_r4;
        }
    }

    if (add_execute &&
        !vc4_qpu_exec_add(state, instruction->op_add,
                          add_a, add_b, &add_result)) {
        return false;
    }
    if (mul_execute &&
        !vc4_qpu_exec_mul(state, instruction->op_mul,
                          mul_a, mul_b, &mul_result)) {
        return false;
    }

    if (add_execute &&
        !vc4_qpu_exec_write(read_func, opaque, state, !instruction->ws,
                            instruction->waddr_add, &add_result,
                            add_pack)) {
        return false;
    }
    if (mul_execute &&
        !vc4_qpu_exec_write(read_func, opaque, state, instruction->ws,
                            instruction->waddr_mul, &mul_result,
                            mul_pack)) {
        return false;
    }
    if (state->varying_c_pending) {
        state->accumulator[5] = state->varying_pending_c;
        state->varying_c_pending = false;
    }
    return true;
}

'''
    text = replace_block(text, alu_start, alu_end, new_alu,
                         "ALU PM/unpack/latency path")

    text = replace_once(
        text,
        "bool vc4_qpu_exec_set_varyings(VC4QPUExecState *state,\n"
        "                                const VC4QPUVector *partial,\n"
        "                                const VC4QPUVector *c, unsigned count)\n"
        "{\n"
        "    if (state == NULL || (count != 0 && (partial == NULL || c == NULL))) {\n"
        "        return false;\n"
        "    }\n\n"
        "    state->varying_partial = partial;\n"
        "    state->varying_c = c;\n"
        "    state->varying_count = count;\n"
        "    state->varying_index = 0;\n"
        "    return true;\n"
        "}\n\n"
        "bool vc4_qpu_execute",
        "bool vc4_qpu_exec_set_varyings(VC4QPUExecState *state,\n"
        "                                const VC4QPUVector *partial,\n"
        "                                const VC4QPUVector *c, unsigned count)\n"
        "{\n"
        "    if (state == NULL || (count != 0 && (partial == NULL || c == NULL))) {\n"
        "        return false;\n"
        "    }\n\n"
        "    state->varying_partial = partial;\n"
        "    state->varying_c = c;\n"
        "    state->varying_count = count;\n"
        "    state->varying_index = 0;\n"
        "    state->varying_c_pending = false;\n"
        "    return true;\n"
        "}\n\n"
        "bool vc4_qpu_exec_set_tmu0(VC4QPUExecState *state,\n"
        "                            VC4QPUTMULoadFunc load_func,\n"
        "                            void *opaque)\n"
        "{\n"
        "    if (state == NULL || load_func == NULL) {\n"
        "        return false;\n"
        "    }\n"
        "    state->tmu0_load_func = load_func;\n"
        "    state->tmu0_opaque = opaque;\n"
        "    return true;\n"
        "}\n\n"
        "bool vc4_qpu_execute",
        "TMU setter implementation",
    )

    execute_start = "bool vc4_qpu_execute(VC4QPUReadFunc read_func, void *opaque,\n"
    execute_end = "const char *vc4_qpu_exec_fault_name(VC4QPUExecFault fault)\n"
    old_execute = text[text.find(execute_start):text.find(execute_end)]
    new_execute = old_execute.replace(
        "    state->last_thread_switch = false;\n",
        "    state->last_thread_switch = false;\n"
        "    state->varying_c_pending = false;\n"
        "    state->tmu0_s_valid = false;\n"
        "    state->tmu0_t_valid = false;\n"
        "    state->tmu0_request_pending = false;\n",
        1,
    ).replace(
        "        case VC4_QPU_SMALL_IMMEDIATE_SIGNAL:\n"
        "            if (!vc4_qpu_exec_alu(\n"
        "                    read_func, opaque, state, &instruction)) {\n"
        "                return false;\n"
        "            }\n"
        "            break;\n"
        "        case VC4_QPU_LOAD_IMMEDIATE_SIGNAL:\n"
        "            if (!vc4_qpu_exec_load_immediate(state, &instruction)) {\n",
        "        case VC4_QPU_SMALL_IMMEDIATE_SIGNAL:\n"
        "            if (!vc4_qpu_exec_alu(\n"
        "                    read_func, opaque, state, &instruction)) {\n"
        "                return false;\n"
        "            }\n"
        "            break;\n"
        "        case VC4_QPU_TMU0_LOAD_SIGNAL:\n"
        "            if (!vc4_qpu_exec_load_tmu0(state) ||\n"
        "                !vc4_qpu_exec_alu(\n"
        "                    read_func, opaque, state, &instruction)) {\n"
        "                return false;\n"
        "            }\n"
        "            break;\n"
        "        case VC4_QPU_LOAD_IMMEDIATE_SIGNAL:\n"
        "            if (!vc4_qpu_exec_load_immediate(\n"
        "                    read_func, opaque, state, &instruction)) {\n",
        1,
    )
    if new_execute == old_execute:
        raise SystemExit("execute loop anchors changed")
    text = replace_block(text, execute_start, execute_end, new_execute,
                         "TMU signal dispatch")

    text = replace_once(
        text,
        "        [VC4_QPU_EXEC_FAULT_VARYING_READ] = \"varying-read\",\n"
        "        [VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT] = \"program-limit\",\n",
        "        [VC4_QPU_EXEC_FAULT_VARYING_READ] = \"varying-read\",\n"
        "        [VC4_QPU_EXEC_FAULT_TMU_CONFIG_READ] = \"tmu-config-read\",\n"
        "        [VC4_QPU_EXEC_FAULT_TMU_REQUEST] = \"tmu-request\",\n"
        "        [VC4_QPU_EXEC_FAULT_TMU_LOAD] = \"tmu-load\",\n"
        "        [VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT] = \"program-limit\",\n",
        "TMU fault names",
    )
    return text


def materialize_test(text: str) -> str:
    if all(marker in text for marker in MARKERS[11:]):
        return text

    mesa_fs = '''
static const uint64_t mesa_readback_fs[] = {
    0x100049e0203e303eULL, 0x100248e1213e317eULL,
    0x600208a7019e7340ULL, 0x10021e67159e7480ULL,
    0x10021e27159e76c0ULL, 0xa00009e7009e7000ULL,
    0x1d020867049e7900ULL, 0x1b424821849e7909ULL,
    0x195248e1849e7900ULL, 0x1f6248a1849e791bULL,
    0x117049e1809e7012ULL, 0x10020ba7159e7240ULL,
    0x300009e7009e7000ULL, 0x100009e7009e7000ULL,
    0x500009e7009e7000ULL,
};
'''
    text = replace_once(
        text,
        "static const uint32_t transform_uniforms[] = {\n",
        mesa_fs + "\nstatic const uint32_t transform_uniforms[] = {\n",
        "Mesa readback shader fixture",
    )

    test_block = r'''
typedef struct TestTMUContext {
    unsigned calls;
    uint32_t expected_p0;
    uint32_t expected_p1;
    uint32_t sampled_word;
    float expected_s;
    float expected_t;
} TestTMUContext;

static bool test_tmu0_load(void *opaque,
                           const VC4QPUVector *s,
                           const VC4QPUVector *t,
                           uint32_t config_p0,
                           uint32_t config_p1,
                           VC4QPUVector *result)
{
    TestTMUContext *context = opaque;

    context->calls++;
    g_assert_cmphex(config_p0, ==, context->expected_p0);
    g_assert_cmphex(config_p1, ==, context->expected_p1);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        float actual_s;
        float actual_t;

        memcpy(&actual_s, &s->lane[lane], sizeof(actual_s));
        memcpy(&actual_t, &t->lane[lane], sizeof(actual_t));
        g_assert_cmpfloat_with_epsilon(
            actual_s, context->expected_s, 0x1p-20f);
        g_assert_cmpfloat_with_epsilon(
            actual_t, context->expected_t, 0x1p-20f);
        result->lane[lane] = context->sampled_word;
    }
    return true;
}

static void test_mesa_readback_fragment_tmu(void)
{
    enum { CODE = 0xd000, UNIFORMS = 0xe000 };
    static const uint32_t texture_uniforms[] = {
        0x00100000, 0x040040a5,
    };
    uint8_t code[sizeof(mesa_readback_fs)];
    uint8_t uniforms[sizeof(texture_uniforms)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;
    VC4QPUVector partial[2] = { 0 };
    VC4QPUVector coefficient[2] = { 0 };
    TestTMUContext tmu = {
        .expected_p0 = texture_uniforms[0],
        .expected_p1 = texture_uniforms[1],
        /* Texture word ABGR -> fragment shader repacks to RGBA bytes. */
        .sampled_word = 0xff2080df,
        .expected_s = 0.35f,
        .expected_t = 0.95f,
    };

    encode_words(code, mesa_readback_fs, ARRAY_SIZE(mesa_readback_fs));
    encode_uniforms(uniforms, texture_uniforms,
                    ARRAY_SIZE(texture_uniforms));
    test_memory_add(&memory, CODE, code, sizeof(code));
    test_memory_add(&memory, UNIFORMS, uniforms, sizeof(uniforms));

    vc4_qpu_exec_init(&state, UNIFORMS);
    g_assert_true(vc4_qpu_exec_set_tmu0(&state, test_tmu0_load, &tmu));
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        state.reg_a[15].lane[lane] = 0x3f800000; /* fragment W = 1 */
        partial[0].lane[lane] = 0x3e800000;      /* 0.25 */
        coefficient[0].lane[lane] = 0x3dcccccd;  /* 0.10 */
        partial[1].lane[lane] = 0x3f400000;      /* 0.75 */
        coefficient[1].lane[lane] = 0x3e4ccccd;  /* 0.20 */
    }
    g_assert_true(vc4_qpu_exec_set_varyings(
        &state, partial, coefficient, ARRAY_SIZE(partial)));

    g_assert_true(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_NONE);
    g_assert_cmpuint(state.instruction_count, ==,
                     ARRAY_SIZE(mesa_readback_fs));
    g_assert_cmpuint(state.varying_index, ==, 2);
    g_assert_cmpuint(tmu.calls, ==, 1);
    g_assert_cmphex(state.uniform_address, ==,
                    UNIFORMS + sizeof(texture_uniforms));
    g_assert_true(state.last_thread_switch);
    g_assert_true(state.scoreboard_unlocked);
    g_assert_true(state.tlb_color_all_valid);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.tlb_color_all.lane[lane], ==, 0xffdf8020);
    }
}

'''
    text = replace_once(
        text,
        "static void test_unsupported_signal_fails_closed(void)\n",
        test_block + "static void test_unsupported_signal_fails_closed(void)\n",
        "Mesa TMU readback test",
    )
    text = replace_once(
        text,
        "    g_test_add_func(\"/vc4/qpu/varying-underflow\",\n"
        "                    test_varying_fifo_underflow_fails_closed);\n"
        "    g_test_add_func(\"/vc4/qpu/fail-closed\",\n",
        "    g_test_add_func(\"/vc4/qpu/varying-underflow\",\n"
        "                    test_varying_fifo_underflow_fails_closed);\n"
        "    g_test_add_func(\"/vc4/qpu/mesa-readback-tmu\",\n"
        "                    test_mesa_readback_fragment_tmu);\n"
        "    g_test_add_func(\"/vc4/qpu/fail-closed\",\n",
        "TMU test registration",
    )
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()

    header = HEADER.read_text(encoding="utf-8")
    source = SOURCE.read_text(encoding="utf-8")
    test = TEST.read_text(encoding="utf-8")

    new_header = materialize_header(header)
    new_source = materialize_source(source)
    new_test = materialize_test(test)
    verify(new_header, new_source, new_test)

    for path, old, new in (
        (HEADER, header, new_header),
        (SOURCE, source, new_source),
        (TEST, test, new_test),
    ):
        if old != new:
            path.write_text(new, encoding="utf-8")
            print(f"updated {path}")
        else:
            print(f"verified {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
