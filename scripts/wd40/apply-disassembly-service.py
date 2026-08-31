#!/usr/bin/env python3
"""Expose bounded structured guest disassembly through QMP."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def ensure_include(path: str, include: str, anchor: str) -> None:
    file_path, text = load(path)
    count = text.count(include)
    if count == 1:
        return
    if count != 0:
        raise RuntimeError(
            f"{path}: expected at most one {include!r}, found {count}"
        )
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(
            f"{path}: expected one include insertion site, "
            f"found {anchor_count}"
        )
    store(file_path, text.replace(anchor, include + anchor, 1))


def insert_before_once(
    path: str,
    anchor: str,
    block: str,
    *,
    owned_markers: tuple[str, ...],
) -> None:
    file_path, text = load(path)
    marker_counts = [text.count(marker) for marker in owned_markers]
    if all(count == 1 for count in marker_counts):
        return
    if any(marker_counts):
        raise RuntimeError(
            f"{path}: partially applied disassembly block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected one insertion site, found {count}")
    store(file_path, text.replace(anchor, block + anchor, 1))


def insert_after_once(
    path: str,
    anchor: str,
    block: str,
    *,
    owned_marker: str,
) -> None:
    file_path, text = load(path)
    marker_count = text.count(owned_marker)
    if marker_count == 1:
        return
    if marker_count != 0:
        raise RuntimeError(
            f"{path}: expected at most one {owned_marker!r}, "
            f"found {marker_count}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected one insertion site, found {count}")
    store(file_path, text.replace(anchor, anchor + block, 1))


def main() -> None:
    insert_before_once(
        "qapi/machine.json",
        """##
# @memsave:
""",
        """##
# @WD40DisassembledInstruction:
#
# One decoded guest instruction.
#
# @address: guest address of the instruction
#
# @length: instruction length in bytes
#
# @bytes: lowercase hexadecimal encoding of the exact instruction
#     bytes
#
# @text: architecture disassembler output without an address or byte
#     dump
#
# Since: 11.2
##
{ 'struct': 'WD40DisassembledInstruction',
  'data': { 'address': 'uint64', 'length': 'uint64',
            'bytes': 'str', 'text': 'str' } }

##
# @WD40Disassembly:
#
# Bounded structured disassembly from one virtual CPU's target mode.
#
# @space: guest address space used to fetch instruction bytes
#
# @cpu-index: virtual CPU whose target mode and address space were
#     used
#
# @address: first guest address decoded
#
# @instruction-count: number of instructions returned
#
# @bytes-consumed: total number of instruction bytes returned
#
# @instructions: decoded instructions in ascending address order
#
# Since: 11.2
##
{ 'struct': 'WD40Disassembly',
  'data': { 'space': 'WD40MemorySpace', 'cpu-index': 'int',
            'address': 'uint64', 'instruction-count': 'uint64',
            'bytes-consumed': 'uint64',
            'instructions': ['WD40DisassembledInstruction'] } }

##
# @x-wd40-disassemble:
#
# Decode between 1 and 256 instructions from guest virtual or physical
# memory.  The guest must be stopped.  The byte budget defaults to 32
# bytes per requested instruction and may be set between 1 byte and
# 64 KiB.
#
# @space: guest address space used to fetch instruction bytes
#
# @address: first guest address to decode
#
# @instruction-count: number of complete instructions to return
#
# @max-bytes: maximum bytes the decoder may read or return
#
# @cpu-index: virtual CPU supplying target mode and physical address
#     space.  It defaults to CPU 0.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: exact bytes and text for each complete decoded instruction
#
# Since: 11.2
##
{ 'command': 'x-wd40-disassemble',
  'data': { 'space': 'WD40MemorySpace', 'address': 'uint64',
            'instruction-count': 'uint64', '*max-bytes': 'uint64',
            '*cpu-index': 'int' },
  'returns': 'WD40Disassembly',
  'features': [ 'unstable' ] }

""",
        owned_markers=(
            "'struct': 'WD40DisassembledInstruction'",
            "'struct': 'WD40Disassembly'",
            "'command': 'x-wd40-disassemble'",
        ),
    )

    insert_after_once(
        "include/disas/dis-asm.h",
        """bool cap_disas_plugin(disassemble_info *info, uint64_t pc, size_t size);
""",
        """int cap_disas_one(disassemble_info *info, uint64_t pc);
