/*
 * VideoCore IV bounded primitive and shader pipeline
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_VC4_V3D_PIPELINE_H
#define HW_DISPLAY_VC4_V3D_PIPELINE_H

#include "hw/display/vc4_qpu_exec.h"
#include "hw/display/vc4_v3d_frontier.h"

#define VC4_V3D_PIPELINE_MAX_VERTICES  VC4_QPU_LANES
#define VC4_V3D_PIPELINE_MAX_TRIANGLES (VC4_QPU_LANES / 3)

typedef enum VC4V3DPipelineFault {
    VC4_V3D_PIPELINE_FAULT_NONE,
    VC4_V3D_PIPELINE_FAULT_STATE,
    VC4_V3D_PIPELINE_FAULT_MODE,
    VC4_V3D_PIPELINE_FAULT_LENGTH,
    VC4_V3D_PIPELINE_FAULT_SHADER_RECORD,
    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SELECT,
    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_SIZE,
    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_BOUNDS,
    VC4_V3D_PIPELINE_FAULT_ATTRIBUTE_READ,
    VC4_V3D_PIPELINE_FAULT_QPU_COORDINATE,
    VC4_V3D_PIPELINE_FAULT_QPU_VERTEX,
    VC4_V3D_PIPELINE_FAULT_QPU_FRAGMENT,
    VC4_V3D_PIPELINE_FAULT_COORDINATE_LAYOUT,
    VC4_V3D_PIPELINE_FAULT_VARYINGS,
    VC4_V3D_PIPELINE_FAULT_FRAGMENT_OUTPUT,
} VC4V3DPipelineFault;

typedef struct VC4V3DPipelineTriangle {
    int32_t x[3];
    int32_t y[3];
    uint32_t color;
} VC4V3DPipelineTriangle;

typedef struct VC4V3DPipelineResult {
    VC4V3DPipelineFault fault;
    VC4QPUExecFault qpu_fault;
    uint32_t detail;
    unsigned triangle_count;
    VC4V3DPipelineTriangle triangles[VC4_V3D_PIPELINE_MAX_TRIANGLES];
} VC4V3DPipelineResult;

bool vc4_v3d_execute_array_primitive(VC4V3DFrontierReadFunc read_func,
                                     void *opaque,
                                     const VC4V3DFrontierState *state,
                                     const VC4V3DPrimitiveInfo *primitive,
                                     int16_t viewport_x,
                                     int16_t viewport_y,
                                     VC4V3DPipelineResult *result);

const char *vc4_v3d_pipeline_fault_name(VC4V3DPipelineFault fault);

#endif /* HW_DISPLAY_VC4_V3D_PIPELINE_H */
