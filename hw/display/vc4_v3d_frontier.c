/*
 * VideoCore IV V3D shader and primitive frontier diagnostics
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_qpu.h"
#include "hw/display/vc4_v3d_frontier.h"
#include "qemu/log.h"

#define VC4_MAX_SHADER_ATTRIBUTES       8
#define VC4_MAX_UNIFORM_TRACE_WORDS    16
#define VC4_MAX_ATTRIBUTE_TRACE_BYTES  16
#define VC4_MAX_ATTRIBUTE_TRACE_VERTICES 3

#define VC4_PACKET_GL_INDEXED_PRIMITIVE 32
#define VC4_PACKET_GL_ARRAY_PRIMITIVE   33

typedef struct VC4V3DShaderAttribute {
    uint32_t address;
    uint32_t stride;
    uint16_t bytes;
    uint8_t vs_vpm_offset;
    uint8_t cs_vpm_offset;
} VC4V3DShaderAttribute;

typedef struct VC4V3DShaderRecord {
    uint32_t address;
    uint32_t fs_code;
    uint32_t fs_uniforms;
    uint32_t vs_code;
    uint32_t vs_uniforms;
    uint32_t cs_code;
    uint32_t cs_uniforms;
    uint8_t flags;
    uint8_t fs_varyings;
    uint8_t vs_attribute_select;
    uint8_t vs_attribute_size;
    uint8_t cs_attribute_select;
    uint8_t cs_attribute_size;
    uint8_t attribute_count;
    bool extended;
    VC4V3DShaderAttribute attributes[VC4_MAX_SHADER_ATTRIBUTES];
} VC4V3DShaderRecord;

typedef struct VC4V3DFrontierReader {
    VC4V3DFrontierReadFunc read_func;
    void *opaque;
    const char *device_name;
} VC4V3DFrontierReader;

static bool vc4_v3d_frontier_read(VC4V3DFrontierReader *reader,
                                  uint32_t address, void *buffer,
                                  size_t size)
{
    return reader->read_func(reader->opaque, address, buffer, size);
}

static bool vc4_v3d_frontier_qpu_read(void *opaque, uint32_t address,
                                      void *buffer, size_t size)
{
    VC4V3DFrontierReader *reader = opaque;

    return vc4_v3d_frontier_read(reader, address, buffer, size);
}

static bool vc4_v3d_frontier_read_offset(VC4V3DFrontierReader *reader,
                                         uint32_t base, uint32_t offset,
                                         void *buffer, size_t size)
{
    uint64_t address = (uint64_t)base + offset;

    return address <= UINT32_MAX &&
           vc4_v3d_frontier_read(reader, (uint32_t)address, buffer, size);
}

static bool vc4_v3d_frontier_read_u32(VC4V3DFrontierReader *reader,
                                      uint32_t address, uint32_t *value)
{
    uint8_t bytes[4];

    if (!vc4_v3d_frontier_read(reader, address, bytes, sizeof(bytes))) {
        return false;
    }
    *value = ldl_le_p(bytes);
    return true;
}

static const char *vc4_v3d_primitive_name(unsigned mode)
{
    static const char *const names[7] = {
        [0] = "points",
        [1] = "lines",
        [2] = "line-loop",
        [3] = "line-strip",
        [4] = "triangles",
        [5] = "triangle-strip",
        [6] = "triangle-fan",
    };

    return mode < ARRAY_SIZE(names) ? names[mode] : "unknown";
}

static bool vc4_v3d_decode_shader_record(VC4V3DFrontierReader *reader,
                                         uint32_t raw,
                                         VC4V3DShaderRecord *record)
{
    uint8_t fixed[36];
    unsigned count = raw & 0x7;

    if (count == 0) {
        count = VC4_MAX_SHADER_ATTRIBUTES;
    }
    *record = (VC4V3DShaderRecord) {
        .address = raw & ~0xfu,
        .attribute_count = count,
        .extended = (raw & 0x8) != 0,
    };

    if (record->address == 0 ||
        !vc4_v3d_frontier_read(reader, record->address,
                               fixed, sizeof(fixed))) {
        return false;
    }

    record->flags = fixed[0];
    record->fs_varyings = fixed[3];
    record->fs_code = ldl_le_p(fixed + 4);
    record->fs_uniforms = ldl_le_p(fixed + 8);
    record->vs_attribute_select = fixed[14];
    record->vs_attribute_size = fixed[15];
    record->vs_code = ldl_le_p(fixed + 16);
    record->vs_uniforms = ldl_le_p(fixed + 20);
    record->cs_attribute_select = fixed[26];
    record->cs_attribute_size = fixed[27];
    record->cs_code = ldl_le_p(fixed + 28);
    record->cs_uniforms = ldl_le_p(fixed + 32);

    for (unsigned index = 0; index < count; index++) {
        VC4V3DShaderAttribute *attribute = &record->attributes[index];
        uint8_t bytes[8];

        if (!vc4_v3d_frontier_read_offset(
                reader, record->address, 36 + index * 8,
                bytes, sizeof(bytes))) {
            return false;
        }
        attribute->address = ldl_le_p(bytes);
        attribute->bytes = bytes[4] + 1;
        attribute->stride = bytes[5];
        attribute->vs_vpm_offset = bytes[6];
        attribute->cs_vpm_offset = bytes[7];

        if (record->extended) {
            uint32_t high_stride;

            if (!vc4_v3d_frontier_read_offset(
                    reader, record->address, 100 + index * 4,
                    bytes, sizeof(high_stride))) {
                return false;
            }
            high_stride = ldl_le_p(bytes);
            attribute->stride |= high_stride & ~0xffu;
        }
    }

    return true;
}

static void vc4_v3d_trace_uniforms(VC4V3DFrontierReader *reader,
                                   const char *stage, uint32_t address)
{
    if (address == 0) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier uniforms stage=%s address=0\n",
                      reader->device_name, stage);
        return;
    }

    for (unsigned index = 0;
         index < VC4_MAX_UNIFORM_TRACE_WORDS; index++) {
        uint64_t current = (uint64_t)address + index * sizeof(uint32_t);
        uint32_t value;

        if (current > UINT32_MAX ||
            !vc4_v3d_frontier_read_u32(
                reader, (uint32_t)current, &value)) {
            qemu_log_mask(LOG_UNIMP,
                          "%s: frontier uniforms stage=%s stopped "
                          "index=%u address=0x%08" PRIx64 "\n",
                          reader->device_name, stage, index, current);
            return;
        }
        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier uniform stage=%s index=%u "
                      "address=0x%08" PRIx64 " value=0x%08x\n",
                      reader->device_name, stage, index, current, value);
    }
}

static void vc4_v3d_trace_attribute_data(
    VC4V3DFrontierReader *reader,
    const VC4V3DShaderAttribute *attribute,
    unsigned attribute_index, uint32_t first, uint32_t length)
{
    unsigned vertices = MIN(
        length, (uint32_t)VC4_MAX_ATTRIBUTE_TRACE_VERTICES);
    size_t size = MIN((size_t)attribute->bytes,
                      (size_t)VC4_MAX_ATTRIBUTE_TRACE_BYTES);

    for (unsigned vertex = 0; vertex < vertices; vertex++) {
        uint64_t element = (uint64_t)first + vertex;
        uint64_t address = attribute->address;
        uint8_t bytes[VC4_MAX_ATTRIBUTE_TRACE_BYTES] = { 0 };

        if (attribute->stride != 0) {
            address += element * attribute->stride;
        }
        if (address > UINT32_MAX ||
            !vc4_v3d_frontier_read(
                reader, (uint32_t)address, bytes, size)) {
            qemu_log_mask(LOG_UNIMP,
                          "%s: frontier attribute-data read failed "
                          "index=%u vertex=%u address=0x%08" PRIx64 "\n",
                          reader->device_name, attribute_index,
                          vertex, address);
            continue;
        }
        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier attribute-data index=%u vertex=%u "
                      "address=0x%08" PRIx64 " size=%zu "
                      "words=%08x,%08x,%08x,%08x\n",
                      reader->device_name, attribute_index, vertex,
                      address, size, ldl_le_p(bytes),
                      ldl_le_p(bytes + 4), ldl_le_p(bytes + 8),
                      ldl_le_p(bytes + 12));
    }
}

static void vc4_v3d_report_primitive(
    VC4V3DFrontierReader *reader,
    const VC4V3DFrontierState *state,
    const VC4V3DPrimitiveInfo *primitive)
{
    unsigned mode = primitive->mode_byte & 0xf;

    if (!primitive->indexed) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier primitive thread=%u packet=0x%02x "
                      "mode=%u:%s length=%u first=%u "
                      "bin=%ux%u flags=0x%02x valid=%u "
                      "alloc=0x%08x+0x%08x state=0x%08x\n",
                      reader->device_name, primitive->thread,
                      primitive->packet, mode,
                      vc4_v3d_primitive_name(mode), primitive->length,
                      primitive->first, state->bin_tiles_x,
                      state->bin_tiles_y, state->bin_flags,
                      state->have_binning_config, state->bin_alloc_base,
                      state->bin_alloc_size, state->bin_state_base);
        return;
    }

    qemu_log_mask(LOG_UNIMP,
                  "%s: frontier primitive thread=%u packet=0x%02x "
                  "mode=%u:%s length=%u index-size=%u "
                  "indices=0x%08x max-index=%u "
                  "bin=%ux%u flags=0x%02x valid=%u "
                  "alloc=0x%08x+0x%08x state=0x%08x\n",
                  reader->device_name, primitive->thread,
                  primitive->packet, mode,
                  vc4_v3d_primitive_name(mode), primitive->length,
                  (primitive->mode_byte & 0x10) ? 2 : 1,
                  primitive->index_address, primitive->max_index,
                  state->bin_tiles_x, state->bin_tiles_y,
                  state->bin_flags, state->have_binning_config,
                  state->bin_alloc_base, state->bin_alloc_size,
                  state->bin_state_base);
}

bool vc4_v3d_frontier_report(VC4V3DFrontierReadFunc read_func,
                              void *opaque, const char *device_name,
                              const VC4V3DFrontierState *state,
                              const VC4V3DPrimitiveInfo *primitive,
                              uint32_t *last_pc,
                              uint32_t *last_shader_record)
{
    VC4V3DFrontierReader reader = {
        .read_func = read_func,
        .opaque = opaque,
        .device_name = device_name,
    };
    VC4V3DShaderRecord record;

    if (*last_pc == primitive->pc &&
        *last_shader_record == state->shader_record) {
        return false;
    }
    *last_pc = primitive->pc;
    *last_shader_record = state->shader_record;

    vc4_v3d_report_primitive(&reader, state, primitive);
    if (!state->have_shader_record) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier primitive has no GL shader state\n",
                      device_name);
        return true;
    }
    if (!vc4_v3d_decode_shader_record(
            &reader, state->shader_record, &record)) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier shader record unreadable "
                      "raw=0x%08x\n",
                      device_name, state->shader_record);
        return true;
    }

    qemu_log_mask(LOG_UNIMP,
                  "%s: frontier shader record=0x%08x raw=0x%08x "
                  "attrs=%u extended=%u flags=0x%02x varyings=%u "
                  "fs=0x%08x fs-uniforms=0x%08x "
                  "vs=0x%08x vs-uniforms=0x%08x "
                  "cs=0x%08x cs-uniforms=0x%08x "
                  "vs-select=0x%02x vs-size=%u "
                  "cs-select=0x%02x cs-size=%u\n",
                  device_name, record.address, state->shader_record,
                  record.attribute_count, record.extended,
                  record.flags, record.fs_varyings,
                  record.fs_code, record.fs_uniforms,
                  record.vs_code, record.vs_uniforms,
                  record.cs_code, record.cs_uniforms,
                  record.vs_attribute_select, record.vs_attribute_size,
                  record.cs_attribute_select, record.cs_attribute_size);

    for (unsigned index = 0; index < record.attribute_count; index++) {
        const VC4V3DShaderAttribute *attribute =
            &record.attributes[index];

        qemu_log_mask(LOG_UNIMP,
                      "%s: frontier attribute index=%u address=0x%08x "
                      "bytes=%u stride=%u vs-vpm=%u cs-vpm=%u\n",
                      device_name, index, attribute->address,
                      attribute->bytes, attribute->stride,
                      attribute->vs_vpm_offset,
                      attribute->cs_vpm_offset);
        if (!primitive->indexed) {
            vc4_v3d_trace_attribute_data(
                &reader, attribute, index,
                primitive->first, primitive->length);
        }
    }

    vc4_qpu_trace_program(vc4_v3d_frontier_qpu_read, &reader,
                          device_name, "fs", record.fs_code);
    vc4_v3d_trace_uniforms(&reader, "fs", record.fs_uniforms);
    vc4_qpu_trace_program(vc4_v3d_frontier_qpu_read, &reader,
                          device_name, "vs", record.vs_code);
    vc4_v3d_trace_uniforms(&reader, "vs", record.vs_uniforms);
    vc4_qpu_trace_program(vc4_v3d_frontier_qpu_read, &reader,
                          device_name, "cs", record.cs_code);
    vc4_v3d_trace_uniforms(&reader, "cs", record.cs_uniforms);
    return true;
}
