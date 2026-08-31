/*
 * Functions related to disassembly from the monitor
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "disas-internal.h"
#include "disas/disas.h"
#include "system/memory.h"
#include "hw/core/cpu.h"
#include "qapi/error.h"
#include "qapi/qapi-commands-machine.h"
#include "system/hw_accel.h"
#include "system/runstate.h"
#include "monitor/monitor.h"

/*
 * Get LENGTH bytes from info's buffer, at target address memaddr.
 * Transfer them to myaddr.
 */
static int
virtual_read_memory(bfd_vma memaddr, bfd_byte *myaddr, int length,
                    struct disassemble_info *info)
{
    CPUDebug *s = container_of(info, CPUDebug, info);
    int r = cpu_memory_rw_debug(s->cpu, memaddr, myaddr, length, 0);
    return r ? EIO : 0;
}

static int
physical_read_memory(bfd_vma memaddr, bfd_byte *myaddr, int length,
                     struct disassemble_info *info)
{
    CPUDebug *s = container_of(info, CPUDebug, info);
    MemTxResult res;

    res = address_space_read(s->cpu->as, memaddr, MEMTXATTRS_UNSPECIFIED,
                             myaddr, length);
    return res == MEMTX_OK ? 0 : EIO;
}

#define WD40_DISASSEMBLY_MAX_INSTRUCTIONS 256U
#define WD40_DISASSEMBLY_MAX_BYTES (64U * 1024U)
#define WD40_DISASSEMBLY_DEFAULT_BYTES_PER_INSN 32U
#define WD40_DISASSEMBLY_MAX_INSN_BYTES 64U

typedef struct WD40DisassemblyContext {
    CPUDebug debug;
    WD40MemorySpace space;
    uint64_t first;
    uint64_t last;
} WD40DisassemblyContext;

static int
wd40_disassembly_read_memory(bfd_vma memaddr, bfd_byte *myaddr, int length,
                             struct disassemble_info *info)
{
    CPUDebug *debug = container_of(info, CPUDebug, info);
    WD40DisassemblyContext *context =
        container_of(debug, WD40DisassemblyContext, debug);
    MemTxResult transaction;

    if (length < 0) {
        return EIO;
    }
    if (length == 0) {
        return 0;
    }
    if (memaddr < context->first || memaddr > context->last ||
        (uint64_t)(length - 1) > context->last - memaddr) {
        return EIO;
    }

    switch (context->space) {
    case WD40_MEMORY_SPACE_VIRTUAL:
        return cpu_memory_rw_debug(context->debug.cpu, memaddr, myaddr,
                                   length, false) ? EIO : 0;
    case WD40_MEMORY_SPACE_PHYSICAL:
        transaction = address_space_read(context->debug.cpu->as, memaddr,
                                         MEMTXATTRS_UNSPECIFIED,
                                         myaddr, length);
        return transaction == MEMTX_OK ? 0 : EIO;
    default:
        g_assert_not_reached();
    }
}

static CPUState *
wd40_disassembly_cpu(bool has_cpu_index, int64_t cpu_index)
{
    CPUState *cpu;

    if (!has_cpu_index) {
        return first_cpu;
    }
    CPU_FOREACH(cpu) {
        if (cpu->cpu_index == cpu_index) {
            return cpu;
        }
    }
    return NULL;
}

static char *
wd40_disassembly_bytes_to_hex(const uint8_t *bytes, size_t length)
{
    static const char digits[] = "0123456789abcdef";
    char *hex = g_malloc(length * 2 + 1);
    size_t i;

    for (i = 0; i < length; i++) {
        hex[i * 2] = digits[bytes[i] >> 4];
        hex[i * 2 + 1] = digits[bytes[i] & 0x0f];
    }
    hex[length * 2] = '\0';
    return hex;
}

static int
wd40_disassemble_one(WD40DisassemblyContext *context, uint64_t pc,
                     uint64_t remaining, GString *text, Error **errp)
{
    disassemble_info *info = &context->debug.info;
    int count = -1;

    g_string_set_size(text, 0);
    info->stream = (FILE *)text;  /* abuse this slot */
    info->buffer_vma = pc;
    info->buffer_length = MIN(remaining, (uint64_t)INT_MAX);

    if (info->cap_arch >= 0) {
        count = cap_disas_one(info, pc);
        if (count < 0 && info->print_insn) {
            g_string_set_size(text, 0);
            count = info->print_insn(pc, info);
        }
    } else if (info->print_insn) {
        count = info->print_insn(pc, info);
    } else {
        error_setg(errp, "Disassembly is not supported for CPU type '%s'",
                   object_get_typename(OBJECT(context->debug.cpu)));
        return -1;
    }

    if (count <= 0) {
        error_setg(errp, "Could not decode an instruction at 0x%016" PRIx64,
                   pc);
        return -1;
    }
    if ((uint64_t)count > remaining) {
        error_setg(errp,
                   "Instruction at 0x%016" PRIx64
                   " exceeds the remaining byte budget",
                   pc);
        return -1;
    }
    if (count > WD40_DISASSEMBLY_MAX_INSN_BYTES) {
        error_setg(errp,
                   "Instruction at 0x%016" PRIx64
                   " has unsupported length %d",
                   pc, count);
        return -1;
    }

    g_strstrip(text->str);
    g_string_set_size(text, strlen(text->str));
    if (text->len == 0) {
        error_setg(errp,
                   "Disassembler returned empty text at 0x%016" PRIx64,
                   pc);
        return -1;
    }
    return count;
}

