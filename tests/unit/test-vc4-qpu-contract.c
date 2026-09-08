/*
 * VideoCore IV QPU execution contracts, independent of the Mesa transcripts.
 * Encoding reference: Broadcom VideoCoreIV-AG100-R, pp. 26-36.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_qpu_exec.h"
#include "qemu/bswap.h"

#define NOP UINT64_C(0x100009e7009e7000)
#define END UINT64_C(0x300009e7009e7000)
#define SENTINEL 0xa5a5a5a5u

/* Deliberately allow a segment to extend past 2^32.  The executor, not this
 * callback, must reject a wrapping DMA transaction before invoking it.
 */
typedef struct TestMemory {
    uint32_t code_address;
    const uint64_t *code;
    size_t words;
    unsigned code_reads;
    uint32_t uniform_address;
    uint32_t uniform;
    unsigned uniform_reads;
} TestMemory;

static bool read_memory(void *opaque, uint32_t address,
                        void *buffer, size_t size)
{
    TestMemory *memory = opaque;
    uint64_t offset = (uint64_t)address - memory->code_address;

    if (address >= memory->code_address && size == 8 &&
        !(offset & 7) && offset / 8 < memory->words) {
        stq_le_p(buffer, memory->code[offset / 8]);
        memory->code_reads++;
        return true;
    }
    if (size == 4 && address == memory->uniform_address) {
        stl_le_p(buffer, memory->uniform);
        memory->uniform_reads++;
        return true;
    }
    return false;
}

/* Sources are r0/r1 on ADD and r2/r3 on MUL. */
static uint64_t alu(unsigned ws, unsigned pack, unsigned wa, unsigned wm,
                    unsigned add, unsigned mul)
{
    return (UINT64_C(1) << 60) | ((uint64_t)pack << 52) |
           ((uint64_t)(add != 0) << 49) |
           ((uint64_t)(mul != 0) << 46) | ((uint64_t)ws << 44) |
           ((uint64_t)wa << 38) | ((uint64_t)wm << 32) |
           ((uint64_t)mul << 29) | ((uint64_t)add << 24) |
           (UINT64_C(39) << 18) | (UINT64_C(39) << 12) |
           (UINT64_C(1) << 6) | (UINT64_C(2) << 3) | 3;
}

static void init_state(VC4QPUExecState *state)
{
    vc4_qpu_exec_init(state, 0x8000);
    memset(state->reg_a, 0xa5, sizeof(state->reg_a));
    memset(state->reg_b, 0xa5, sizeof(state->reg_b));
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        state->accumulator[0].lane[lane] = 0x12340000 | lane;
        state->accumulator[1].lane[lane] = 0x0000ff00;
        state->accumulator[2].lane[lane] = 0x3f800000;
        state->accumulator[3].lane[lane] = 0x40000000;
    }
}

static bool execute_one(uint64_t word, VC4QPUExecState *state)
{
    const uint64_t code[] = { word, END, NOP, NOP };
    TestMemory memory = { .code_address = 0x1000,
                          .code = code, .words = ARRAY_SIZE(code) };

    return vc4_qpu_execute(read_memory, &memory, memory.code_address, state);
}

static void test_alu_write_swap(void)
{
    for (unsigned ws = 0; ws < 2; ws++) {
        VC4QPUExecState state;
        init_state(&state);
        g_assert_true(execute_one(alu(ws, 0, 0, 1, 21, 1), &state));
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            g_assert_cmphex((ws ? state.reg_b[0] : state.reg_a[0]).lane[lane],
                            ==, 0x1234ff00 | lane);
            g_assert_cmphex((ws ? state.reg_a[1] : state.reg_b[1]).lane[lane],
                            ==, 0x40000000);
            g_assert_cmphex((ws ? state.reg_a[0] : state.reg_b[0]).lane[lane],
                            ==, SENTINEL);
            g_assert_cmphex((ws ? state.reg_b[1] : state.reg_a[1]).lane[lane],
                            ==, SENTINEL);
        }
    }
}

static void test_load_write_swap(void)
{
    for (unsigned ws = 0; ws < 2; ws++) {
        VC4QPUExecState state;
        uint64_t word = (UINT64_C(14) << 60) | (UINT64_C(1) << 49) |
                        (UINT64_C(1) << 46) | ((uint64_t)ws << 44) |
                        (UINT64_C(1) << 32) | UINT64_C(0x89abcdef);
        init_state(&state);
        g_assert_true(execute_one(word, &state));
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            g_assert_cmphex((ws ? state.reg_b[0] : state.reg_a[0]).lane[lane],
                            ==, 0x89abcdef);
            g_assert_cmphex((ws ? state.reg_a[1] : state.reg_b[1]).lane[lane],
                            ==, 0x89abcdef);
            g_assert_cmphex((ws ? state.reg_a[0] : state.reg_b[0]).lane[lane],
                            ==, SENTINEL);
            g_assert_cmphex((ws ? state.reg_b[1] : state.reg_a[1]).lane[lane],
                            ==, SENTINEL);
        }
    }
}

