/*
 * VideoCore IV measured shader execution tests
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_qpu_exec.h"
#include "qemu/bswap.h"

#define TEST_MAX_SEGMENTS 4

typedef struct TestMemorySegment {
    uint32_t address;
    const uint8_t *data;
    size_t size;
} TestMemorySegment;

typedef struct TestMemory {
    TestMemorySegment segment[TEST_MAX_SEGMENTS];
    unsigned count;
} TestMemory;

static const uint64_t measured_vs[] = {
    0xd002102702821f80ULL, 0xe0024c6700201a00ULL,
    0x100049e020c20037ULL, 0x100049e1209c0007ULL,
    0x1012402227c20277ULL, 0x100049e3209c0017ULL,
    0x10220027079e76c0ULL, 0xe0025c6700001a00ULL,
    0x10020c2715027d80ULL, 0x10020c2715827d80ULL,
    0x10020c27159c0fc0ULL, 0x300009e7009e7000ULL,
    0x100009e7009e7000ULL, 0x100009e7009e7000ULL,
};

static const uint64_t measured_cs[] = {
    0xe0024c6700201a00ULL, 0xe0025c6700001a00ULL,
    0xd002102702821f80ULL, 0x1002086715c27d80ULL,
    0x10024c233582724eULL, 0x100248a135c00d9fULL,
    0x1012402027827256ULL, 0x10024c20359c0487ULL,
    0xd0020c27159c0fc0ULL, 0x10220027079e7000ULL,
    0xd0020c27159e0fc0ULL, 0x10020c2715027d80ULL,
    0x10020c2715827d80ULL, 0x10020c27159c0fc0ULL,
    0x300009e7009e7000ULL, 0x100009e7009e7000ULL,
    0x100009e7009e7000ULL,
};

static const uint64_t measured_fs[] = {
    0x100009e7009e7000ULL, 0x100009e7009e7000ULL,
    0x10020ba715827d80ULL, 0x300009e7009e7000ULL,
    0x100009e7009e7000ULL, 0x500009e7009e7000ULL,
};

static const uint32_t transform_uniforms[] = {
    0x3f800000, 0x44000000, 0xc4000000, 0x3f000000,
};

static bool test_memory_read(void *opaque, uint32_t address,
                             void *buffer, size_t size)
{
    TestMemory *memory = opaque;
    uint64_t end = (uint64_t)address + size;

    for (unsigned index = 0; index < memory->count; index++) {
        const TestMemorySegment *segment = &memory->segment[index];
        uint64_t segment_end = (uint64_t)segment->address + segment->size;

        if (address >= segment->address && end <= segment_end) {
            memcpy(buffer,
                   segment->data + (address - segment->address), size);
            return true;
        }
    }
    return false;
}

static void test_memory_add(TestMemory *memory, uint32_t address,
                            const void *data, size_t size)
{
    g_assert_cmpuint(memory->count, <, TEST_MAX_SEGMENTS);
    memory->segment[memory->count++] = (TestMemorySegment) {
        .address = address,
        .data = data,
        .size = size,
    };
}

static void encode_words(uint8_t *bytes, const uint64_t *words,
                         size_t count)
{
    for (size_t index = 0; index < count; index++) {
        stq_le_p(bytes + index * sizeof(uint64_t), words[index]);
    }
}

static void encode_uniforms(uint8_t *bytes, const uint32_t *words,
                            size_t count)
{
    for (size_t index = 0; index < count; index++) {
        stl_le_p(bytes + index * sizeof(uint32_t), words[index]);
    }
}

static void load_triangle_attributes(VC4QPUExecState *state)
{
    state->vpm[0].lane[0] = 0xbf800000;
    state->vpm[0].lane[1] = 0x40400000;
    state->vpm[0].lane[2] = 0xbf800000;
    state->vpm[1].lane[0] = 0xbf800000;
    state->vpm[1].lane[1] = 0xbf800000;
    state->vpm[1].lane[2] = 0x40400000;
}

static void assert_transform_output(const VC4QPUExecState *state,
                                    unsigned packed_row)
{
    g_assert_cmphex(state->vpm[packed_row].lane[0], ==, 0x0200fe00);
    g_assert_cmphex(state->vpm[packed_row].lane[1], ==, 0x02000600);
    g_assert_cmphex(state->vpm[packed_row].lane[2], ==, 0xfa00fe00);
}

static void prepare_transform_memory(TestMemory *memory,
                                     uint32_t code_address,
                                     uint32_t uniform_address,
                                     uint8_t *code,
                                     const uint64_t *words,
                                     size_t word_count,
                                     uint8_t *uniforms)
{
    encode_words(code, words, word_count);
    encode_uniforms(uniforms, transform_uniforms,
                    ARRAY_SIZE(transform_uniforms));
    test_memory_add(memory, code_address, code,
                    word_count * sizeof(uint64_t));
    test_memory_add(memory, uniform_address, uniforms,
                    sizeof(transform_uniforms));
}

static void test_measured_vertex_shader(void)
{
    enum { CODE = 0x1000, UNIFORMS = 0x2000 };
    uint8_t code[sizeof(measured_vs)];
    uint8_t uniforms[sizeof(transform_uniforms)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;

    prepare_transform_memory(&memory, CODE, UNIFORMS, code,
                             measured_vs, ARRAY_SIZE(measured_vs), uniforms);
    vc4_qpu_exec_init(&state, UNIFORMS);
    load_triangle_attributes(&state);

    g_assert_true(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_NONE);
    g_assert_cmpuint(state.instruction_count, ==, ARRAY_SIZE(measured_vs));
    g_assert_cmphex(state.uniform_address, ==,
                    UNIFORMS + sizeof(transform_uniforms));
    assert_transform_output(&state, 0);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.vpm[1].lane[lane], ==, 0x3f000000);
        g_assert_cmphex(state.vpm[2].lane[lane], ==, 0x3f800000);
    }
}

static void test_measured_coordinate_shader(void)
{
    enum { CODE = 0x3000, UNIFORMS = 0x4000 };
    uint8_t code[sizeof(measured_cs)];
    uint8_t uniforms[sizeof(transform_uniforms)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;

    prepare_transform_memory(&memory, CODE, UNIFORMS, code,
                             measured_cs, ARRAY_SIZE(measured_cs), uniforms);
    vc4_qpu_exec_init(&state, UNIFORMS);
    load_triangle_attributes(&state);

    g_assert_true(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_NONE);
    g_assert_cmpuint(state.instruction_count, ==, ARRAY_SIZE(measured_cs));
    g_assert_cmphex(state.uniform_address, ==,
                    UNIFORMS + sizeof(transform_uniforms));

    g_assert_cmphex(state.vpm[0].lane[0], ==, 0xbf800000);
    g_assert_cmphex(state.vpm[0].lane[1], ==, 0x40400000);
    g_assert_cmphex(state.vpm[0].lane[2], ==, 0xbf800000);
    g_assert_cmphex(state.vpm[1].lane[0], ==, 0xbf800000);
    g_assert_cmphex(state.vpm[1].lane[1], ==, 0xbf800000);
    g_assert_cmphex(state.vpm[1].lane[2], ==, 0x40400000);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.vpm[2].lane[lane], ==, 0x00000000);
        g_assert_cmphex(state.vpm[3].lane[lane], ==, 0x3f800000);
        g_assert_cmphex(state.vpm[5].lane[lane], ==, 0x3f000000);
        g_assert_cmphex(state.vpm[6].lane[lane], ==, 0x3f800000);
    }
    assert_transform_output(&state, 4);
}

static void test_measured_fragment_shader(void)
{
    enum { CODE = 0x5000, UNIFORMS = 0x6000 };
    static const uint32_t color_uniform[] = { 0xff2080df };
    uint8_t code[sizeof(measured_fs)];
    uint8_t uniforms[sizeof(color_uniform)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;

    encode_words(code, measured_fs, ARRAY_SIZE(measured_fs));
    encode_uniforms(uniforms, color_uniform, ARRAY_SIZE(color_uniform));
    test_memory_add(&memory, CODE, code, sizeof(code));
    test_memory_add(&memory, UNIFORMS, uniforms, sizeof(uniforms));

    vc4_qpu_exec_init(&state, UNIFORMS);
    g_assert_true(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_NONE);
    g_assert_cmpuint(state.instruction_count, ==, ARRAY_SIZE(measured_fs));
    g_assert_true(state.tlb_color_all_valid);
    g_assert_true(state.scoreboard_unlocked);
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        g_assert_cmphex(state.tlb_color_all.lane[lane], ==,
                        color_uniform[0]);
    }
}

static void test_unsupported_signal_fails_closed(void)
{
    enum { CODE = 0x7000 };
    static const uint64_t branch = 0xf000000000000000ULL;
    uint8_t code[sizeof(branch)];
    TestMemory memory = { 0 };
    VC4QPUExecState state;

    encode_words(code, &branch, 1);
    test_memory_add(&memory, CODE, code, sizeof(code));
    vc4_qpu_exec_init(&state, 0);

    g_assert_false(vc4_qpu_execute(test_memory_read, &memory, CODE, &state));
    g_assert_cmpint(state.fault, ==, VC4_QPU_EXEC_FAULT_SIGNAL);
    g_assert_cmpuint(state.fault_detail, ==, 15);
    g_assert_cmpstr(vc4_qpu_exec_fault_name(state.fault), ==, "signal");
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/vc4/qpu/measured-vs", test_measured_vertex_shader);
    g_test_add_func("/vc4/qpu/measured-cs", test_measured_coordinate_shader);
    g_test_add_func("/vc4/qpu/measured-fs", test_measured_fragment_shader);
    g_test_add_func("/vc4/qpu/fail-closed",
                    test_unsupported_signal_fails_closed);
    return g_test_run();
}
