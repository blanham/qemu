/*
 * VideoCore IV QPU instruction decoding and bounded tracing
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "hw/display/vc4_qpu.h"
#include "qemu/log.h"

#define VC4_QPU_MAX_TRACE_WORDS       64
#define VC4_QPU_PROGRAM_END_SIGNAL     3
#define VC4_QPU_PROGRAM_END_DELAY      2

#define VC4_QPU_FIELD(word, shift, mask) \
    (((word) >> (shift)) & (mask))

static const char *vc4_qpu_signal_name(unsigned signal)
{
    static const char *const names[16] = {
        [0] = "breakpoint",
        [1] = "none",
        [2] = "thread-switch",
        [3] = "program-end",
        [4] = "scoreboard-wait",
        [5] = "scoreboard-unlock",
        [6] = "last-thread-switch",
        [7] = "coverage-load",
        [8] = "color-load",
        [9] = "color-load-end",
        [10] = "tmu0-load",
        [11] = "tmu1-load",
        [12] = "alpha-mask-load",
        [13] = "small-immediate",
        [14] = "load-immediate",
        [15] = "branch",
    };

    return signal < ARRAY_SIZE(names) && names[signal] != NULL ?
           names[signal] : "unknown";
}

void vc4_qpu_decode(uint64_t word, VC4QPUInstruction *instruction)
{
    *instruction = (VC4QPUInstruction) {
        .word = word,
        .signal = VC4_QPU_FIELD(word, 60, 0xf),
        .unpack = VC4_QPU_FIELD(word, 57, 0x7),
        .pm = (word >> 56) & 1,
        .pack = VC4_QPU_FIELD(word, 52, 0xf),
        .cond_add = VC4_QPU_FIELD(word, 49, 0x7),
        .cond_mul = VC4_QPU_FIELD(word, 46, 0x7),
        .set_flags = (word >> 45) & 1,
        .ws = (word >> 44) & 1,
        .waddr_add = VC4_QPU_FIELD(word, 38, 0x3f),
        .waddr_mul = VC4_QPU_FIELD(word, 32, 0x3f),
        .op_mul = VC4_QPU_FIELD(word, 29, 0x7),
        .op_add = VC4_QPU_FIELD(word, 24, 0x1f),
        .raddr_a = VC4_QPU_FIELD(word, 18, 0x3f),
        .raddr_b = VC4_QPU_FIELD(word, 12, 0x3f),
        .add_a = VC4_QPU_FIELD(word, 9, 0x7),
        .add_b = VC4_QPU_FIELD(word, 6, 0x7),
        .mul_a = VC4_QPU_FIELD(word, 3, 0x7),
        .mul_b = VC4_QPU_FIELD(word, 0, 0x7),
    };
}

static bool vc4_qpu_read_word(VC4QPUReadFunc read_func, void *opaque,
                              uint32_t address, uint64_t *word)
{
    uint8_t bytes[8];

    if (!read_func(opaque, address, bytes, sizeof(bytes))) {
        return false;
    }
    *word = ldq_le_p(bytes);
    return true;
}

bool vc4_qpu_trace_program(VC4QPUReadFunc read_func, void *opaque,
                           const char *device_name, const char *stage,
                           uint32_t address)
{
    unsigned delay_words = 0;
    bool saw_program_end = false;

    if (address == 0) {
        qemu_log_mask(LOG_UNIMP,
                      "%s: qpu frontier stage=%s address=0\n",
                      device_name, stage);
        return false;
    }

    for (unsigned index = 0; index < VC4_QPU_MAX_TRACE_WORDS; index++) {
        VC4QPUInstruction instruction;
        uint64_t word;
        uint64_t current = (uint64_t)address + index * sizeof(word);

        if (saw_program_end && delay_words == 0) {
            return true;
        }

        if (current > UINT32_MAX ||
            !vc4_qpu_read_word(read_func, opaque, (uint32_t)current,
                               &word)) {
            qemu_log_mask(LOG_UNIMP,
                          "%s: qpu frontier read failed stage=%s "
                          "index=%u address=0x%08" PRIx64 "\n",
                          device_name, stage, index, current);
            return false;
        }

        vc4_qpu_decode(word, &instruction);
        qemu_log_mask(LOG_UNIMP,
                      "%s: qpu frontier stage=%s index=%u "
                      "address=0x%08" PRIx64 " word=0x%016" PRIx64 " "
                      "sig=%u:%s unpack=%u pm=%u pack=%u "
                      "ca=%u cm=%u sf=%u ws=%u wa=%u wm=%u "
                      "add=%u mul=%u ra=%u rb=%u "
                      "aa=%u ab=%u ma=%u mb=%u\n",
                      device_name, stage, index, current, word,
                      instruction.signal,
                      vc4_qpu_signal_name(instruction.signal),
                      instruction.unpack, instruction.pm,
                      instruction.pack, instruction.cond_add,
                      instruction.cond_mul, instruction.set_flags,
                      instruction.ws, instruction.waddr_add,
                      instruction.waddr_mul, instruction.op_add,
                      instruction.op_mul, instruction.raddr_a,
                      instruction.raddr_b, instruction.add_a,
                      instruction.add_b, instruction.mul_a,
                      instruction.mul_b);

        if (saw_program_end) {
            delay_words--;
            continue;
        }
        if (instruction.signal == VC4_QPU_PROGRAM_END_SIGNAL) {
            saw_program_end = true;
            delay_words = VC4_QPU_PROGRAM_END_DELAY;
        }
    }

    qemu_log_mask(LOG_UNIMP,
                  "%s: qpu frontier stage=%s exceeded %u words "
                  "without program end\n",
                  device_name, stage, VC4_QPU_MAX_TRACE_WORDS);
    return true;
}
