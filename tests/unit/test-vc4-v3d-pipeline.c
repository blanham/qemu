/*
 * VideoCore IV measured primitive-pipeline tests
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_v3d_pipeline.h"
#include "qemu/bswap.h"

#define TEST_MAX_SEGMENTS 8

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

static bool test_memory_read(void *opaque, uint32_t address,
                             void *buffer, size_t size)
{
    TestMemory *memory = opaque;
    uint64_t end = (uint64_t)address + size;

    for (unsigned index = 0; index < memory->count; index++) {
        const TestMemorySegment *segment = &memory->segment[index];
        uint64_t segment_end = (uint64_t)segment->address + segment->size;

        if (address >= segment->address && end <= segment_end) {
            memcpy(buffer, segment->data + address - segment->address, size);
            return true;
        }
    }
    return false;
}

static void add_segment(TestMemory *memory, uint32_t address,
                        const void *data, size_t size)
{
    g_assert_cmpuint(memory->count, <, TEST_MAX_SEGMENTS);
    memory->segment[memory->count++] = (TestMemorySegment) {
        .address = address,
        .data = data,
        .size = size,
    };
}

static void encode_u64(uint8_t *bytes, const uint64_t *words, size_t count)
{
    for (size_t index = 0; index < count; index++) {
        stq_le_p(bytes + index * 8, words[index]);
    }
}

static void encode_u32(uint8_t *bytes, const uint32_t *words, size_t count)
{
    for (size_t index = 0; index < count; index++) {
        stl_le_p(bytes + index * 4, words[index]);
    }
}

typedef struct PipelineFixture {
    TestMemory memory;
    uint8_t record[52];
    uint8_t attributes[128];
    uint8_t fs_code[sizeof(measured_fs)];
    uint8_t vs_code[sizeof(measured_vs)];
    uint8_t cs_code[sizeof(measured_cs)];
    uint8_t fs_uniforms[4];
    uint8_t transform_uniforms[16];
    VC4V3DFrontierState state;
    VC4V3DPrimitiveInfo primitive;
} PipelineFixture;

static void fixture_init(PipelineFixture *fixture)
{
    enum {
        RECORD = 0x1000,
        ATTRIBUTES = 0x2000,
        FS_CODE = 0x3000,
        VS_CODE = 0x4000,
        CS_CODE = 0x5000,
        FS_UNIFORMS = 0x6000,
        VS_UNIFORMS = 0x7000,
        CS_UNIFORMS = 0x8000,
    };
    static const uint32_t transform[] = {
        0x3f800000, 0x44000000, 0xc4000000, 0x3f000000,
    };
    static const uint32_t color[] = { 0xff2080df };
    static const uint32_t vertex_attributes[] = {
        0xbf800000, 0xbf800000, 0x00000000, 0x3f800000,
        0x3c800000, 0x3f7c0000, 0x00000000, 0x00000000,
        0x40400000, 0xbf800000, 0x00000000, 0x3f800000,
        0x3d000000, 0x3f7c0000, 0x00000000, 0x00000000,
        0xbf800000, 0x40400000, 0x00000000, 0x3f800000,
        0x3d000000, 0x3f780000, 0x00000000, 0x00000000,
        0x40400000, 0x40400000, 0x00000000, 0x3f800000,
        0x3c800000, 0x3f780000, 0x00000000, 0x00000000,
    };

    memset(fixture, 0, sizeof(*fixture));
    fixture->record[0] = 0x05;
    fixture->record[3] = 0;
    stl_le_p(fixture->record + 4, FS_CODE);
    stl_le_p(fixture->record + 8, FS_UNIFORMS);
    fixture->record[14] = 3;
    fixture->record[15] = 24;
    stl_le_p(fixture->record + 16, VS_CODE);
    stl_le_p(fixture->record + 20, VS_UNIFORMS);
    fixture->record[26] = 1;
    fixture->record[27] = 16;
    stl_le_p(fixture->record + 28, CS_CODE);
    stl_le_p(fixture->record + 32, CS_UNIFORMS);

    stl_le_p(fixture->record + 36, ATTRIBUTES);
    fixture->record[40] = 15;
    fixture->record[41] = 32;
    fixture->record[42] = 0;
    fixture->record[43] = 0;

    stl_le_p(fixture->record + 44, ATTRIBUTES + 16);
    fixture->record[48] = 15;
    fixture->record[49] = 32;
    fixture->record[50] = 16;
    fixture->record[51] = 16;

    encode_u32(fixture->attributes, vertex_attributes,
               ARRAY_SIZE(vertex_attributes));
    encode_u64(fixture->fs_code, measured_fs, ARRAY_SIZE(measured_fs));
    encode_u64(fixture->vs_code, measured_vs, ARRAY_SIZE(measured_vs));
    encode_u64(fixture->cs_code, measured_cs, ARRAY_SIZE(measured_cs));
    encode_u32(fixture->fs_uniforms, color, ARRAY_SIZE(color));
    encode_u32(fixture->transform_uniforms, transform, ARRAY_SIZE(transform));

    add_segment(&fixture->memory, RECORD,
                fixture->record, sizeof(fixture->record));
    add_segment(&fixture->memory, ATTRIBUTES,
                fixture->attributes, sizeof(fixture->attributes));
    add_segment(&fixture->memory, FS_CODE,
                fixture->fs_code, sizeof(fixture->fs_code));
    add_segment(&fixture->memory, VS_CODE,
                fixture->vs_code, sizeof(fixture->vs_code));
    add_segment(&fixture->memory, CS_CODE,
                fixture->cs_code, sizeof(fixture->cs_code));
    add_segment(&fixture->memory, FS_UNIFORMS,
                fixture->fs_uniforms, sizeof(fixture->fs_uniforms));
    add_segment(&fixture->memory, VS_UNIFORMS,
                fixture->transform_uniforms,
                sizeof(fixture->transform_uniforms));
    add_segment(&fixture->memory, CS_UNIFORMS,
                fixture->transform_uniforms,
                sizeof(fixture->transform_uniforms));

    fixture->state = (VC4V3DFrontierState) {
        .bin_alloc_base = 0x9000,
        .bin_alloc_size = 0x1000,
        .bin_state_base = 0xa000,
        .shader_record = RECORD | 2,
        .bin_tiles_x = 1,
        .bin_tiles_y = 1,
        .bin_flags = 0x44,
        .have_binning_config = true,
        .have_shader_record = true,
    };
    fixture->primitive = (VC4V3DPrimitiveInfo) {
        .pc = 0xb000,
        .length = 3,
        .first = 0,
        .thread = 0,
        .packet = 33,
        .mode_byte = 4,
        .indexed = false,
    };
}

static void test_measured_triangle(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    g_assert_true(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0x0200, 0x0200, &result));
    g_assert_cmpint(result.fault, ==, VC4_V3D_PIPELINE_FAULT_NONE);
    g_assert_cmpuint(result.triangle_count, ==, 1);
    g_assert_cmpint(result.triangles[0].x[0], ==, 0);
    g_assert_cmpint(result.triangles[0].y[0], ==, 1024);
    g_assert_cmpint(result.triangles[0].x[1], ==, 2048);
    g_assert_cmpint(result.triangles[0].y[1], ==, 1024);
    g_assert_cmpint(result.triangles[0].x[2], ==, 0);
    g_assert_cmpint(result.triangles[0].y[2], ==, -1024);
    g_assert_cmphex(result.triangles[0].color, ==, 0xff2080df);
}

static void test_triangle_fan(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.primitive.mode_byte = 6;
    fixture.primitive.length = 4;
    g_assert_true(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0x0200, 0x0200, &result));
    g_assert_cmpint(result.fault, ==, VC4_V3D_PIPELINE_FAULT_NONE);
    g_assert_cmpuint(result.triangle_count, ==, 2);

    g_assert_cmpint(result.triangles[0].x[0], ==, 0);
    g_assert_cmpint(result.triangles[0].y[0], ==, 1024);
    g_assert_cmpint(result.triangles[0].x[1], ==, 2048);
    g_assert_cmpint(result.triangles[0].y[1], ==, 1024);
    g_assert_cmpint(result.triangles[0].x[2], ==, 0);
    g_assert_cmpint(result.triangles[0].y[2], ==, -1024);

    g_assert_cmpint(result.triangles[1].x[0], ==, 0);
    g_assert_cmpint(result.triangles[1].y[0], ==, 1024);
    g_assert_cmpint(result.triangles[1].x[1], ==, 0);
    g_assert_cmpint(result.triangles[1].y[1], ==, -1024);
    g_assert_cmpint(result.triangles[1].x[2], ==, 2048);
    g_assert_cmpint(result.triangles[1].y[2], ==, -1024);
    g_assert_cmphex(result.triangles[1].color, ==, 0xff2080df);
}

static void test_bad_mode_fails_closed(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.primitive.mode_byte = 5;
    g_assert_false(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0, 0, &result));
    g_assert_cmpint(result.fault, ==, VC4_V3D_PIPELINE_FAULT_MODE);
}

static void test_bad_length_fails_closed(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.primitive.length = 4;
    g_assert_false(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0, 0, &result));
    g_assert_cmpint(result.fault, ==, VC4_V3D_PIPELINE_FAULT_LENGTH);
}

static void test_short_fan_fails_closed(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.primitive.mode_byte = 6;
    fixture.primitive.length = 2;
    g_assert_false(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0, 0, &result));
    g_assert_cmpint(result.fault, ==, VC4_V3D_PIPELINE_FAULT_LENGTH);
}

static void test_bad_attribute_select_fails_closed(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.record[26] = 4;
    g_assert_false(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0, 0, &result));
    g_assert_cmpint(result.fault, ==,
                    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SELECT);
}

static void test_attribute_gap_fails_closed(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.record[50] = 20;
    g_assert_false(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0, 0, &result));
    g_assert_cmpint(result.fault, ==,
                    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SIZE);
    g_assert_cmphex(result.detail, ==, (16u << 16) | 24u);
}

static void test_attribute_overlap_fails_closed(void)
{
    PipelineFixture fixture;
    VC4V3DPipelineResult result;

    fixture_init(&fixture);
    fixture.record[50] = 12;
    g_assert_false(vc4_v3d_execute_array_primitive(
        test_memory_read, &fixture.memory,
        &fixture.state, &fixture.primitive, 0, 0, &result));
    g_assert_cmpint(result.fault, ==,
                    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_BOUNDS);
}

int main(int argc, char **argv)
{
    g_test_init(&argc, &argv, NULL);
    g_test_add_func("/vc4/v3d-pipeline/measured-triangle",
                    test_measured_triangle);
    g_test_add_func("/vc4/v3d-pipeline/triangle-fan",
                    test_triangle_fan);
    g_test_add_func("/vc4/v3d-pipeline/bad-mode",
                    test_bad_mode_fails_closed);
    g_test_add_func("/vc4/v3d-pipeline/bad-length",
                    test_bad_length_fails_closed);
    g_test_add_func("/vc4/v3d-pipeline/short-fan",
                    test_short_fan_fails_closed);
    g_test_add_func("/vc4/v3d-pipeline/bad-attribute-select",
                    test_bad_attribute_select_fails_closed);
    g_test_add_func("/vc4/v3d-pipeline/attribute-gap",
                    test_attribute_gap_fails_closed);
    g_test_add_func("/vc4/v3d-pipeline/attribute-overlap",
                    test_attribute_overlap_fails_closed);
    return g_test_run();
}
