#!/usr/bin/env python3
"""Materialize temporary tracing for the first VC4 high-to-low PC transfer."""

from __future__ import annotations

from pathlib import Path


def replace_one(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label}: found {count} anchors")
    return text.replace(old, new, 1)


def instrument_helper_header() -> None:
    path = Path("target/vc4/helper.h")
    text = path.read_text()
    anchor = "DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)\n"
    declaration = (
        "DEF_HELPER_5(vc4_trace_target, i32, env, i32, i32, i32, i32)\n"
    )
    path.write_text(
        replace_one(text, anchor, declaration + anchor,
                    "trace helper declaration")
    )


def instrument_helper_implementation() -> None:
    path = Path("target/vc4/op_helper.c")
    text = path.read_text()
    anchor = (
        "static void vc4_push(CPUArchState *envp, CPUVC4State *env, "
        "unsigned reg)\n"
    )
    implementation = r'''static void vc4_trace_low_target(CPUArchState *envp,
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
    vc4_trace_low_target(envp, vc4_helper_env(envp), source, target,
                         kind, aux);
    return target;
}

'''
    path.write_text(
        replace_one(text, anchor, implementation + anchor,
                    "trace helper implementation")
    )


def instrument_translator() -> None:
    path = Path("target/vc4/translate.c")
    text = path.read_text()

    old = '''    } else {
        tcg_gen_mov_i32(cpu_pc, value);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

static void vc4_set_reg_imm(DisasContext *ctx, unsigned reg, uint32_t value)
'''
    new = '''    } else {
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc), value,
            tcg_constant_i32(1), tcg_constant_i32(reg));
        tcg_gen_mov_i32(cpu_pc, traced);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

static void vc4_set_reg_imm(DisasContext *ctx, unsigned reg, uint32_t value)
'''
    text = replace_one(text, old, new, "dynamic PC write")

    old = '''    } else {
        tcg_gen_movi_i32(cpu_pc, value);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

static TCGv_i32 vc4_gen_cond_from_sr'''
    new = '''    } else {
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc),
            tcg_constant_i32(value), tcg_constant_i32(2),
            tcg_constant_i32(reg));
        tcg_gen_mov_i32(cpu_pc, traced);
        ctx->base.is_jmp = DISAS_JUMP;
    }
}

static TCGv_i32 vc4_gen_cond_from_sr'''
    text = replace_one(text, old, new, "immediate PC write")

    old = '''static void vc4_gen_goto_tb(DisasContext *ctx, unsigned slot, uint32_t dest)
{
    if (translator_use_goto_tb(&ctx->base, dest)) {
        tcg_gen_goto_tb(slot);
        tcg_gen_movi_i32(cpu_pc, dest);
        tcg_gen_exit_tb(ctx->base.tb, slot);
    } else {
        tcg_gen_movi_i32(cpu_pc, dest);
        tcg_gen_lookup_and_goto_ptr();
    }
    ctx->base.is_jmp = DISAS_NORETURN;
}
'''
    new = '''static void vc4_gen_goto_tb(DisasContext *ctx, unsigned slot, uint32_t dest)
{
    if (ctx->pc >= 0x200 && dest < 0x200) {
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc),
            tcg_constant_i32(dest), tcg_constant_i32(3),
            tcg_constant_i32(slot));
        tcg_gen_mov_i32(cpu_pc, traced);
        tcg_gen_lookup_and_goto_ptr();
    } else if (translator_use_goto_tb(&ctx->base, dest)) {
        tcg_gen_goto_tb(slot);
        tcg_gen_movi_i32(cpu_pc, dest);
        tcg_gen_exit_tb(ctx->base.tb, slot);
    } else {
        tcg_gen_movi_i32(cpu_pc, dest);
        tcg_gen_lookup_and_goto_ptr();
    }
    ctx->base.is_jmp = DISAS_NORETURN;
}
'''
    text = replace_one(text, old, new, "direct branch")

    cond_block = '''    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
    tcg_gen_movi_i32(cpu_pc, dest);
    tcg_gen_exit_tb(NULL, 0);
'''
    if text.count(cond_block) != 2:
        raise SystemExit(
            "unexpected conditional branch blocks: "
            f"found {text.count(cond_block)} anchors"
        )

    cond_traced = '''    tcg_gen_brcondi_i32(TCG_COND_EQ, predicate, 0, not_taken);
    if (ctx->pc >= 0x200 && dest < 0x200) {
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc),
            tcg_constant_i32(dest), tcg_constant_i32(KIND),
            tcg_constant_i32(cond));
        tcg_gen_mov_i32(cpu_pc, traced);
    } else {
        tcg_gen_movi_i32(cpu_pc, dest);
    }
    tcg_gen_exit_tb(NULL, 0);
'''
    text = text.replace(
        cond_block, cond_traced.replace("KIND", "4"), 1
    )
    text = text.replace(
        cond_block, cond_traced.replace("KIND", "5"), 1
    )

    old = '''    tcg_gen_shli_i32(displacement, displacement, 1);
    tcg_gen_add_i32(target, base, displacement);
    tcg_gen_mov_i32(cpu_pc, target);
    ctx->base.is_jmp = DISAS_JUMP;
}
'''
    new = '''    tcg_gen_shli_i32(displacement, displacement, 1);
    tcg_gen_add_i32(target, base, displacement);
    {
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc), target,
            tcg_constant_i32(halfword ? 9 : 8),
            tcg_constant_i32(reg));
        tcg_gen_mov_i32(cpu_pc, traced);
    }
    ctx->base.is_jmp = DISAS_JUMP;
}
'''
    text = replace_one(text, old, new, "table branch")

    old = '''    case 0x000a:                    /* RTI */
        gen_helper_vc4_rti(tcg_env);
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
'''
    new = '''    case 0x000a: {                  /* RTI */
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_rti(tcg_env);
        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc), cpu_pc,
            tcg_constant_i32(11), tcg_constant_i32(insn));
        tcg_gen_mov_i32(cpu_pc, traced);
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
'''
    text = replace_one(text, old, new, "RTI transfer")

    old = '''    if ((insn & 0xffe0) == 0x0040) {
        tcg_gen_mov_i32(cpu_pc, vc4_get_reg(ctx, insn & 0x1f));
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
    if ((insn & 0xffe0) == 0x0060) {
        tcg_gen_movi_i32(cpu_gpr[VC4_REG_LR], ctx->base.pc_next);
        tcg_gen_mov_i32(cpu_pc, vc4_get_reg(ctx, insn & 0x1f));
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
'''
    new = '''    if ((insn & 0xffe0) == 0x0040) {
        unsigned reg = insn & 0x1f;
        TCGv_i32 traced = tcg_temp_new_i32();

        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc),
            vc4_get_reg(ctx, reg), tcg_constant_i32(6),
            tcg_constant_i32(reg));
        tcg_gen_mov_i32(cpu_pc, traced);
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
    if ((insn & 0xffe0) == 0x0060) {
        unsigned reg = insn & 0x1f;
        TCGv_i32 traced = tcg_temp_new_i32();

        tcg_gen_movi_i32(cpu_gpr[VC4_REG_LR], ctx->base.pc_next);
        gen_helper_vc4_trace_target(
            traced, tcg_env, tcg_constant_i32(ctx->pc),
            vc4_get_reg(ctx, reg), tcg_constant_i32(7),
            tcg_constant_i32(reg));
        tcg_gen_mov_i32(cpu_pc, traced);
        ctx->base.is_jmp = DISAS_JUMP;
        return true;
    }
'''
    text = replace_one(text, old, new, "indirect branches")

    old = '''        gen_helper_vc4_push_pop(tcg_env,
                            tcg_constant_i32(push),
                            tcg_constant_i32(lrpc),
                            tcg_constant_i32(start),
                            tcg_constant_i32(count));
        if (writes_pc) {
            ctx->base.is_jmp = DISAS_JUMP;
        }
'''
    new = '''        gen_helper_vc4_push_pop(tcg_env,
                            tcg_constant_i32(push),
                            tcg_constant_i32(lrpc),
                            tcg_constant_i32(start),
                            tcg_constant_i32(count));
        if (writes_pc) {
            TCGv_i32 traced = tcg_temp_new_i32();

            gen_helper_vc4_trace_target(
                traced, tcg_env, tcg_constant_i32(ctx->pc), cpu_pc,
                tcg_constant_i32(10), tcg_constant_i32(insn));
            tcg_gen_mov_i32(cpu_pc, traced);
            ctx->base.is_jmp = DISAS_JUMP;
        }
'''
    text = replace_one(text, old, new, "POP-to-PC transfer")

    path.write_text(text)


