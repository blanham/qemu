#!/usr/bin/env python3
"""Trace architectural stack-pointer writes in the proven failing VC4 leaf."""

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


def main() -> None:
    subprocess.run(
        [sys.executable, "scripts/vc4/instrument-low-pc-transfer.py"],
        check=True,
    )

    replace_one(
        Path("target/vc4/helper.h"),
        "DEF_HELPER_5(vc4_trace_target, i32, env, i32, i32, i32, i32)\n",
        "DEF_HELPER_5(vc4_trace_target, i32, env, i32, i32, i32, i32)\n"
        "DEF_HELPER_4(vc4_trace_leaf_sp, void, env, i32, i32, i32)\n",
        "leaf SP helper declaration",
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
        if (reg == VC4_REG_SP) {
            gen_helper_vc4_trace_leaf_sp(
                tcg_env, tcg_constant_i32(ctx->pc), value,
                tcg_constant_i32(1));
        }
        tcg_gen_mov_i32(cpu_gpr[reg], value);
''',
        "dynamic SP write",
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
        if (reg == VC4_REG_SP) {
            gen_helper_vc4_trace_leaf_sp(
                tcg_env, tcg_constant_i32(ctx->pc),
                tcg_constant_i32(value), tcg_constant_i32(2));
        }
        tcg_gen_movi_i32(cpu_gpr[reg], value);
''',
        "immediate SP write",
    )

    implementation = r'''#define VC4_FAILURE_LEAF_START 0x000014fcu
#define VC4_FAILURE_LEAF_END   0x0000166eu

void helper_vc4_trace_leaf_sp(CPUArchState *envp, uint32_t source,
                              uint32_t value, uint32_t kind)
{
    CPUVC4State *env = vc4_helper_env(envp);
    uint32_t insn;

    if (source < VC4_FAILURE_LEAF_START ||
        source >= VC4_FAILURE_LEAF_END) {
        return;
    }

    insn = cpu_ldl_le_data(envp, source);
    qemu_log_mask(LOG_GUEST_ERROR,
                  "VC4_LEAF_SP source=0x%08x kind=%u insn=0x%08x "
                  "old=0x%08x new=0x%08x delta=%d "
                  "lr=0x%08x pc=0x%08x sr=0x%08x\n",
                  source, kind, insn,
                  env->gpr[VC4_REG_SP], value,
                  (int32_t)(value - env->gpr[VC4_REG_SP]),
                  env->gpr[VC4_REG_LR], env->pc, env->sr);
}

'''

    replace_one(
        Path("target/vc4/op_helper.c"),
        "static void vc4_trace_low_target(CPUArchState *envp,\n",
        implementation +
        "static void vc4_trace_low_target(CPUArchState *envp,\n",
        "leaf SP helper implementation",
    )

    print("instrumented VC4 failing-leaf stack-pointer writes")


if __name__ == "__main__":
    main()
