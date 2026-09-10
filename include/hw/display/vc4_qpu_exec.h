/*
 * VideoCore IV QPU bounded execution core
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_VC4_QPU_EXEC_H
#define HW_DISPLAY_VC4_QPU_EXEC_H

#include "hw/display/vc4_qpu.h"

#define VC4_QPU_LANES       16
#define VC4_QPU_REGFILE_SIZE 32
#define VC4_QPU_VPM_ROWS    64

typedef struct VC4QPUVector {
    uint32_t lane[VC4_QPU_LANES];
} VC4QPUVector;

typedef enum VC4QPUExecFault {
    VC4_QPU_EXEC_FAULT_NONE,
    VC4_QPU_EXEC_FAULT_CODE_READ,
    VC4_QPU_EXEC_FAULT_UNIFORM_READ,
    VC4_QPU_EXEC_FAULT_VARYING_READ,
    VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT,
    VC4_QPU_EXEC_FAULT_SIGNAL,
    VC4_QPU_EXEC_FAULT_CONDITION,
    VC4_QPU_EXEC_FAULT_FLAGS,
    VC4_QPU_EXEC_FAULT_UNPACK,
    VC4_QPU_EXEC_FAULT_PACK,
    VC4_QPU_EXEC_FAULT_ADD_OP,
    VC4_QPU_EXEC_FAULT_MUL_OP,
    VC4_QPU_EXEC_FAULT_READ_ADDRESS,
    VC4_QPU_EXEC_FAULT_WRITE_ADDRESS,
    VC4_QPU_EXEC_FAULT_SMALL_IMMEDIATE,
    VC4_QPU_EXEC_FAULT_VPM_SETUP,
    VC4_QPU_EXEC_FAULT_VPM_READ,
    VC4_QPU_EXEC_FAULT_FLOAT_CONVERSION,
} VC4QPUExecFault;

typedef struct VC4QPUExecState {
    VC4QPUVector reg_a[VC4_QPU_REGFILE_SIZE];
    VC4QPUVector reg_b[VC4_QPU_REGFILE_SIZE];
    VC4QPUVector accumulator[6];
    VC4QPUVector vpm[VC4_QPU_VPM_ROWS];

    uint32_t uniform_address;

    /*
     * Fragment-pipeline varying FIFO input.  A VARYING_READ returns the
     * precomputed VP term and simultaneously loads its C coefficient into r5.
     * The caller owns these vectors for the duration of vc4_qpu_execute().
     */
    const VC4QPUVector *varying_partial;
    const VC4QPUVector *varying_c;
    unsigned varying_count;
    unsigned varying_index;

    uint32_t pc;
    unsigned instruction_count;
    unsigned active_lanes;

    bool vpm_read_configured;
    uint8_t vpm_read_row;
    uint8_t vpm_read_stride;
    uint8_t vpm_read_remaining;

    bool vpm_write_configured;
    uint8_t vpm_write_row;
    uint8_t vpm_write_stride;

    /* Exact rows emitted by the current program, for typed pipeline output. */
    bool vpm_wrote;
    uint8_t vpm_first_write_row;
    uint8_t vpm_last_write_row;
    unsigned vpm_write_count;

    bool tlb_color_all_valid;
    VC4QPUVector tlb_color_all;
    bool scoreboard_unlocked;
    bool last_thread_switch;

    VC4QPUExecFault fault;
    unsigned fault_index;
    uint64_t fault_word;
    uint32_t fault_detail;
} VC4QPUExecState;

void vc4_qpu_exec_init(VC4QPUExecState *state, uint32_t uniform_address);

bool vc4_qpu_exec_set_active_lanes(VC4QPUExecState *state,
                                   unsigned active_lanes);

bool vc4_qpu_exec_set_varyings(VC4QPUExecState *state,
                                const VC4QPUVector *partial,
                                const VC4QPUVector *c, unsigned count);

bool vc4_qpu_execute(VC4QPUReadFunc read_func, void *opaque,
                     uint32_t code_address, VC4QPUExecState *state);

const char *vc4_qpu_exec_fault_name(VC4QPUExecFault fault);

#endif