def instrument_irq_entry() -> None:
    path = Path("target/vc4/cpu.c")
    text = path.read_text()

    old = '''    uint32_t vector_entry;
    uint32_t saved_sr = env->sr;
'''
    new = '''    uint32_t vector_entry;
    uint32_t saved_sr = env->sr;
    uint32_t source_pc = env->pc;
'''
    text = replace_one(text, old, new, "IRQ source capture")

    old = '''    env->pc = vector_entry & ~1u;
    cs->halted = 0;
'''
    new = r'''    env->pc = vector_entry & ~1u;
    if (source_pc >= 0x200 && env->pc < 0x200) {
        uint32_t insn = cpu_ldl_le_data(vc4_tcg_env(cs), source_pc);

        qemu_log_mask(LOG_GUEST_ERROR,
                      "VC4_LOW_TARGET source=0x%08x target=0x%08x "
                      "kind=12 aux=0x%08x insn=0x%08x "
                      "lr=0x%08x sr=0x%08x "
                      "r0=0x%08x r1=0x%08x r2=0x%08x r3=0x%08x "
                      "sp=0x%08x\n",
                      source_pc, env->pc, vector, insn,
                      env->gpr[VC4_REG_LR], env->sr,
                      env->gpr[0], env->gpr[1], env->gpr[2], env->gpr[3],
                      env->gpr[VC4_REG_SP]);
    }
    cs->halted = 0;
'''
    text = replace_one(text, old, new, "IRQ PC write")
    path.write_text(text)


def main() -> int:
    instrument_helper_header()
    instrument_helper_implementation()
    instrument_translator()
    instrument_irq_entry()
    print("instrumented all VC4 PC-writing paths")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
