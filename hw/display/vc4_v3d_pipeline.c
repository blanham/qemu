/*
 * VideoCore IV bounded primitive and shader pipeline
 *
 * This file connects validated shader records and the separately tested QPU
 * subset to typed triangles.  It intentionally supports only array triangles,
 * zero-varying fragment shaders, and bounded vertex batches.  Everything else
 * fails explicitly so later coverage cannot be mistaken for working hardware.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_v3d_pipeline.h"
#include "qemu/bswap.h"

#define VC4_PACKET_GL_ARRAY_PRIMITIVE 33
#define VC4_PRIMITIVE_TRIANGLES        4
#define VC4_COORDINATE_PACKED_XY_WORD  4
#define VC4_MAX_ATTRIBUTE_BYTES      256

static bool vc4_v3d_pipeline_set_fault(VC4V3DPipelineResult *result,
                                       VC4V3DPipelineFault fault,
                                       uint32_t detail)
{
    if (result->fault == VC4_V3D_PIPELINE_FAULT_NONE) {
        result->fault = fault;
        result->detail = detail;
    }
    return false;
}

static bool vc4_v3d_pipeline_set_qpu_fault(VC4V3DPipelineResult *result,
                                           VC4V3DPipelineFault fault,
                                           const VC4QPUExecState *qpu)
{
    result->qpu_fault = qpu->fault;
    return vc4_v3d_pipeline_set_fault(result, fault, qpu->fault_detail);
}

static bool vc4_v3d_load_attributes(VC4V3DFrontierReadFunc read_func,
                                    void *opaque,
                                    const VC4V3DShaderRecord *record,
                                    uint8_t select, uint8_t expected_size,
                                    bool coordinate,
                                    uint32_t first, uint32_t length,
                                    VC4QPUExecState *qpu,
                                    VC4V3DPipelineResult *result)
{
    uint32_t valid_mask = record->attribute_count == 8 ?
                          0xffu : ((1u << record->attribute_count) - 1u);
    unsigned selected_bytes = 0;

    if ((select & ~valid_mask) != 0) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SELECT, select);
    }

    for (unsigned index = 0; index < record->attribute_count; index++) {
        const VC4V3DShaderAttribute *attribute = &record->attributes[index];
        unsigned words;
        unsigned vpm_offset;

        if ((select & (1u << index)) == 0) {
            continue;
        }

        selected_bytes += attribute->bytes;
        words = DIV_ROUND_UP(attribute->bytes, sizeof(uint32_t));
        vpm_offset = coordinate ? attribute->cs_vpm_offset :
                                  attribute->vs_vpm_offset;
        if (attribute->bytes == 0 || attribute->bytes > VC4_MAX_ATTRIBUTE_BYTES ||
            words == 0 || vpm_offset + words > VC4_QPU_VPM_ROWS) {
            return vc4_v3d_pipeline_set_fault(
                result, VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_BOUNDS,
                (index << 24) | (vpm_offset << 16) | attribute->bytes);
        }

        for (unsigned vertex = 0; vertex < length; vertex++) {
            uint64_t element = (uint64_t)first + vertex;
            uint64_t address = attribute->address;
            uint8_t bytes[VC4_MAX_ATTRIBUTE_BYTES] = { 0 };

            if (attribute->stride != 0) {
                address += element * attribute->stride;
            }
            if (address > UINT32_MAX ||
                attribute->bytes - 1 > UINT32_MAX - address ||
                !read_func(opaque, (uint32_t)address,
                           bytes, attribute->bytes)) {
                return vc4_v3d_pipeline_set_fault(
                    result, VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_READ,
                    (index << 24) | vertex);
            }

            for (unsigned word = 0; word < words; word++) {
                uint8_t scalar[4] = { 0 };
                size_t offset = word * sizeof(uint32_t);
                size_t available = MIN((size_t)attribute->bytes - offset,
                                       sizeof(scalar));

                memcpy(scalar, bytes + offset, available);
                qpu->vpm[vpm_offset + word].lane[vertex] =
                    ldl_le_p(scalar);
            }
        }
    }

    if (selected_bytes != expected_size) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SIZE,
            (selected_bytes << 16) | expected_size);
    }
    return true;
}

static bool vc4_v3d_run_stage(VC4V3DFrontierReadFunc read_func,
                              void *opaque, uint32_t code,
                              uint32_t uniforms,
                              VC4QPUExecState *qpu,
                              VC4V3DPipelineResult *result,
                              VC4V3DPipelineFault fault)
{
    if (code == 0 || uniforms == 0) {
        return vc4_v3d_pipeline_set_fault(result, fault, code);
    }
    if (!vc4_qpu_execute(read_func, opaque, code, qpu)) {
        return vc4_v3d_pipeline_set_qpu_fault(result, fault, qpu);
    }
    return true;
}

bool vc4_v3d_execute_array_primitive(VC4V3DFrontierReadFunc read_func,
                                     void *opaque,
                                     const VC4V3DFrontierState *state,
                                     const VC4V3DPrimitiveInfo *primitive,
                                     int16_t viewport_x,
                                     int16_t viewport_y,
                                     VC4V3DPipelineResult *result)
{
    VC4V3DShaderRecord record;
    VC4QPUExecState coordinate_qpu;
    VC4QPUExecState vertex_qpu;
    VC4QPUExecState fragment_qpu;
    uint8_t packed_row;
    uint32_t color;

    if (result == NULL) {
        return false;
    }
    memset(result, 0, sizeof(*result));

    if (read_func == NULL || state == NULL || primitive == NULL ||
        primitive->thread != 0 || primitive->indexed ||
        primitive->packet != VC4_PACKET_GL_ARRAY_PRIMITIVE ||
        !state->have_binning_config || !state->have_shader_record) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_STATE, 0);
    }
    if ((primitive->mode_byte & 0xf) != VC4_PRIMITIVE_TRIANGLES) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_MODE,
            primitive->mode_byte & 0xf);
    }
    if (primitive->length < 3 ||
        primitive->length > VC4_V3D_PIPELINE_MAX_VERTICES ||
        (primitive->length % 3) != 0 ||
        primitive->first > UINT32_MAX - primitive->length) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_LENGTH, primitive->length);
    }
    if (!vc4_v3d_decode_shader_record(
            read_func, opaque, state->shader_record, &record)) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_SHADER_RECORD,
            state->shader_record);
    }
    if (record.fs_varyings != 0) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_VARYINGS,
            record.fs_varyings);
    }

    vc4_qpu_exec_init(&coordinate_qpu, record.cs_uniforms);
    if (!vc4_v3d_load_attributes(
            read_func, opaque, &record,
            record.cs_attribute_select, record.cs_attribute_size,
            true, primitive->first, primitive->length,
            &coordinate_qpu, result) ||
        !vc4_v3d_run_stage(
            read_func, opaque, record.cs_code, record.cs_uniforms,
            &coordinate_qpu, result,
            VC4_V3D_PIPELINE_FAULT_QPU_COORDINATE)) {
        return false;
    }

    if (!coordinate_qpu.vpm_wrote ||
        coordinate_qpu.vpm_write_count <= VC4_COORDINATE_PACKED_XY_WORD) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_COORDINATE_LAYOUT,
            coordinate_qpu.vpm_write_count);
    }
    packed_row = (coordinate_qpu.vpm_first_write_row +
                  VC4_COORDINATE_PACKED_XY_WORD *
                  coordinate_qpu.vpm_write_stride) &
                 (VC4_QPU_VPM_ROWS - 1);

    vc4_qpu_exec_init(&vertex_qpu, record.vs_uniforms);
    if (!vc4_v3d_load_attributes(
            read_func, opaque, &record,
            record.vs_attribute_select, record.vs_attribute_size,
            false, primitive->first, primitive->length,
            &vertex_qpu, result) ||
        !vc4_v3d_run_stage(
            read_func, opaque, record.vs_code, record.vs_uniforms,
            &vertex_qpu, result,
            VC4_V3D_PIPELINE_FAULT_QPU_VERTEX)) {
        return false;
    }

    vc4_qpu_exec_init(&fragment_qpu, record.fs_uniforms);
    if (!vc4_v3d_run_stage(
            read_func, opaque, record.fs_code, record.fs_uniforms,
            &fragment_qpu, result,
            VC4_V3D_PIPELINE_FAULT_QPU_FRAGMENT)) {
        return false;
    }
    if (!fragment_qpu.tlb_color_all_valid ||
        !fragment_qpu.scoreboard_unlocked) {
        return vc4_v3d_pipeline_set_fault(
            result, VC4_V3D_PIPELINE_FAULT_FRAGMENT_OUTPUT,
            (fragment_qpu.tlb_color_all_valid ? 1u : 0u) |
            (fragment_qpu.scoreboard_unlocked ? 2u : 0u));
    }

    color = fragment_qpu.tlb_color_all.lane[0];
    for (unsigned lane = 1; lane < primitive->length; lane++) {
        if (fragment_qpu.tlb_color_all.lane[lane] != color) {
            return vc4_v3d_pipeline_set_fault(
                result, VC4_V3D_PIPELINE_FAULT_FRAGMENT_OUTPUT, lane);
        }
    }

    result->triangle_count = primitive->length / 3;
    for (unsigned triangle = 0;
         triangle < result->triangle_count; triangle++) {
        VC4V3DPipelineTriangle *output = &result->triangles[triangle];

        output->color = color;
        for (unsigned vertex = 0; vertex < 3; vertex++) {
            unsigned lane = triangle * 3 + vertex;
            uint32_t packed = coordinate_qpu.vpm[packed_row].lane[lane];

            output->x[vertex] =
                (int32_t)(int16_t)(packed & 0xffffu) + viewport_x;
            output->y[vertex] =
                (int32_t)(int16_t)(packed >> 16) + viewport_y;
        }
    }
    return true;
}

const char *vc4_v3d_pipeline_fault_name(VC4V3DPipelineFault fault)
{
    static const char *const names[] = {
        [VC4_V3D_PIPELINE_FAULT_NONE] = "none",
        [VC4_V3D_PIPELINE_FAULT_STATE] = "state",
        [VC4_V3D_PIPELINE_FAULT_MODE] = "mode",
        [VC4_V3D_PIPELINE_FAULT_LENGTH] = "length",
        [VC4_V3D_PIPELINE_FAULT_SHADER_RECORD] = "shader-record",
        [VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SELECT] = "attribute-select",
        [VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SIZE] = "attribute-size",
        [VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_BOUNDS] = "attribute-bounds",
        [VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_READ] = "attribute-read",
        [VC4_V3D_PIPELINE_FAULT_QPU_COORDINATE] = "qpu-coordinate",
        [VC4_V3D_PIPELINE_FAULT_QPU_VERTEX] = "qpu-vertex",
        [VC4_V3D_PIPELINE_FAULT_QPU_FRAGMENT] = "qpu-fragment",
        [VC4_V3D_PIPELINE_FAULT_COORDINATE_LAYOUT] = "coordinate-layout",
        [VC4_V3D_PIPELINE_FAULT_VARYINGS] = "varyings",
        [VC4_V3D_PIPELINE_FAULT_FRAGMENT_OUTPUT] = "fragment-output",
    };

    return fault < ARRAY_SIZE(names) && names[fault] != NULL ?
           names[fault] : "unknown";
}
