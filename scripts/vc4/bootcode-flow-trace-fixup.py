#!/usr/bin/env python3
"""Materialize a bounded bootcode retry-flow trace in the VC4 translator.

This transformer is used only by the diagnostic workflow.  It records the
pre-instruction state for a few completed delay-helper generations whose return
addresses are the three callers observed in production bootcode.bin.  The tight
delay loop itself is suppressed; after each return, the next bounded instruction
window is logged so the retry condition can be identified without speculative
hardware changes.

The generated target changes must never be committed.

SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "target/vc4/helper.h"
OP_HELPER = ROOT / "target/vc4/op_helper.c"
TRANSLATE = ROOT / "target/vc4/translate.c"
MARKER = "VC4_BOOT_FLOW_TRACE"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label}: found {count} anchors")
    return text.replace(old, new, 1)


def already_materialized() -> bool:
    states = [
        MARKER in path.read_text(encoding="utf-8")
        for path in (HELPER, OP_HELPER, TRANSLATE)
    ]
    if any(states) and not all(states):
        raise SystemExit("partial VC4 boot-flow trace materialization")
    return all(states)


def update_helper_declaration() -> None:
    text = HELPER.read_text(encoding="utf-8")
    anchor = "DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)\n"
    declaration = (
        "/* VC4_BOOT_FLOW_TRACE: workflow-only bounded firmware trace. */\n"
        "DEF_HELPER_5(vc4_boot_flow, void, env, i32, i32, i32, i32)\n"
    )
    text = replace_once(text, anchor, declaration + anchor,
                        "boot-flow helper declaration")
    HELPER.write_text(text, encoding="utf-8")


def update_helper_implementation() -> None:
    text = OP_HELPER.read_text(encoding="utf-8")
    anchor = """static uint32_t vc4_bitreverse(uint32_t value)
{
"""
    implementation = r'''/* VC4_BOOT_FLOW_TRACE: workflow-only bounded firmware trace. */
#define VC4_BOOT_FLOW_RECORD_LIMIT 2048
#define VC4_BOOT_FLOW_FOLLOW_INSNS 96
#define VC4_BOOT_FLOW_GENERATIONS 4

static unsigned vc4_boot_flow_seq;
static unsigned vc4_boot_flow_generations[3];
static bool vc4_boot_flow_in_delay;
static bool vc4_boot_flow_active;
static uint32_t vc4_boot_flow_caller;
static unsigned vc4_boot_flow_generation;
static unsigned vc4_boot_flow_follow;

static int vc4_boot_flow_caller_slot(uint32_t caller)
{
    switch (caller & ~1u) {
    case 0x596:
        return 0;
    case 0x5a2:
        return 1;
    case 0x5b0:
        return 2;
    default:
        return -1;
    }
}

static void vc4_boot_flow_log(CPUVC4State *env, const char *event,
                              uint32_t pc, uint32_t i1, uint32_t i2,
                              uint32_t i3, uint32_t length)
{
    if (vc4_boot_flow_seq >= VC4_BOOT_FLOW_RECORD_LIMIT) {
        return;
    }

    qemu_log_mask(
        LOG_GUEST_ERROR,
        "VC4_BOOT_FLOW event=%s seq=%u generation=%u caller=0x%08x "
        "pc=0x%08x insn=%04x:%04x:%04x len=%u sr=0x%08x "
        "r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x "
        "r4=0x%08x r5=0x%08x r6=0x%08x r7=0x%08x "
        "r8=0x%08x r12=0x%08x r13=0x%08x r14=0x%08x "
        "r15=0x%08x r16=0x%08x r17=0x%08x r24=0x%08x "
        "sp=0x%08x lr=0x%08x\n",
        event, vc4_boot_flow_seq, vc4_boot_flow_generation,
        vc4_boot_flow_caller, pc, i1 & 0xffff, i2 & 0xffff,
        i3 & 0xffff, length, env->sr,
        env->gpr[0], env->gpr[1], env->gpr[2], env->gpr[3],
        env->gpr[4], env->gpr[5], env->gpr[6], env->gpr[7],
        env->gpr[8], env->gpr[12], env->gpr[13], env->gpr[14],
        env->gpr[15], env->gpr[16], env->gpr[17], env->gpr[24],
        env->gpr[VC4_REG_SP], env->gpr[VC4_REG_LR]);
    vc4_boot_flow_seq++;
}

void helper_vc4_boot_flow(CPUArchState *envp, uint32_t pc,
                          uint32_t i1, uint32_t i2, uint32_t meta)
{
    CPUVC4State *env = vc4_helper_env(envp);
    uint32_t i3 = meta & 0xffff;
    uint32_t length = (meta >> 16) & 0xff;
    uint32_t caller = env->gpr[VC4_REG_LR] & ~1u;
    bool delay_helper = pc >= 0x540 && pc < 0x560;
    int slot;

    if (vc4_boot_flow_seq >= VC4_BOOT_FLOW_RECORD_LIMIT) {
        return;
    }

    if (delay_helper) {
        if (!vc4_boot_flow_in_delay) {
            slot = vc4_boot_flow_caller_slot(caller);
            vc4_boot_flow_in_delay = true;
            vc4_boot_flow_follow = 0;
            vc4_boot_flow_caller = caller;
            vc4_boot_flow_active =
                slot >= 0 &&
                vc4_boot_flow_generations[slot] <
                    VC4_BOOT_FLOW_GENERATIONS;
            if (vc4_boot_flow_active) {
                vc4_boot_flow_generation =
                    ++vc4_boot_flow_generations[slot];
                vc4_boot_flow_log(env, "delay-enter", pc, i1, i2,
                                  i3, length);
            }
        }
        return;
    }

    if (vc4_boot_flow_in_delay) {
        vc4_boot_flow_in_delay = false;
        if (vc4_boot_flow_active) {
            vc4_boot_flow_log(env, "delay-exit", pc, i1, i2,
                              i3, length);
            vc4_boot_flow_follow = VC4_BOOT_FLOW_FOLLOW_INSNS;
        }
    }

    if (vc4_boot_flow_active && vc4_boot_flow_follow) {
        vc4_boot_flow_log(env, "step", pc, i1, i2, i3, length);
        vc4_boot_flow_follow--;
        if (!vc4_boot_flow_follow) {
            vc4_boot_flow_active = false;
        }
    }
}

'''
    text = replace_once(text, anchor, implementation + anchor,
                        "boot-flow helper implementation anchor")
    OP_HELPER.write_text(text, encoding="utf-8")


def update_translator() -> None:
    text = TRANSLATE.read_text(encoding="utf-8")
    declaration = "    uint16_t i1, i2, i3, i4, i5;\n"
    initialized = "    uint16_t i1, i2 = 0, i3 = 0, i4, i5;\n"
    text = replace_once(text, declaration, initialized,
                        "translator halfword declaration")

    anchor = """static void vc4_tr_init_disas_context(DisasContextBase *dcbase, CPUState *cs)
{
"""
    generator = r'''/* VC4_BOOT_FLOW_TRACE: workflow-only bounded firmware trace. */
static void vc4_gen_boot_flow(DisasContext *ctx, uint16_t i1,
                              uint16_t i2, uint16_t i3,
                              unsigned length)
{
    uint32_t meta = i3 | (length << 16);

    gen_helper_vc4_boot_flow(tcg_env, tcg_constant_i32(ctx->pc),
                             tcg_constant_i32(i1),
                             tcg_constant_i32(i2),
                             tcg_constant_i32(meta));
}

'''
    text = replace_once(text, anchor, generator + anchor,
                        "translator trace generator anchor")

    replacements = [
        (
            """        ctx->base.pc_next = ctx->pc + 2;
        decoded = vc4_decode_scalar16(ctx, i1);
""",
            """        ctx->base.pc_next = ctx->pc + 2;
        vc4_gen_boot_flow(ctx, i1, 0, 0, 2);
        decoded = vc4_decode_scalar16(ctx, i1);
""",
            "scalar16 trace",
        ),
        (
            """        ctx->base.pc_next = ctx->pc + 4;
        decoded = vc4_decode_scalar32(ctx, i1, i2);
""",
            """        ctx->base.pc_next = ctx->pc + 4;
        vc4_gen_boot_flow(ctx, i1, i2, 0, 4);
        decoded = vc4_decode_scalar32(ctx, i1, i2);
""",
            "scalar32 trace",
        ),
        (
            """        ctx->base.pc_next = ctx->pc + 6;
        decoded = vc4_decode_scalar48(ctx, i1, i2, i3);
""",
            """        ctx->base.pc_next = ctx->pc + 6;
        vc4_gen_boot_flow(ctx, i1, i2, i3, 6);
        decoded = vc4_decode_scalar48(ctx, i1, i2, i3);
""",
            "scalar48 trace",
        ),
        (
            """        ctx->base.pc_next = ctx->pc + 10;
        decoded = vc4_decode_vector80_delay(i1, i2, i3, i4, i5);
""",
            """        ctx->base.pc_next = ctx->pc + 10;
        vc4_gen_boot_flow(ctx, i1, i2, i3, 10);
        decoded = vc4_decode_vector80_delay(i1, i2, i3, i4, i5);
""",
            "vector80 trace",
        ),
        (
            """        ctx->base.pc_next = ctx->pc + 6;
        decoded = vc4_decode_vector48_delay(i1, i2, i3);
""",
            """        ctx->base.pc_next = ctx->pc + 6;
        vc4_gen_boot_flow(ctx, i1, i2, i3, 6);
        decoded = vc4_decode_vector48_delay(i1, i2, i3);
""",
            "vector48 trace",
        ),
    ]
    for old, new, label in replacements:
        text = replace_once(text, old, new, label)

    TRANSLATE.write_text(text, encoding="utf-8")


def main() -> int:
    if already_materialized():
        print("VC4 boot-flow trace is already materialized.")
        return 0

    update_helper_declaration()
    update_helper_implementation()
    update_translator()
    print("Materialized bounded VC4 bootcode retry-flow tracing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
