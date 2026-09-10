#!/usr/bin/env python3
"""Materialize the bounded VC4 QPU varying-FIFO execution tranche."""

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


h = Path("include/hw/display/vc4_qpu_exec.h")
s = h.read_text()
s = replace_once(
    s,
    "    VC4_QPU_EXEC_FAULT_CODE_READ,\n"
    "    VC4_QPU_EXEC_FAULT_UNIFORM_READ,\n"
    "    VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT,\n",
    "    VC4_QPU_EXEC_FAULT_CODE_READ,\n"
    "    VC4_QPU_EXEC_FAULT_UNIFORM_READ,\n"
    "    VC4_QPU_EXEC_FAULT_VARYING_READ,\n"
    "    VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT,\n",
    "fault enum",
)
s = replace_once(
    s,
    "    uint32_t uniform_address;\n"
    "    uint32_t pc;\n"
    "    unsigned instruction_count;\n",
    "    uint32_t uniform_address;\n\n"
    "    /*\n"
    "     * Fragment-pipeline varying FIFO input.  A VARYING_READ returns the\n"
    "     * precomputed VP term and simultaneously loads its C coefficient into r5.\n"
    "     * The caller owns these vectors for the duration of vc4_qpu_execute().\n"
    "     */\n"
    "    const VC4QPUVector *varying_partial;\n"
    "    const VC4QPUVector *varying_c;\n"
    "    unsigned varying_count;\n"
    "    unsigned varying_index;\n\n"
    "    uint32_t pc;\n"
    "    unsigned instruction_count;\n",
    "state",
)
s = replace_once(
    s,
    "bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,\n"
    "                                   unsigned active_lanes);\n\n"
    "bool vc4_qpu_execute",
    "bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,\n"
    "                                   unsigned active_lanes);\n\n"
    "bool vc4_qpu_exec_set_varyings(VC4QPUExecState *state,\n"
    "                                const VC4QPUVector *partial,\n"
    "                                const VC4QPUVector *c, unsigned count);\n\n"
    "bool vc4_qpu_execute",
    "header API",
)
h.write_text(s)

c = Path("hw/display/vc4_qpu_exec.c")
s = c.read_text()
s = replace_once(
    s,
    "#define VC4_QPU_REG_UNIFORM              32\n"
    "#define VC4_QPU_REG_NULL                 39\n",
    "#define VC4_QPU_REG_UNIFORM              32\n"
    "#define VC4_QPU_REG_VARYING              35\n"
    "#define VC4_QPU_REG_NULL                 39\n",
    "register constant",
)
read_port = (
    "static bool vc4_qpu_exec_read_port(VC4QPUReadFunc read_func, void *opaque,\n"
    "                                   VC4QPUExecState *state, bool file_a,\n"
    "                                   unsigned address, VC4QPUVector *value)\n"
    "{\n"
)
helper = (
    "static bool vc4_qpu_exec_read_varying(VC4QPUExecState *state,\n"
    "                                          VC4QPUVector *value)\n"
    "{\n"
    "    unsigned index = state->varying_index;\n\n"
    "    if (index >= state->varying_count ||\n"
    "        state->varying_partial == NULL || state->varying_c == NULL) {\n"
    "        return vc4_qpu_exec_set_fault(\n"
    "            state, VC4_QPU_EXEC_FAULT_VARYING_READ, index);\n"
    "    }\n\n"
    "    *value = state->varying_partial[index];\n"
    "    state->accumulator[5] = state->varying_c[index];\n"
    "    state->varying_index = index + 1;\n"
    "    return true;\n"
    "}\n\n"
)
s = replace_once(s, read_port, helper + read_port, "read-port helper")
s = replace_once(
    s,
    "    case VC4_QPU_REG_UNIFORM:\n"
    "        return vc4_qpu_exec_read_uniform(\n"
    "            read_func, opaque, state, value);\n"
    "    case VC4_QPU_REG_NULL:\n",
    "    case VC4_QPU_REG_UNIFORM:\n"
    "        return vc4_qpu_exec_read_uniform(\n"
    "            read_func, opaque, state, value);\n"
    "    case VC4_QPU_REG_VARYING:\n"
    "        return vc4_qpu_exec_read_varying(state, value);\n"
    "    case VC4_QPU_REG_NULL:\n",
    "special read",
)
s = replace_once(
    s,
    "                   (instruction->raddr_a == VC4_QPU_REG_UNIFORM ||\n"
    "                    instruction->raddr_a == VC4_QPU_REG_NULL ||\n"
    "                    instruction->raddr_a == VC4_QPU_REG_VPM)) {\n",
    "                   (instruction->raddr_a == VC4_QPU_REG_UNIFORM ||\n"
    "                    instruction->raddr_a == VC4_QPU_REG_VARYING ||\n"
    "                    instruction->raddr_a == VC4_QPU_REG_NULL ||\n"
    "                    instruction->raddr_a == VC4_QPU_REG_VPM)) {\n",
    "shared special read",
)
active_api = (
    "bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,\n"
    "                                   unsigned active_lanes)\n"
    "{\n"
    "    if (state == NULL || active_lanes == 0 ||\n"
    "        active_lanes > VC4_QPU_LANES) {\n"
    "        return false;\n"
    "    }\n\n"
    "    state->active_lanes = active_lanes;\n"
    "    return true;\n"
    "}\n\n"
    "bool vc4_qpu_execute"
)
varying_api = (
    "bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,\n"
    "                                   unsigned active_lanes)\n"
    "{\n"
    "    if (state == NULL || active_lanes == 0 ||\n"
    "        active_lanes > VC4_QPU_LANES) {\n"
    "        return false;\n"
    "    }\n\n"
    "    state->active_lanes = active_lanes;\n"
    "    return true;\n"
    "}\n\n"
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
    "bool vc4_qpu_execute"
)
s = replace_once(s, active_api, varying_api, "varying API")
s = replace_once(
    s,
    "        [VC4_QPU_EXEC_FAULT_CODE_READ] = \"code-read\",\n"
    "        [VC4_QPU_EXEC_FAULT_UNIFORM_READ] = \"uniform-read\",\n"
    "        [VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT] = \"program-limit\",\n",
    "        [VC4_QPU_EXEC_FAULT_CODE_READ] = \"code-read\",\n"
    "        [VC4_QPU_EXEC_FAULT_UNIFORM_READ] = \"uniform-read\",\n"
    "        [VC4_QPU_EXEC_FAULT_VARYING_READ] = \"varying-read\",\n"
    "        [VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT] = \"program-limit\",\n",
    "fault name",
)
c.write_text(s)