WD40Disassembly *
qmp_x_wd40_disassemble(WD40MemorySpace space, uint64_t address,
                       uint64_t instruction_count, bool has_max_bytes,
                       uint64_t max_bytes, bool has_cpu_index,
                       int64_t cpu_index, Error **errp)
{
    WD40DisassemblyContext context = { 0 };
    WD40Disassembly *result = NULL;
    WD40DisassembledInstructionList **tail;
    g_autoptr(GString) text = g_string_new(NULL);
    CPUState *cpu;
    uint64_t consumed = 0;
    uint64_t i;

    if (instruction_count == 0 ||
        instruction_count > WD40_DISASSEMBLY_MAX_INSTRUCTIONS) {
        error_setg(errp,
                   "instruction-count must be between 1 and %u",
                   WD40_DISASSEMBLY_MAX_INSTRUCTIONS);
        return NULL;
    }
    if (!has_max_bytes) {
        max_bytes =
            instruction_count * WD40_DISASSEMBLY_DEFAULT_BYTES_PER_INSN;
    }
    if (max_bytes == 0 || max_bytes > WD40_DISASSEMBLY_MAX_BYTES) {
        error_setg(errp, "max-bytes must be between 1 and %u",
                   WD40_DISASSEMBLY_MAX_BYTES);
        return NULL;
    }
    if (address > UINT64_MAX - (max_bytes - 1)) {
        error_setg(errp, "Disassembly byte range wraps past UINT64_MAX");
        return NULL;
    }
    if (runstate_is_running()) {
        error_setg(errp,
                   "The guest must be stopped before disassembly");
        return NULL;
    }

    cpu = wd40_disassembly_cpu(has_cpu_index, cpu_index);
    if (!cpu) {
        if (has_cpu_index) {
            error_setg(errp, "CPU index %" PRId64 " does not exist",
                       cpu_index);
        } else {
            error_setg(errp, "No realized CPU is available");
        }
        return NULL;
    }

    cpu_synchronize_state(cpu);
    disas_initialize_debug_target(&context.debug, cpu);
    context.space = space;
    context.first = address;
    context.last = address + max_bytes - 1;
    context.debug.info.read_memory_func = wd40_disassembly_read_memory;
    context.debug.info.fprintf_func = disas_gstring_printf;
    context.debug.info.show_opcodes = false;

    result = g_new0(WD40Disassembly, 1);
    result->space = space;
    result->cpu_index = cpu->cpu_index;
    result->address = address;
    result->instruction_count = instruction_count;
    tail = &result->instructions;

    for (i = 0; i < instruction_count; i++) {
        uint8_t bytes[WD40_DISASSEMBLY_MAX_INSN_BYTES];
        WD40DisassembledInstruction *instruction;
        WD40DisassembledInstructionList *entry;
        uint64_t pc = address + consumed;
        uint64_t remaining = max_bytes - consumed;
        int count;

        if (remaining == 0) {
            error_setg(errp,
                       "Byte budget exhausted after %" PRIu64
                       " complete instructions",
                       i);
            goto fail;
        }
        count = wd40_disassemble_one(&context, pc, remaining, text, errp);
        if (count < 0) {
            goto fail;
        }
        if (wd40_disassembly_read_memory(pc, bytes, count,
                                         &context.debug.info) != 0) {
            error_setg(errp,
                       "Could not read decoded instruction bytes at "
                       "0x%016" PRIx64,
                       pc);
            goto fail;
        }

        instruction = g_new0(WD40DisassembledInstruction, 1);
        instruction->address = pc;
        instruction->length = count;
        instruction->bytes =
            wd40_disassembly_bytes_to_hex(bytes, count);
        instruction->text = g_strdup(text->str);

        entry = g_new0(WD40DisassembledInstructionList, 1);
        entry->value = instruction;
        *tail = entry;
        tail = &entry->next;
        consumed += count;
    }

    result->bytes_consumed = consumed;
    return result;

fail:
    qapi_free_WD40Disassembly(result);
    return NULL;
}

/* Disassembler for the monitor.  */
void monitor_disas(Monitor *mon, CPUState *cpu, uint64_t pc,
                   int nb_insn, bool is_physical)
{
    int count, i;
    CPUDebug s;
    g_autoptr(GString) ds = g_string_new("");

    disas_initialize_debug_target(&s, cpu);
    s.info.fprintf_func = disas_gstring_printf;
    s.info.stream = (FILE *)ds;  /* abuse this slot */
    s.info.show_opcodes = true;

    if (is_physical) {
        s.info.read_memory_func = physical_read_memory;
    } else {
        s.info.read_memory_func = virtual_read_memory;
    }
    s.info.buffer_vma = pc;

    if (s.info.cap_arch >= 0 && cap_disas_monitor(&s.info, pc, nb_insn)) {
        monitor_puts(mon, ds->str);
        return;
    }

    if (!s.info.print_insn) {
        monitor_printf(mon, "0x%08" PRIx64
                       ": Asm output not supported on this arch\n", pc);
        return;
    }

    for (i = 0; i < nb_insn; i++) {
        g_string_append_printf(ds, "0x%08" PRIx64 ":  ", pc);
        count = s.info.print_insn(pc, &s.info);
        g_string_append_c(ds, '\n');
        if (count < 0) {
            break;
        }
        pc += count;
    }

    monitor_puts(mon, ds->str);
}
