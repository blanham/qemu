#!/usr/bin/env python3
"""Trace the leaf routine that returns -1 from official bootcode.bin."""

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
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(f"unexpected {label} anchors in {path}")
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
        "DEF_HELPER_4(vc4_trace_leaf_reg, void, env, i32, i32, i32)\n"
        "DEF_HELPER_5(vc4_trace_leaf_mem, void, env, i32, i32, i32, i32)\n"
        "DEF_HELPER_6(vc4_trace_leaf_branch, void, env, i32, i32, i32, i32, i32)\n",
        "leaf trace helper declarations",
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
        if (reg == 9) {
            gen_helper_vc4_trace_leaf_reg(
                tcg_env, tcg_constant_i32(ctx->pc), value,
                tcg_constant_i32(1));
        }
        tcg_gen_mov_i32(cpu_gpr[reg], value);
''',
        "dynamic r9 write",
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
        if (reg == 9) {
            gen_helper_vc4_trace_leaf_reg(
                tcg_env, tcg_constant_i32(ctx->pc),
                tcg_constant_i32(value), tcg_constant_i32(2));
        }
        tcg_gen_movi_i32(cpu_gpr[reg], value);
''',
        "immediate r9 write",
    )

    replace_one(
        Path("target/vc4/translate.c"),
        '''    skip = vc4_gen_skip_if_false(cond);
    if (store) {
        vc4_gen_qemu_st_i32(vc4_get_reg(ctx, rd), address, 0,
                            vc4_store_mop(format));
    } else {
        vc4_gen_qemu_ld_i32(value, address, 0, vc4_load_mop(format));
        vc4_set_reg(ctx, rd, value);
    }
''',
        '''    skip = vc4_gen_skip_if_false(cond);
    if (store) {
        tcg_gen_mov_i32(value, vc4_get_reg(ctx, rd));
        gen_helper_vc4_trace_leaf_mem(
            tcg_env, tcg_constant_i32(ctx->pc), address, value,
            tcg_constant_i32(0x80000000u | (format << 16) | rd));
        vc4_gen_qemu_st_i32(value, address, 0, vc4_store_mop(format));
    } else {
        vc4_gen_qemu_ld_i32(value, address, 0, vc4_load_mop(format));
        gen_helper_vc4_trace_leaf_mem(
            tcg_env, tcg_constant_i32(ctx->pc), address, value,
            tcg_constant_i32((format << 16) | rd));
        vc4_set_reg(ctx, rd, value);
    }
''',
        "address-form leaf memory trace",
    )
    replace_one(
        Path("target/vc4/translate.c"),
        '''    if (store) {
        vc4_gen_qemu_st_i32(vc4_get_reg(ctx, rd), address, 0,
                            vc4_store_mop(format));
    } else {
        vc4_gen_qemu_ld_i32(value, address, 0, vc4_load_mop(format));
        vc4_set_reg(ctx, rd, value);
    }

    if (postincrement) {
''',
        '''    if (store) {
        tcg_gen_mov_i32(value, vc4_get_reg(ctx, rd));
        gen_helper_vc4_trace_leaf_mem(
            tcg_env, tcg_constant_i32(ctx->pc), address, value,
            tcg_constant_i32(0x80000000u | (format << 16) | rd));
        vc4_gen_qemu_st_i32(value, address, 0, vc4_store_mop(format));
    } else {
        vc4_gen_qemu_ld_i32(value, address, 0, vc4_load_mop(format));
        gen_helper_vc4_trace_leaf_mem(
            tcg_env, tcg_constant_i32(ctx->pc), address, value,
            tcg_constant_i32((format << 16) | rd));
        vc4_set_reg(ctx, rd, value);
    }

    if (postincrement) {
''',
        "offset-form leaf memory trace",
    )

    replace_one(
        Path("target/vc4/translate.c"),
        '''    predicate = vc4_gen_cond_from_sr(cpu_sr, cond);
    not_taken = gen_new_label();

    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
''',
        '''    predicate = vc4_gen_cond_from_sr(cpu_sr, cond);
    not_taken = gen_new_label();

    gen_helper_vc4_trace_leaf_branch(
        tcg_env, tcg_constant_i32(ctx->pc), tcg_constant_i32(dest),
        tcg_constant_i32(next), tcg_constant_i32(cond), predicate);
    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
''',
        "status conditional branch trace",
    )
    replace_one(
        Path("target/vc4/translate.c"),
        '''    predicate = vc4_gen_cond_from_sr(flags, cond);
    not_taken = gen_new_label();

    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
''',
        '''    predicate = vc4_gen_cond_from_sr(flags, cond);
    not_taken = gen_new_label();

    gen_helper_vc4_trace_leaf_branch(
        tcg_env, tcg_constant_i32(ctx->pc), tcg_constant_i32(dest),
        tcg_constant_i32(next), tcg_constant_i32(cond), predicate);
    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
''',
        "compare conditional branch trace",
    )

    implementation = r'''#define VC4_LEAF_START 0x000014fcu
#define VC4_LEAF_END   0x0000166eu
#define VC4_LEAF_REG_CAPACITY     8
#define VC4_LEAF_BRANCH_CAPACITY 12
#define VC4_LEAF_MEM_CAPACITY    16

typedef struct VC4LeafRegEvent {
    uint32_t source;
    uint32_t kind;
    uint32_t old_value;
    uint32_t new_value;
    uint32_t sr;
} VC4LeafRegEvent;

typedef struct VC4LeafBranchEvent {
    uint32_t source;
    uint32_t dest;
    uint32_t next;
    uint32_t cond;
    uint32_t taken;
    uint32_t sr;
} VC4LeafBranchEvent;

typedef struct VC4LeafMemEvent {
    uint32_t source;
    uint32_t address;
    uint32_t value;
    uint32_t meta;
} VC4LeafMemEvent;

static VC4LeafRegEvent vc4_leaf_regs[VC4_LEAF_REG_CAPACITY];
static VC4LeafBranchEvent vc4_leaf_branches[VC4_LEAF_BRANCH_CAPACITY];
static VC4LeafMemEvent vc4_leaf_mem[VC4_LEAF_MEM_CAPACITY];
static unsigned vc4_leaf_reg_head, vc4_leaf_reg_count;
static unsigned vc4_leaf_branch_head, vc4_leaf_branch_count;
static unsigned vc4_leaf_mem_head, vc4_leaf_mem_count;
static bool vc4_leaf_dumped;

static bool vc4_in_failure_leaf(uint32_t source)
{
    return source >= VC4_LEAF_START && source < VC4_LEAF_END;
}

void helper_vc4_trace_leaf_reg(CPUArchState *envp, uint32_t source,
                               uint32_t value, uint32_t kind)
{
    CPUVC4State *env = vc4_helper_env(envp);
    VC4LeafRegEvent *event;

    if (!vc4_in_failure_leaf(source)) {
        return;
    }
    event = &vc4_leaf_regs[vc4_leaf_reg_head];
    *event = (VC4LeafRegEvent) {
        .source = source,
        .kind = kind,
        .old_value = env->gpr[9],
        .new_value = value,
        .sr = env->sr,
    };
    vc4_leaf_reg_head = (vc4_leaf_reg_head + 1) % VC4_LEAF_REG_CAPACITY;
    if (vc4_leaf_reg_count < VC4_LEAF_REG_CAPACITY) {
        vc4_leaf_reg_count++;
    }
}

void helper_vc4_trace_leaf_mem(CPUArchState *envp, uint32_t source,
                               uint32_t address, uint32_t value,
                               uint32_t meta)
{
    VC4LeafMemEvent *event;

    if (!vc4_in_failure_leaf(source)) {
        return;
    }
    event = &vc4_leaf_mem[vc4_leaf_mem_head];
    *event = (VC4LeafMemEvent) {
        .source = source,
        .address = address,
        .value = value,
        .meta = meta,
    };
    vc4_leaf_mem_head = (vc4_leaf_mem_head + 1) % VC4_LEAF_MEM_CAPACITY;
    if (vc4_leaf_mem_count < VC4_LEAF_MEM_CAPACITY) {
        vc4_leaf_mem_count++;
    }
}

void helper_vc4_trace_leaf_branch(CPUArchState *envp, uint32_t source,
                                  uint32_t dest, uint32_t next,
                                  uint32_t cond, uint32_t taken)
{
    CPUVC4State *env = vc4_helper_env(envp);
    VC4LeafBranchEvent *event;

    if (!vc4_in_failure_leaf(source)) {
        return;
    }
    event = &vc4_leaf_branches[vc4_leaf_branch_head];
    *event = (VC4LeafBranchEvent) {
        .source = source,
        .dest = dest,
        .next = next,
        .cond = cond,
        .taken = taken != 0,
        .sr = env->sr,
    };
    vc4_leaf_branch_head =
        (vc4_leaf_branch_head + 1) % VC4_LEAF_BRANCH_CAPACITY;
    if (vc4_leaf_branch_count < VC4_LEAF_BRANCH_CAPACITY) {
        vc4_leaf_branch_count++;
    }
}

static void vc4_dump_leaf_failure(void)
{
    unsigned i;

    qemu_log_mask(LOG_GUEST_ERROR,
                  "VC4_LEAF_FAILURE_BEGIN regs=%u branches=%u mem=%u\n",
                  vc4_leaf_reg_count, vc4_leaf_branch_count,
                  vc4_leaf_mem_count);
    for (i = 0; i < vc4_leaf_reg_count; i++) {
        unsigned index =
            (vc4_leaf_reg_head + VC4_LEAF_REG_CAPACITY -
             vc4_leaf_reg_count + i) % VC4_LEAF_REG_CAPACITY;
        const VC4LeafRegEvent *event = &vc4_leaf_regs[index];
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VC4_LEAF_R9 ordinal=%u source=0x%08x kind=%u "
                      "old=0x%08x new=0x%08x sr=0x%08x\n",
                      i, event->source, event->kind, event->old_value,
                      event->new_value, event->sr);
    }
    for (i = 0; i < vc4_leaf_branch_count; i++) {
        unsigned index =
            (vc4_leaf_branch_head + VC4_LEAF_BRANCH_CAPACITY -
             vc4_leaf_branch_count + i) % VC4_LEAF_BRANCH_CAPACITY;
        const VC4LeafBranchEvent *event = &vc4_leaf_branches[index];
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VC4_LEAF_BRANCH ordinal=%u source=0x%08x "
                      "dest=0x%08x next=0x%08x cond=%u taken=%u "
                      "sr=0x%08x\n",
                      i, event->source, event->dest, event->next,
                      event->cond, event->taken, event->sr);
    }
    for (i = 0; i < vc4_leaf_mem_count; i++) {
        unsigned index =
            (vc4_leaf_mem_head + VC4_LEAF_MEM_CAPACITY -
             vc4_leaf_mem_count + i) % VC4_LEAF_MEM_CAPACITY;
        const VC4LeafMemEvent *event = &vc4_leaf_mem[index];
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VC4_LEAF_MEM ordinal=%u source=0x%08x "
                      "address=0x%08x value=0x%08x store=%u "
                      "format=%u reg=%u\n",
                      i, event->source, event->address, event->value,
                      event->meta >> 31, (event->meta >> 16) & 3,
                      event->meta & 0x1f);
    }
    qemu_log_mask(LOG_GUEST_ERROR, "VC4_LEAF_FAILURE_END\n");
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
    insn = cpu_ldl_le_data(envp, source);
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

    vc4_trace_low_target(envp, env, source, target, kind, aux);
    if (!vc4_leaf_dumped && source == 0x0000166c && target == 0 && kind == 10) {
        vc4_leaf_dumped = true;
        vc4_dump_leaf_failure();
    }
    return target;
}

'''
    replace_between(
        Path("target/vc4/op_helper.c"),
        "static void vc4_trace_low_target(",
        "static void vc4_push(",
        implementation,
        "leaf trace implementation",
    )

    print("instrumented the bootcode failure leaf")


if __name__ == "__main__":
    main()
