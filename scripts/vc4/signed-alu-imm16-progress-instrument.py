#!/usr/bin/env python3
"""Temporarily trace VC4 POP-to-PC transfers into low memory."""

from __future__ import annotations

from pathlib import Path


def replace_one(path: Path, old: str, new: str, label: str) -> None:
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"unexpected {label}: found {count} anchors")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def instrument_helper_header() -> None:
    path = Path("target/vc4/helper.h")
    old = "DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)\n"
    new = (
        "DEF_HELPER_3(vc4_trace_low_pop, void, env, i32, i32)\n"
        + old
    )
    text = path.read_text(encoding="utf-8")
    if "vc4_trace_low_pop" in text:
        return
    replace_one(path, old, new, "trace helper declaration")


def instrument_helper_implementation() -> None:
    path = Path("target/vc4/op_helper.c")
    text = path.read_text(encoding="utf-8")
    if "helper_vc4_trace_low_pop" in text:
        return

    old = (
        "static void vc4_push(CPUArchState *envp, CPUVC4State *env, "
        "unsigned reg)\n"
    )
    implementation = r'''void helper_vc4_trace_low_pop(CPUArchState *envp,
                                  uint32_t source,
                                  uint32_t insn)
{
    CPUVC4State *env = vc4_helper_env(envp);

    if (source >= 0x200 && env->pc < 0x200) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VC4_LOW_POP source=0x%08x target=0x%08x "
                      "insn=0x%04x lr=0x%08x sr=0x%08x "
                      "sp=0x%08x\n",
                      source, env->pc, insn & 0xffff,
                      env->gpr[VC4_REG_LR], env->sr,
                      env->gpr[VC4_REG_SP]);
    }
}

'''
    replace_one(
        path, old, implementation + old, "trace helper implementation"
    )


def instrument_translator() -> None:
    path = Path("target/vc4/translate.c")
    text = path.read_text(encoding="utf-8")
    if "gen_helper_vc4_trace_low_pop" in text:
        return

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
            gen_helper_vc4_trace_low_pop(
                tcg_env, tcg_constant_i32(ctx->pc),
                tcg_constant_i32(insn));
            ctx->base.is_jmp = DISAS_JUMP;
        }
'''
    replace_one(path, old, new, "POP-to-PC trace")


def main() -> int:
    instrument_helper_header()
    instrument_helper_implementation()
    instrument_translator()
    print("instrumented VC4 low-memory POP-to-PC transfers")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