static void test_integer_pack(void)
{
    for (unsigned pack = 1; pack <= 2; pack++) {
        VC4QPUExecState state;
        init_state(&state);
        g_assert_true(execute_one(alu(0, pack, 0, 1, 21, 1), &state));
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            uint32_t expected = pack == 1 ? 0xa5a5ff00 | lane :
                                ((0xff00 | lane) << 16) | 0xa5a5;
            g_assert_cmphex(state.reg_a[0].lane[lane], ==, expected);
            g_assert_cmphex(state.reg_b[1].lane[lane], ==, 0x40000000);
        }
    }
}

static void test_pack_bypasses_accumulators(void)
{
    for (unsigned ws = 0; ws < 2; ws++) {
        VC4QPUExecState state;
        unsigned wa = ws ? 0 : 32;
        unsigned wm = ws ? 32 : 1;
        init_state(&state);
        g_assert_true(execute_one(alu(ws, 1, wa, wm, 21, 1), &state));
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            g_assert_cmphex(state.accumulator[0].lane[lane], ==,
                            ws ? 0x40000000 : 0x1234ff00 | lane);
            g_assert_cmphex(state.reg_b[ws ? 0 : 1].lane[lane], ==,
                            ws ? 0x1234ff00 | lane : 0x40000000);
        }
    }
}

/* Packed peripheral writes are outside the supported subset. */
static void test_packed_tlb_fails_closed(void)
{
    VC4QPUExecState state;
    init_state(&state);
    g_assert_false(execute_one(alu(0, 1, 46, 39, 21, 0), &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_PACK);
    g_assert_false(state.tlb_color_all_valid);
}

static void test_float_pack_fails_closed(void)
{
    for (unsigned ws = 0; ws < 2; ws++) {
        VC4QPUExecState state;
        init_state(&state);
        g_assert_false(execute_one(alu(ws, 1, 0, 1, ws ? 21 : 2, 1), &state));
        g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_PACK);
        g_assert_cmpuint(state.fault_index, ==, 0);
        /* Neither pipeline may commit before unsupported packing is rejected. */
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            g_assert_cmphex(state.reg_a[0].lane[lane], ==, SENTINEL);
            g_assert_cmphex(state.reg_b[0].lane[lane], ==, SENTINEL);
            g_assert_cmphex(state.reg_a[1].lane[lane], ==, SENTINEL);
            g_assert_cmphex(state.reg_b[1].lane[lane], ==, SENTINEL);
        }
    }
}

static void test_ftoi_boundaries(void)
{
    static const uint32_t input[] = {
        0x00000000, 0x80000000, 0x3fe00000, 0xbfe00000,
        0x4effffff, 0xcf000000, 0xceffffff, 0x3f000000,
    };
    static const uint32_t expected[] = {
        0, 0, 1, 0xffffffff, 0x7fffff80, 0x80000000, 0x80000080, 0,
    };
    VC4QPUExecState state;
    init_state(&state);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        state.accumulator[0].lane[lane] = input[lane % ARRAY_SIZE(input)];
    }
    g_assert_true(execute_one(alu(0, 0, 0, 39, 7, 0), &state));
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.reg_a[0].lane[lane], ==,
                        expected[lane % ARRAY_SIZE(expected)]);
    }
}

static void test_ftoi_rejects_out_of_range(void)
{
    static const uint32_t invalid[] = {
        0x4f000000, 0xcf000001, 0x7f800000, 0xff800000,
        0x7fc00000, 0x7f800001,
    };
    for (unsigned i = 0; i < ARRAY_SIZE(invalid); i++) {
        VC4QPUExecState state;
        init_state(&state);
        state.accumulator[0].lane[7] = invalid[i];
        g_assert_false(execute_one(alu(0, 0, 0, 39, 7, 0), &state));
        g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_FLOAT_CONVERSION);
        g_assert_cmpuint(state.fault_index, ==, 0);
        g_assert_cmpuint(state.fault_detail, ==, 7);
        for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
            g_assert_cmphex(state.reg_a[0].lane[lane], ==, SENTINEL);
        }
    }
}

static uint64_t read_both_uniform_ports(void)
{
    uint64_t word = alu(0, 0, 0, 39, 21, 0);
    word &= ~((UINT64_C(63) << 18) | (UINT64_C(63) << 12) |
              (UINT64_C(7) << 9) | (UINT64_C(7) << 6));
    return word | (UINT64_C(32) << 18) | (UINT64_C(32) << 12) |
           (UINT64_C(6) << 9) | (UINT64_C(7) << 6);
}

