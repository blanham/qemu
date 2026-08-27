/*
 * VideoCore IV QPU instruction decoding and bounded tracing
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef HW_DISPLAY_VC4_QPU_H
#define HW_DISPLAY_VC4_QPU_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef bool (*VC4QPUReadFunc)(void *opaque, uint32_t address,
                               void *buffer, size_t size);

typedef struct VC4QPUInstruction {
    uint64_t word;
    uint8_t signal;
    uint8_t unpack;
    uint8_t pack;
    uint8_t cond_add;
    uint8_t cond_mul;
    uint8_t waddr_add;
    uint8_t waddr_mul;
    uint8_t op_add;
    uint8_t op_mul;
    uint8_t raddr_a;
    uint8_t raddr_b;
    uint8_t add_a;
    uint8_t add_b;
    uint8_t mul_a;
    uint8_t mul_b;
    bool pm;
    bool ws;
    bool set_flags;
} VC4QPUInstruction;

void vc4_qpu_decode(uint64_t word, VC4QPUInstruction *instruction);

/*
 * Log a bounded program transcript.  The callback is observational: callers
 * should not turn a failed trace read into guest-visible device state.
 */
bool vc4_qpu_trace_program(VC4QPUReadFunc read_func, void *opaque,
                           const char *device_name, const char *stage,
                           uint32_t address);

#endif /* HW_DISPLAY_VC4_QPU_H */
