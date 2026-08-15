#!/usr/bin/env python3
"""Keep the fall-through PC for a predicated scalar FP write to r31."""

from pathlib import Path

path = Path(__file__).resolve().parents[2] / "target/vc4/translate.c"
text = path.read_text(encoding="utf-8")
old = """    skip = vc4_gen_skip_if_false(cond);
    if (op == VC4_FOP_FCMP) {
        gen_helper_vc4_float_cmp(result, vc4_get_reg(ctx, ra), b);
        vc4_write_nzcv(result);
    } else {
        if (rd == VC4_REG_PC) {
            tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
        }
        gen_helper_vc4_float_op(result, tcg_constant_i32(op),
                                vc4_get_reg(ctx, ra), b);
        vc4_set_reg(ctx, rd, result);
    }
"""
new = """    if (op != VC4_FOP_FCMP && rd == VC4_REG_PC) {
        tcg_gen_movi_i32(cpu_pc, ctx->base.pc_next);
    }

    skip = vc4_gen_skip_if_false(cond);
    if (op == VC4_FOP_FCMP) {
        gen_helper_vc4_float_cmp(result, vc4_get_reg(ctx, ra), b);
        vc4_write_nzcv(result);
    } else {
        gen_helper_vc4_float_op(result, tcg_constant_i32(op),
                                vc4_get_reg(ctx, ra), b);
        vc4_set_reg(ctx, rd, result);
    }
"""
if old in text:
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
elif new not in text:
    raise SystemExit("could not locate generated scalar-FPU PC handling")