static void test_shared_uniform_read(void)
{
    uint64_t code[] = { read_both_uniform_ports(), END, NOP, NOP };
    TestMemory memory = { .code_address = 0x1000, .code = code,
                          .words = ARRAY_SIZE(code),
                          .uniform_address = 0x8000, .uniform = 0xfedcba98 };
    VC4QPUExecState state;
    init_state(&state);
    g_assert_true(vc4_qpu_execute(read_memory, &memory, 0x1000, &state));
    g_assert_cmpuint(memory.uniform_reads, ==, 1);
    g_assert_cmphex(state.uniform_address, ==, 0x8004);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.reg_a[0].lane[lane], ==, memory.uniform);
    }
}

static void test_code_read_span(void)
{
    const uint64_t code[] = { NOP, END, NOP, NOP };
    TestMemory memory = { .code_address = 0xfffffffc, .code = code,
                          .words = ARRAY_SIZE(code) };
    VC4QPUExecState state;
    init_state(&state);
    g_assert_false(vc4_qpu_execute(read_memory, &memory,
                                   memory.code_address, &state));
    g_assert_cmpuint(memory.code_reads, ==, 0);
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_CODE_READ);
    g_assert_cmpuint(state.fault_index, ==, 0);
    memory.code_address = 0xffffffe0;
    init_state(&state);
    g_assert_true(vc4_qpu_execute(read_memory, &memory,
                                  memory.code_address, &state));
    g_assert_cmpuint(memory.code_reads, ==, 4);
    g_assert_cmphex(state.pc, ==, 0xfffffff8);
}

static void test_uniform_read_span(void)
{
    uint64_t code[] = { read_both_uniform_ports(), END, NOP, NOP };
    TestMemory memory = { .code_address = 0x1000, .code = code,
                          .words = ARRAY_SIZE(code),
                          .uniform_address = 0xfffffffe, .uniform = 1 };
    VC4QPUExecState state;
    init_state(&state);
    state.uniform_address = memory.uniform_address;
    g_assert_false(vc4_qpu_execute(read_memory, &memory, 0x1000, &state));
    g_assert_cmpuint(memory.uniform_reads, ==, 0);
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_UNIFORM_READ);
    g_assert_cmphex(state.uniform_address, ==, memory.uniform_address);
}

static void test_end_delay(void)
{
    const uint64_t code[] = { NOP, END, NOP };
    TestMemory memory = { .code_address = 0x1000, .code = code,
                          .words = ARRAY_SIZE(code) };
    VC4QPUExecState state;
    init_state(&state);
    g_assert_false(vc4_qpu_execute(read_memory, &memory, 0x1000, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_CODE_READ);
    g_assert_cmpuint(state.fault_index, ==, 3);
    g_assert_cmphex(state.pc, ==, 0x1018);
}

static void test_program_limit(void)
{
    uint64_t code[64];
    TestMemory memory = { .code_address = 0x1000, .code = code,
                          .words = ARRAY_SIZE(code) };
    VC4QPUExecState state;
    for (unsigned i = 0; i < ARRAY_SIZE(code); i++) {
        code[i] = NOP;
    }
    init_state(&state);
    g_assert_false(vc4_qpu_execute(read_memory, &memory, 0x1000, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT);
    g_assert_cmpuint(state.instruction_count, ==, 64);
    code[61] = END;
    init_state(&state);
    g_assert_true(vc4_qpu_execute(read_memory, &memory, 0x1000, &state));
    g_assert_cmpuint(state.instruction_count, ==, 64);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/vc4/qpu/contract/alu-write-swap",
                    test_alu_write_swap);
    g_test_add_func("/vc4/qpu/contract/load-write-swap",
                    test_load_write_swap);
    g_test_add_func("/vc4/qpu/contract/integer-pack",
                    test_integer_pack);
    g_test_add_func("/vc4/qpu/contract/pack-accumulator",
                    test_pack_bypasses_accumulators);
    g_test_add_func("/vc4/qpu/contract/packed-tlb-rejected",
                    test_packed_tlb_fails_closed);
    g_test_add_func("/vc4/qpu/contract/float-pack-rejected",
                    test_float_pack_fails_closed);
    g_test_add_func("/vc4/qpu/contract/ftoi-boundaries",
                    test_ftoi_boundaries);
    g_test_add_func("/vc4/qpu/contract/ftoi-invalid",
                    test_ftoi_rejects_out_of_range);
    g_test_add_func("/vc4/qpu/contract/shared-uniform",
                    test_shared_uniform_read);
    g_test_add_func("/vc4/qpu/contract/code-read-span",
                    test_code_read_span);
    g_test_add_func("/vc4/qpu/contract/uniform-read-span",
                    test_uniform_read_span);
    g_test_add_func("/vc4/qpu/contract/end-delay",
                    test_end_delay);
    g_test_add_func("/vc4/qpu/contract/program-limit",
                    test_program_limit);
    return g_test_run();
}