""",
        owned_marker="int cap_disas_one(disassemble_info *info",
    )
    insert_after_once(
        "include/disas/dis-asm.h",
        """# define cap_disas_plugin(i, p, c)  false
""",
        """# define cap_disas_one(i, p)        (-1)
""",
        owned_marker="# define cap_disas_one(i, p)",
    )

    insert_before_once(
        "disas/capstone.c",
        """/* Disassemble COUNT insns at PC for the target.  */
""",
        r"""/*
 * Decode one instruction without formatting an address or opcode dump.
 *
 * INFO->buffer_length is the caller's remaining byte budget.  Read in
 * small boundary-limited pieces so a complete instruction immediately
 * before an inaccessible page does not require the following page.
 */
int cap_disas_one(disassemble_info *info, uint64_t pc)
{
    uint8_t cap_buf[32];
    csh handle;
    size_t limit;
    size_t csize = 0;
    int result = -EINVAL;

    if (info->buffer_length <= 0) {
        return -EIO;
    }
    limit = MIN(sizeof(cap_buf), (size_t)info->buffer_length);
    if (cap_disas_start(info, &handle) != CS_ERR_OK) {
        return -ENOSYS;
    }

    while (csize < limit) {
        size_t boundary = 1024 - ((pc + csize) & 1023);
        size_t tsize = MIN(limit - csize, boundary);
        const uint8_t *cbuf;
        size_t available;
        uint64_t next_pc;

        if (info->read_memory_func(pc + csize, cap_buf + csize,
                                   tsize, info) != 0) {
            result = -EIO;
            break;
        }
        csize += tsize;
        cbuf = cap_buf;
        available = csize;
        next_pc = pc;
        if (cs_disasm_iter(handle, &cbuf, &available,
                           &next_pc, cap_insn)) {
            info->fprintf_func(info->stream, "%s%s%s",
                               cap_insn->mnemonic,
                               cap_insn->op_str[0] ? " " : "",
                               cap_insn->op_str);
            result = cap_insn->size;
            break;
        }
    }

    cs_close(&handle);
    return result;
}

""",
        owned_markers=(
            "int cap_disas_one(disassemble_info *info",
            "Decode one instruction without formatting an address",
        ),
    )

    for include in (
        '#include "qapi/error.h"\n',
        '#include "qapi/qapi-commands-machine.h"\n',
        '#include "system/hw_accel.h"\n',
        '#include "system/runstate.h"\n',
    ):
        ensure_include(
            "disas/disas-mon.c",
            include,
            '#include "monitor/monitor.h"\n',
        )

    insert_before_once(
        "disas/disas-mon.c",
        """/* Disassembler for the monitor.  */
""",
        r"""#define WD40_DISASSEMBLY_MAX_INSTRUCTIONS 256U
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

""",
        owned_markers=(
            "#define WD40_DISASSEMBLY_MAX_INSTRUCTIONS",
            "typedef struct WD40DisassemblyContext",
            "qmp_x_wd40_disassemble",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Structured log-category control
-------------------------------
""",
        """Bounded structured disassembly
-------------------------------

``x-wd40-disassemble`` decodes a caller-bounded sequence of complete guest
instructions and returns one typed record per instruction: address, exact raw
bytes, decoded length, and architecture disassembler text.  TTYphoon therefore
does not have to scrape addresses and opcode columns from ``x/NI`` output.

The selected CPU supplies the target's current instruction mode and, for
physical reads, its address space.  Virtual reads use that CPU's debug-memory
translation.  The guest must be stopped so the mode, translation state, and
instruction bytes cannot race normal execution while a multi-instruction
result is assembled.

Clients request between 1 and 256 instructions and may impose a byte budget
between 1 byte and 64 KiB.  The default budget is 32 bytes per requested
instruction.  A memory fault, unsupported target disassembler, incomplete
instruction, or exhausted budget fails the command rather than returning a
silently truncated list.  Successful results report the total bytes consumed,
which may be smaller than the read budget.

The service uses QEMU's target disassembler callbacks directly.  Capstone
builds use a single-instruction adapter that returns the decoded length instead
of reparsing Capstone's monitor-formatted text.  Builds without Capstone retain
support for targets with an in-tree ``print_insn`` decoder and report a typed
error for targets that have no available decoder.

""",
        owned_markers=(
            "Bounded structured disassembly",
            "x-wd40-disassemble",
            "single-instruction adapter",
        ),
    )


if __name__ == "__main__":
    main()
