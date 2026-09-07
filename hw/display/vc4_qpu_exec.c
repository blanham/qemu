/*
 * VideoCore IV QPU bounded execution core
 *
 * This is deliberately an explicit subset executor.  Unsupported ISA,
 * register, packing, and VPM modes fail closed instead of being guessed.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_qpu_exec.h"
#include "qemu/bswap.h"

#define VC4_QPU_MAX_EXEC_WORDS          64
#define VC4_QPU_PROGRAM_END_SIGNAL       3
#define VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL 5
#define VC4_QPU_SMALL_IMMEDIATE_SIGNAL  13
#define VC4_QPU_LOAD_IMMEDIATE_SIGNAL   14

#define VC4_QPU_COND_NEVER                0
#define VC4_QPU_COND_ALWAYS               1

#define VC4_QPU_ADD_NOP                   0
#define VC4_QPU_ADD_FSUB                  2
#define VC4_QPU_ADD_FTOI                  7
#define VC4_QPU_ADD_OR                   21

#define VC4_QPU_MUL_NOP                   0
#define VC4_QPU_MUL_FMUL                  1

#define VC4_QPU_MUX_R0                    0
#define VC4_QPU_MUX_R5                    5
#define VC4_QPU_MUX_A                     6
#define VC4_QPU_MUX_B                     7

#define VC4_QPU_REG_UNIFORM              32
#define VC4_QPU_REG_NULL                 39
#define VC4_QPU_REG_TLB_COLOR_ALL        46
#define VC4_QPU_REG_VPM                  48
#define VC4_QPU_REG_VPM_SETUP            49

static bool vc4_qpu_exec_set_fault(VC4QPUExecState *state,
                                   VC4QPUExecFault fault,
                                   uint32_t detail)
{
    if (state->fault == VC4_QPU_EXEC_FAULT_NONE) {
        state->fault = fault;
        state->fault_index = state->instruction_count;
        state->fault_detail = detail;
    }
    return false;
}

static uint32_t vc4_qpu_float_to_bits(float value)
{
    uint32_t bits;

    memcpy(&bits, &value, sizeof(bits));
    return bits;
}

static float vc4_qpu_bits_to_float(uint32_t bits)
{
    float value;

    memcpy(&value, &bits, sizeof(value));
    return value;
}

static void vc4_qpu_vector_splat(VC4QPUVector *vector, uint32_t value)
{
    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        vector->lane[lane] = value;
    }
}

static bool vc4_qpu_exec_read_u32(VC4QPUReadFunc read_func, void *opaque,
                                  uint32_t address, uint32_t *value)
{
    uint8_t bytes[4];

    if (!read_func(opaque, address, bytes, sizeof(bytes))) {
        return false;
    }
    *value = ldl_le_p(bytes);
    return true;
}

static bool vc4_qpu_exec_read_u64(VC4QPUReadFunc read_func, void *opaque,
                                  uint32_t address, uint64_t *value)
{
    uint8_t bytes[8];

    if (!read_func(opaque, address, bytes, sizeof(bytes))) {
        return false;
    }
    *value = ldq_le_p(bytes);
    return true;
}

static bool vc4_qpu_exec_setup_vpm_read(VC4QPUExecState *state,
                                        uint32_t setup)
{
    unsigned number = (setup >> 20) & 0xf;
    unsigned stride = (setup >> 12) & 0x3f;
    unsigned horizontal = (setup >> 11) & 1;
    unsigned size = (setup >> 8) & 0x3;

    if ((setup >> 30) != 0 || !horizontal || size != 2 ||
        (setup & 0xc0) != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_VPM_SETUP, setup);
    }

    state->vpm_read_configured = true;
    state->vpm_read_row = setup & 0x3f;
    state->vpm_read_stride = stride == 0 ? VC4_QPU_VPM_ROWS : stride;
    state->vpm_read_remaining = number == 0 ? VC4_QPU_LANES : number;
    return true;
}

static bool vc4_qpu_exec_setup_vpm_write(VC4QPUExecState *state,
                                         uint32_t setup)
{
    unsigned stride = (setup >> 12) & 0x3f;
    unsigned horizontal = (setup >> 11) & 1;
    unsigned size = (setup >> 8) & 0x3;

    if ((setup >> 30) != 0 || !horizontal || size != 2 ||
        (setup & 0xc0) != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_VPM_SETUP, setup);
    }

    state->vpm_write_configured = true;
    state->vpm_write_row = setup & 0x3f;
    state->vpm_write_stride = stride == 0 ? VC4_QPU_VPM_ROWS : stride;
    return true;
}

static bool vc4_qpu_exec_read_vpm(VC4QPUExecState *state,
                                  VC4QPUVector *value)
{
    if (!state->vpm_read_configured || state->vpm_read_remaining == 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_VPM_READ, state->vpm_read_row);
    }

    *value = state->vpm[state->vpm_read_row];
    state->vpm_read_row =
        (state->vpm_read_row + state->vpm_read_stride) &
        (VC4_QPU_VPM_ROWS - 1);
    state->vpm_read_remaining--;
    return true;
}

static bool vc4_qpu_exec_write_vpm(VC4QPUExecState *state,
                                   const VC4QPUVector *value)
{
    if (!state->vpm_write_configured) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_VPM_SETUP, 0);
    }

    state->vpm[state->vpm_write_row] = *value;
    state->vpm_write_row =
        (state->vpm_write_row + state->vpm_write_stride) &
        (VC4_QPU_VPM_ROWS - 1);
    return true;
}

static bool vc4_qpu_exec_read_uniform(VC4QPUReadFunc read_func,
                                      void *opaque,
                                      VC4QPUExecState *state,
                                      VC4QPUVector *value)
{
    uint32_t scalar;

    if (!vc4_qpu_exec_read_u32(read_func, opaque,
                               state->uniform_address, &scalar)) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_UNIFORM_READ,
            state->uniform_address);
    }
    vc4_qpu_vector_splat(value, scalar);
    state->uniform_address += sizeof(scalar);
    return true;
}

static bool vc4_qpu_exec_read_port(VC4QPUReadFunc read_func, void *opaque,
                                   VC4QPUExecState *state, bool file_a,
                                   unsigned address, VC4QPUVector *value)
{
    if (address < VC4_QPU_REGFILE_SIZE) {
        *value = file_a ? state->reg_a[address] : state->reg_b[address];
        return true;
    }

    switch (address) {
    case VC4_QPU_REG_UNIFORM:
        return vc4_qpu_exec_read_uniform(
            read_func, opaque, state, value);
    case VC4_QPU_REG_NULL:
        memset(value, 0, sizeof(*value));
        return true;
    case VC4_QPU_REG_VPM:
        return vc4_qpu_exec_read_vpm(state, value);
    default:
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_READ_ADDRESS,
            (file_a ? 0x100 : 0x200) | address);
    }
}

static bool vc4_qpu_exec_small_immediate(VC4QPUExecState *state,
                                         unsigned code,
                                         VC4QPUVector *value)
{
    uint32_t scalar;

    if (code < 16) {
        scalar = code;
    } else if (code < 32) {
        scalar = (uint32_t)(int32_t)(code - 32);
    } else if (code < 40) {
        scalar = (127 + code - 32) << 23;
    } else if (code < 48) {
        scalar = (119 + code - 40) << 23;
    } else {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_SMALL_IMMEDIATE, code);
    }

    vc4_qpu_vector_splat(value, scalar);
    return true;
}

static const VC4QPUVector *vc4_qpu_exec_mux(
    VC4QPUExecState *state, unsigned mux,
    const VC4QPUVector *port_a, const VC4QPUVector *port_b)
{
    if (mux <= VC4_QPU_MUX_R5) {
        return &state->accumulator[mux];
    }
    return mux == VC4_QPU_MUX_A ? port_a : port_b;
}

static bool vc4_qpu_exec_add(VC4QPUExecState *state, unsigned operation,
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

            if (!isfinite(value) || value < INT32_MIN || value > INT32_MAX) {
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

static bool vc4_qpu_exec_mul(VC4QPUExecState *state, unsigned operation,
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

static bool vc4_qpu_exec_write_general(VC4QPUExecState *state,
                                       bool file_a, unsigned address,
                                       const VC4QPUVector *value,
                                       unsigned pack)
{
    VC4QPUVector *destination;

    if (address >= VC4_QPU_REGFILE_SIZE) {
        return false;
    }
    destination = file_a ? &state->reg_a[address] : &state->reg_b[address];

    if (pack == 0) {
        *destination = *value;
        return true;
    }
    if (!file_a || (pack != 1 && pack != 2)) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, pack);
    }

    for (unsigned lane = 0; lane < VC4_QPU_LANES; lane++) {
        uint32_t packed = value->lane[lane] & 0xffff;

        if (pack == 1) {
            destination->lane[lane] =
                (destination->lane[lane] & 0xffff0000) | packed;
        } else {
            destination->lane[lane] =
                (destination->lane[lane] & 0x0000ffff) | (packed << 16);
        }
    }
    return true;
}

static bool vc4_qpu_exec_write(VC4QPUExecState *state, bool file_a,
                               unsigned address, const VC4QPUVector *value,
                               unsigned pack)
{
    if (address < VC4_QPU_REGFILE_SIZE) {
        return vc4_qpu_exec_write_general(
            state, file_a, address, value, pack);
    }
    if (pack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, pack);
    }

    if (address >= 32 && address <= 35) {
        state->accumulator[address - 32] = *value;
        return true;
    }

    switch (address) {
    case VC4_QPU_REG_NULL:
        return true;
    case VC4_QPU_REG_TLB_COLOR_ALL:
        state->tlb_color_all = *value;
        state->tlb_color_all_valid = true;
        return true;
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

static bool vc4_qpu_exec_condition(VC4QPUExecState *state,
                                   unsigned condition, bool *execute)
{
    if (condition > VC4_QPU_COND_ALWAYS) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_CONDITION, condition);
    }
    *execute = condition == VC4_QPU_COND_ALWAYS;
    return true;
}

static bool vc4_qpu_exec_load_immediate(VC4QPUExecState *state,
                                        const VC4QPUInstruction *instruction)
{
    VC4QPUVector value;
    bool add_execute;
    bool mul_execute;

    if (instruction->set_flags) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_FLAGS, 1);
    }
    if (instruction->unpack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_UNPACK, instruction->unpack);
    }
    if (instruction->pack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, instruction->pack);
    }
    if (!vc4_qpu_exec_condition(
            state, instruction->cond_add, &add_execute) ||
        !vc4_qpu_exec_condition(
            state, instruction->cond_mul, &mul_execute)) {
        return false;
    }

    vc4_qpu_vector_splat(&value, instruction->word);
    if (add_execute &&
        !vc4_qpu_exec_write(state, instruction->ws,
                            instruction->waddr_add, &value, 0)) {
        return false;
    }
    if (mul_execute &&
        !vc4_qpu_exec_write(state, !instruction->ws,
                            instruction->waddr_mul, &value, 0)) {
        return false;
    }
    return true;
}

static bool vc4_qpu_exec_alu(VC4QPUReadFunc read_func, void *opaque,
                             VC4QPUExecState *state,
                             const VC4QPUInstruction *instruction)
{
    VC4QPUVector port_a = { 0 };
    VC4QPUVector port_b = { 0 };
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

    if (instruction->set_flags) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_FLAGS, 1);
    }
    if (instruction->unpack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_UNPACK, instruction->unpack);
    }
    if (instruction->pm && instruction->pack != 0) {
        return vc4_qpu_exec_set_fault(
            state, VC4_QPU_EXEC_FAULT_PACK, instruction->pack);
    }
    if (instruction->pack > 2) {
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
        !vc4_qpu_exec_write(state, instruction->ws,
                            instruction->waddr_add, &add_result,
                            instruction->pm ? 0 : instruction->pack)) {
        return false;
    }
    if (mul_execute &&
        !vc4_qpu_exec_write(state, !instruction->ws,
                            instruction->waddr_mul, &mul_result,
                            instruction->pm ? instruction->pack : 0)) {
        return false;
    }
    return true;
}

void vc4_qpu_exec_init(VC4QPUExecState *state, uint32_t uniform_address)
{
    memset(state, 0, sizeof(*state));
    state->uniform_address = uniform_address;
}

bool vc4_qpu_execute(VC4QPUReadFunc read_func, void *opaque,
                     uint32_t code_address, VC4QPUExecState *state)
{
    unsigned end_delay = 0;
    bool saw_program_end = false;

    state->fault = VC4_QPU_EXEC_FAULT_NONE;
    state->fault_detail = 0;
    state->fault_index = 0;
    state->fault_word = 0;
    state->instruction_count = 0;
    state->pc = code_address;
    state->scoreboard_unlocked = false;
    state->tlb_color_all_valid = false;

    for (unsigned index = 0; index < VC4_QPU_MAX_EXEC_WORDS; index++) {
        VC4QPUInstruction instruction;
        uint64_t current = (uint64_t)code_address + index * sizeof(uint64_t);
        uint64_t word;

        state->instruction_count = index;
        if (current > UINT32_MAX ||
            !vc4_qpu_exec_read_u64(read_func, opaque,
                                   (uint32_t)current, &word)) {
            state->pc = current <= UINT32_MAX ? current : UINT32_MAX;
            return vc4_qpu_exec_set_fault(
                state, VC4_QPU_EXEC_FAULT_CODE_READ, state->pc);
        }

        state->pc = current;
        state->fault_word = word;
        vc4_qpu_decode(word, &instruction);

        switch (instruction.signal) {
        case 1:
        case VC4_QPU_PROGRAM_END_SIGNAL:
        case VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL:
        case VC4_QPU_SMALL_IMMEDIATE_SIGNAL:
            if (!vc4_qpu_exec_alu(
                    read_func, opaque, state, &instruction)) {
                return false;
            }
            break;
        case VC4_QPU_LOAD_IMMEDIATE_SIGNAL:
            if (!vc4_qpu_exec_load_immediate(state, &instruction)) {
                return false;
            }
            break;
        default:
            return vc4_qpu_exec_set_fault(
                state, VC4_QPU_EXEC_FAULT_SIGNAL,
                instruction.signal);
        }

        state->instruction_count = index + 1;
        if (instruction.signal == VC4_QPU_SCOREBOARD_UNLOCK_SIGNAL) {
            state->scoreboard_unlocked = true;
        }
        if (saw_program_end) {
            if (--end_delay == 0) {
                state->fault_word = 0;
                return true;
            }
        } else if (instruction.signal == VC4_QPU_PROGRAM_END_SIGNAL) {
            saw_program_end = true;
            end_delay = 2;
        }
    }

    return vc4_qpu_exec_set_fault(
        state, VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT,
        VC4_QPU_MAX_EXEC_WORDS);
}

const char *vc4_qpu_exec_fault_name(VC4QPUExecFault fault)
{
    static const char *const names[] = {
        [VC4_QPU_EXEC_FAULT_NONE] = "none",
        [VC4_QPU_EXEC_FAULT_CODE_READ] = "code-read",
        [VC4_QPU_EXEC_FAULT_UNIFORM_READ] = "uniform-read",
        [VC4_QPU_EXEC_FAULT_PROGRAM_LIMIT] = "program-limit",
        [VC4_QPU_EXEC_FAULT_SIGNAL] = "signal",
        [VC4_QPU_EXEC_FAULT_CONDITION] = "condition",
        [VC4_QPU_EXEC_FAULT_FLAGS] = "flags",
        [VC4_QPU_EXEC_FAULT_UNPACK] = "unpack",
        [VC4_QPU_EXEC_FAULT_PACK] = "pack",
        [VC4_QPU_EXEC_FAULT_ADD_OP] = "add-op",
        [VC4_QPU_EXEC_FAULT_MUL_OP] = "mul-op",
        [VC4_QPU_EXEC_FAULT_READ_ADDRESS] = "read-address",
        [VC4_QPU_EXEC_FAULT_WRITE_ADDRESS] = "write-address",
        [VC4_QPU_EXEC_FAULT_SMALL_IMMEDIATE] = "small-immediate",
        [VC4_QPU_EXEC_FAULT_VPM_SETUP] = "vpm-setup",
        [VC4_QPU_EXEC_FAULT_VPM_READ] = "vpm-read",
        [VC4_QPU_EXEC_FAULT_FLOAT_CONVERSION] = "float-conversion",
    };

    return fault < ARRAY_SIZE(names) && names[fault] != NULL ?
           names[fault] : "unknown";
}