t = Path("tests/unit/test-vc4-qpu.c")
s = t.read_text()
anchor = "static void test_unsupported_signal_fails_closed(void)\n{\n"
test = r'''static void test_measured_varying_read_contract(void)
{
    enum { CODE = 0x6800 };
    static const uint64_t program[] = {
        /* First instruction from the measured Mesa readback fragment shader. */
        0x100049e0203e303eULL,
        0x300009e7009e7000ULL,
        0x100009e7009e7000ULL,
        0x100009e7009e7000ULL,
    };
    uint8_t code[sizeof(program)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;
    VC4QPUVector partial = { 0 };
    VC4QPUVector coefficient = { 0 };

    encode_words(code, program, ARRAY_SIZE(program));
    test_memory_add(&memory, CODE, code, sizeof(code));
    vc4_qpu_exec_init(&state, 0);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        state.reg_a[15].lane[lane] = 0x3f800000; /* W = 1.0 */
        partial.lane[lane] = 0x40000000;         /* VP = 2.0 */
        coefficient.lane[lane] = 0x40400000;     /* C = 3.0 */
    }
    g_assert_true(vc4_qpu_exec_set_varyings(
        &state, &partial, &coefficient, 1));

    g_assert_true(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_NONE);
    g_assert_cmpuint(state.varying_index, ==, 1);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.accumulator[0].lane[lane], ==, 0x40000000);
        g_assert_cmphex(state.accumulator[5].lane[lane], ==, 0x40400000);
    }
}

static void test_varying_fifo_underflow_fails_closed(void)
{
    enum { CODE = 0x6900 };
    static const uint64_t program[] = {
        0x100049e0203e303eULL,
        0x300009e7009e7000ULL,
        0x100009e7009e7000ULL,
        0x100009e7009e7000ULL,
    };
    uint8_t code[sizeof(program)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;

    encode_words(code, program, ARRAY_SIZE(program));
    test_memory_add(&memory, CODE, code, sizeof(code));
    vc4_qpu_exec_init(&state, 0);

    g_assert_false(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_VARYING_READ);
    g_assert_cmpuint(state.fault_detail, ==, 0);
    g_assert_cmpstr(vc4_qpu_exec_fault_name(state.fault), ==, "varying-read");
}

'''
s = replace_once(s, anchor, test + anchor, "unit-test insertion")
s = replace_once(
    s,
    "    g_test_add_func(\"/vc4/qpu/measured-fs\", test_measured_fragment_shader);\n"
    "    g_test_add_func(\"/vc4/qpu/fail-closed\",\n"
    "                    test_unsupported_signal_fails_closed);\n",
    "    g_test_add_func(\"/vc4/qpu/measured-fs\", test_measured_fragment_shader);\n"
    "    g_test_add_func(\"/vc4/qpu/measured-varying-read\",\n"
    "                    test_measured_varying_read_contract);\n"
    "    g_test_add_func(\"/vc4/qpu/varying-underflow\",\n"
    "                    test_varying_fifo_underflow_fails_closed);\n"
    "    g_test_add_func(\"/vc4/qpu/fail-closed\",\n"
    "                    test_unsupported_signal_fails_closed);\n",
    "unit-test registration",
)
t.write_text(s)
