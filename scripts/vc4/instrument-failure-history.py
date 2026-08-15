#!/usr/bin/env python3
"""Record bounded VC4 call/return and r0 history at bootcode failure."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def replace_one(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label}: found {count} anchors in {path}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_between(path: Path, start: str, end: str,
                    replacement: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    start_count = text.count(start)
    end_count = text.count(end)
    if start_count != 1 or end_count != 1:
        raise SystemExit(
            f"unexpected {label}: start={start_count} end={end_count} in {path}"
        )
    first = text.index(start)
    last = text.index(end, first)
    path.write_text(text[:first] + replacement + text[last:], encoding="utf-8")


def main() -> None:
    subprocess.run(
        [sys.executable, "scripts/vc4/instrument-low-pc-transfer.py"],
        check=True,
    )

    replace_one(
        Path("target/vc4/helper.h"),
        "DEF_HELPER_5(vc4_trace_target, i32, env, i32, i32, i32, i32)\n",
        "DEF_HELPER_5(vc4_trace_target, i32, env, i32, i32, i32, i32)\n"
        "DEF_HELPER_4(vc4_trace_r0, void, env, i32, i32, i32)\n",
        "r0 trace helper declaration",
    )
    replace_one(
        Path("target/vc4/helper.h"),
        "DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)\n",
        "DEF_HELPER_6(vc4_push_pop, void, env, i32, i32, i32, i32, i32)\n",
        "push/pop helper declaration",
    )

    replace_one(
        Path("target/vc4/translate.c"),
        '''static void vc4_set_reg(DisasContext *ctx, unsigned reg, TCGv_i32 value)
{
    if (reg < VC4_NUM_GPRS) {
        tcg_gen_mov_i32(cpu_gpr[reg], value);
''',
        '''static void vc4_set_reg(DisasContext *ctx, unsigned reg, TCGv_i32 value)
{
    if (reg < VC4_NUM_GPRS) {
        if (reg == 0) {
            gen_helper_vc4_trace_r0(
                tcg_env, tcg_constant_i32(ctx->pc), value,
                tcg_constant_i32(1));
        }
        tcg_gen_mov_i32(cpu_gpr[reg], value);
''',
        "dynamic r0 write",
    )
    replace_one(
        Path("target/vc4/translate.c"),
        '''static void vc4_set_reg_imm(DisasContext *ctx, unsigned reg, uint32_t value)
{
    if (reg < VC4_NUM_GPRS) {
        tcg_gen_movi_i32(cpu_gpr[reg], value);
''',
        '''static void vc4_set_reg_imm(DisasContext *ctx, unsigned reg, uint32_t value)
{
    if (reg < VC4_NUM_GPRS) {
        if (reg == 0) {
            gen_helper_vc4_trace_r0(
                tcg_env, tcg_constant_i32(ctx->pc),
                tcg_constant_i32(value), tcg_constant_i32(2));
        }
        tcg_gen_movi_i32(cpu_gpr[reg], value);
''',
        "immediate r0 write",
    )

    replace_one(
        Path("target/vc4/translate.c"),
        '''        gen_helper_vc4_push_pop(tcg_env,
                            tcg_constant_i32(push),
                            tcg_constant_i32(lrpc),
                            tcg_constant_i32(start),
                            tcg_constant_i32(count));
''',
        '''        gen_helper_vc4_push_pop(tcg_env,
                            tcg_constant_i32(push),
                            tcg_constant_i32(lrpc),
                            tcg_constant_i32(start),
                            tcg_constant_i32(count),
                            tcg_constant_i32(ctx->pc));
''',
        "push/pop source propagation",
    )

    replace_one(
        Path("target/vc4/translate.c"),
        '''    if ((i1 & 0xf080) == 0x9080) {
        raw = i2 | ((uint32_t)(i1 & 0x7f) << 16);
        raw |= (uint32_t)(i1 & 0xf00) << 15;
        offset = vc4_sext(raw, 0x04000000) * 2;
        tcg_gen_movi_i32(cpu_gpr[VC4_REG_LR], ctx->base.pc_next);
        vc4_gen_goto_tb(ctx, 0, ctx->pc + offset);
        return true;
    }
''',
        '''    if ((i1 & 0xf080) == 0x9080) {
        TCGv_i32 traced = tcg_temp_new_i32();
        uint32_t target;

        raw = i2 | ((uint32_t)(i1 & 0x7f) << 16);
        raw |= (uint32_t)(i1 & 0xf00) << 15;
        offset = vc4_sext(raw, 0x04000000) * 2;
        target = ctx->pc + offset;
        tcg_gen_movi_i32(cpu_gpr[VC4_REG_LR], ctx->base.pc_next);
        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc),
            tcg_constant_i32(target), tcg_constant_i32(13),
            tcg_constant_i32(raw));
        tcg_gen_mov_i32(cpu_pc, traced);
        tcg_gen_lookup_and_goto_ptr();
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    }
''',
        "direct BL transfer history",
    )

    history_implementation = r'''#define VC4_TRANSFER_HISTORY_CAPACITY 256
#define VC4_R0_HISTORY_CAPACITY       256

/* Temporary, single-VPU diagnostic state used only by the materialized trace. */
typedef struct VC4TransferHistoryEntry {
    uint32_t source;
    uint32_t target;
    uint32_t kind;
    uint32_t aux;
    uint32_t insn;
    uint32_t lr;
    uint32_t sr;
    uint32_t r0;
    uint32_t r1;
    uint32_t r2;
    uint32_t r3;
    uint32_t sp;
} VC4TransferHistoryEntry;

typedef struct VC4R0HistoryEntry {
    uint32_t source;
    uint32_t kind;
    uint32_t insn;
    uint32_t old_value;
    uint32_t new_value;
    uint32_t lr;
    uint32_t sr;
    uint32_t sp;
} VC4R0HistoryEntry;

static VC4TransferHistoryEntry
    vc4_transfer_history[VC4_TRANSFER_HISTORY_CAPACITY];
static VC4R0HistoryEntry vc4_r0_history[VC4_R0_HISTORY_CAPACITY];
static unsigned vc4_transfer_history_head;
static unsigned vc4_transfer_history_count;
static unsigned vc4_r0_history_head;
static unsigned vc4_r0_history_count;
static bool vc4_failure_history_dumped;

static uint32_t vc4_trace_insn(CPUArchState *envp, uint32_t source)
{
    return cpu_ldl_le_data(envp, source);
}

static void vc4_record_transfer(CPUArchState *envp, CPUVC4State *env,
                                uint32_t source, uint32_t target,
                                uint32_t kind, uint32_t aux)
{
    VC4TransferHistoryEntry *entry =
        &vc4_transfer_history[vc4_transfer_history_head];

    *entry = (VC4TransferHistoryEntry) {
        .source = source,
        .target = target,
        .kind = kind,
        .aux = aux,
        .insn = vc4_trace_insn(envp, source),
        .lr = env->gpr[VC4_REG_LR],
        .sr = env->sr,
        .r0 = env->gpr[0],
        .r1 = env->gpr[1],
        .r2 = env->gpr[2],
        .r3 = env->gpr[3],
        .sp = env->gpr[VC4_REG_SP],
    };
    vc4_transfer_history_head =
        (vc4_transfer_history_head + 1) % VC4_TRANSFER_HISTORY_CAPACITY;
    if (vc4_transfer_history_count < VC4_TRANSFER_HISTORY_CAPACITY) {
        vc4_transfer_history_count++;
    }
}

static void vc4_record_r0(CPUArchState *envp, CPUVC4State *env,
                          uint32_t source, uint32_t new_value,
                          uint32_t kind)
{
    VC4R0HistoryEntry *entry = &vc4_r0_history[vc4_r0_history_head];

    *entry = (VC4R0HistoryEntry) {
        .source = source,
        .kind = kind,
        .insn = vc4_trace_insn(envp, source),
        .old_value = env->gpr[0],
        .new_value = new_value,
        .lr = env->gpr[VC4_REG_LR],
        .sr = env->sr,
        .sp = env->gpr[VC4_REG_SP],
    };
    vc4_r0_history_head =
        (vc4_r0_history_head + 1) % VC4_R0_HISTORY_CAPACITY;
    if (vc4_r0_history_count < VC4_R0_HISTORY_CAPACITY) {
        vc4_r0_history_count++;
    }
}

static void vc4_dump_failure_history(void)
{
    unsigned i;

    qemu_log_mask(LOG_GUEST_ERROR,
                  "VC4_FAILURE_HISTORY_BEGIN transfers=%u r0_writes=%u\n",
                  vc4_transfer_history_count, vc4_r0_history_count);

    for (i = 0; i < vc4_transfer_history_count; i++) {
        unsigned index =
            (vc4_transfer_history_head + VC4_TRANSFER_HISTORY_CAPACITY -
             vc4_transfer_history_count + i) %
            VC4_TRANSFER_HISTORY_CAPACITY;
        const VC4TransferHistoryEntry *entry = &vc4_transfer_history[index];

        qemu_log_mask(
            LOG_GUEST_ERROR,
            "VC4_FAILURE_TRANSFER ordinal=%u source=0x%08x "
            "target=0x%08x kind=%u aux=0x%08x insn=0x%08x "
            "lr=0x%08x sr=0x%08x r0=0x%08x r1=0x%08x "
            "r2=0x%08x r3=0x%08x sp=0x%08x\n",
            i, entry->source, entry->target, entry->kind, entry->aux,
            entry->insn, entry->lr, entry->sr, entry->r0, entry->r1,
            entry->r2, entry->r3, entry->sp);
    }

    for (i = 0; i < vc4_r0_history_count; i++) {
        unsigned index =
            (vc4_r0_history_head + VC4_R0_HISTORY_CAPACITY -
             vc4_r0_history_count + i) % VC4_R0_HISTORY_CAPACITY;
        const VC4R0HistoryEntry *entry = &vc4_r0_history[index];

        qemu_log_mask(
            LOG_GUEST_ERROR,
            "VC4_FAILURE_R0 ordinal=%u source=0x%08x kind=%u "
            "insn=0x%08x old=0x%08x new=0x%08x lr=0x%08x "
            "sr=0x%08x sp=0x%08x\n",
            i, entry->source, entry->kind, entry->insn,
            entry->old_value, entry->new_value, entry->lr,
            entry->sr, entry->sp);
    }

    qemu_log_mask(LOG_GUEST_ERROR, "VC4_FAILURE_HISTORY_END\n");
}

static void vc4_trace_low_target(CPUArchState *envp,
                                 CPUVC4State *env,
                                 uint32_t source,
                                 uint32_t target,
                                 uint32_t kind,
                                 uint32_t aux)
{
    uint32_t insn;

    if (source < 0x200 || target >= 0x200) {
        return;
    }

    insn = vc4_trace_insn(envp, source);
    qemu_log_mask(LOG_GUEST_ERROR,
                  "VC4_LOW_TARGET source=0x%08x target=0x%08x "
                  "kind=%u aux=0x%08x insn=0x%08x "
                  "lr=0x%08x sr=0x%08x "
                  "r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x "
                  "sp=0x%08x\n",
                  source, target, kind, aux, insn,
                  env->gpr[VC4_REG_LR], env->sr,
                  env->gpr[0], env->gpr[1], env->gpr[2], env->gpr[3],
                  env->gpr[VC4_REG_SP]);
}

uint32_t helper_vc4_trace_target(CPUArchState *envp,
                                 uint32_t source,
                                 uint32_t target,
                                 uint32_t kind,
                                 uint32_t aux)
{
    CPUVC4State *env = vc4_helper_env(envp);

    vc4_record_transfer(envp, env, source, target, kind, aux);
    vc4_trace_low_target(envp, env, source, target, kind, aux);
    if (!vc4_failure_history_dumped && source == 0x0000166c &&
        target == 0 && kind == 10) {
        vc4_failure_history_dumped = true;
        vc4_dump_failure_history();
    }
    return target;
}

void helper_vc4_trace_r0(CPUArchState *envp, uint32_t source,
                         uint32_t value, uint32_t kind)
{
    vc4_record_r0(envp, vc4_helper_env(envp), source, value, kind);
}

'''
    replace_between(
        Path("target/vc4/op_helper.c"),
        "static void vc4_trace_low_target(",
        "static void vc4_push(",
        history_implementation,
        "trace helper implementation",
    )

    replace_one(
        Path("target/vc4/op_helper.c"),
        '''static void vc4_pop(CPUArchState *envp, CPUVC4State *env, unsigned reg)
{
    uint32_t sp = env->gpr[VC4_REG_SP];
    uint32_t value = cpu_ldl_le_data(envp, sp);

    vc4_env_set_reg(env, reg, value);
    env->gpr[VC4_REG_SP] = sp + 4;
}
''',
        '''static void vc4_pop(CPUArchState *envp, CPUVC4State *env,
                    unsigned reg, uint32_t source)
{
    uint32_t sp = env->gpr[VC4_REG_SP];
    uint32_t value = cpu_ldl_le_data(envp, sp);

    if (reg == 0) {
        vc4_record_r0(envp, env, source, value, 3);
    }
    vc4_env_set_reg(env, reg, value);
    env->gpr[VC4_REG_SP] = sp + 4;
}
''',
        "pop r0 tracing",
    )
    replace_one(
        Path("target/vc4/op_helper.c"),
        '''void helper_vc4_push_pop(CPUArchState *envp, uint32_t push, uint32_t lrpc,
                         uint32_t start, uint32_t count)
''',
        '''void helper_vc4_push_pop(CPUArchState *envp, uint32_t push, uint32_t lrpc,
                         uint32_t start, uint32_t count, uint32_t source)
''',
        "push/pop source argument",
    )
    replace_one(
        Path("target/vc4/op_helper.c"),
        "                vc4_pop(envp, env, start);\n",
        "                vc4_pop(envp, env, start, source);\n",
        "compact pop source",
    )
    replace_one(
        Path("target/vc4/op_helper.c"),
        "                vc4_pop(envp, env, (start + i) % VC4_NUM_REGS);\n",
        "                vc4_pop(envp, env, (start + i) % VC4_NUM_REGS,\n"
        "                        source);\n",
        "register pop source",
    )
    replace_one(
        Path("target/vc4/op_helper.c"),
        "            vc4_pop(envp, env, VC4_REG_PC);\n",
        "            vc4_pop(envp, env, VC4_REG_PC, source);\n",
        "PC pop source",
    )

    print("instrumented bounded VC4 failure history")


if __name__ == "__main__":
    main()
