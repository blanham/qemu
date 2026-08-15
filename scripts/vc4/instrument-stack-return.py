#!/usr/bin/env python3
"""Add focused stack-frame tracing around the proven VC4 bad return."""

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
        "DEF_HELPER_5(vc4_push_pop, void, env, i32, i32, i32, i32)\n",
        "DEF_HELPER_6(vc4_push_pop, void, env, i32, i32, i32, i32, i32)\n",
        "push/pop helper declaration",
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
        "push/pop translation call",
    )

    helper_prefix = r'''#define VC4_TRACE_STACK_LOW  0x8001d500u
#define VC4_TRACE_STACK_HIGH 0x8001d800u

static unsigned vc4_lrpc_stack_words(uint32_t lrpc, uint32_t count)
{
    if (!lrpc) {
        return count;
    }

    /*
     * The all-ones count encodings are compact single-register forms.
     * Otherwise an LR/PC form transfers count ordinary registers plus LR/PC.
     */
    if (((count - 1) & 0xf) == 0xf) {
        return (count - 1) == 0x1f ? 2 : 1;
    }
    return count + 1;
}

static bool vc4_stack_frame_overlaps_trace_window(uint32_t base,
                                                   uint32_t end)
{
    return base < VC4_TRACE_STACK_HIGH && end > VC4_TRACE_STACK_LOW;
}

static void vc4_trace_lrpc_stack(CPUArchState *envp,
                                 CPUVC4State *env,
                                 uint32_t source,
                                 uint32_t push,
                                 uint32_t lrpc,
                                 uint32_t start,
                                 uint32_t count,
                                 bool before)
{
    const char *phase = before ? "pre" : "post";
    const char *operation = push ? "push" : "pop";
    unsigned words;
    uint32_t bytes;
    uint32_t sp;
    uint32_t base;
    uint32_t end;
    GString *line;
    unsigned i;

    if (!lrpc) {
        return;
    }

    words = vc4_lrpc_stack_words(lrpc, count);
    bytes = words * 4;
    sp = env->gpr[VC4_REG_SP];

    if (push) {
        base = before ? sp - bytes : sp;
        end = before ? sp : sp + bytes;
    } else {
        base = before ? sp : sp - bytes;
        end = before ? sp + bytes : sp;
    }

    if (source != 0x0000166c &&
        !vc4_stack_frame_overlaps_trace_window(base, end)) {
        return;
    }

    line = g_string_new(NULL);
    g_string_append_printf(
        line,
        "VC4_STACK_FRAME phase=%s source=0x%08x op=%s "
        "start=%u count=%u words=%u sp=0x%08x "
        "frame=0x%08x..0x%08x lr=0x%08x pc=0x%08x sr=0x%08x "
        "depth=%u normal_sp=0x%08x r28=0x%08x data=",
        phase, source, operation, start, count, words, sp, base, end,
        env->gpr[VC4_REG_LR], env->pc, env->sr, env->exception_depth,
        env->normal_sp, env->gpr[28]);

    for (i = 0; i < words; i++) {
        uint32_t address = base + i * 4;
        uint32_t value = cpu_ldl_le_data(envp, address);

        g_string_append_printf(line, "%s0x%08x:0x%08x",
                               i ? "," : "", address, value);
    }
    g_string_append_c(line, '\n');
    qemu_log_mask(LOG_GUEST_ERROR, "%s", line->str);
    g_string_free(line, TRUE);
}

'''

    old_open = '''void helper_vc4_push_pop(CPUArchState *envp, uint32_t push, uint32_t lrpc,
                         uint32_t start, uint32_t count)
{
    CPUVC4State *env = vc4_helper_env(envp);
    int i;

    if (push) {
'''
    new_open = helper_prefix + '''void helper_vc4_push_pop(CPUArchState *envp,
                         uint32_t push, uint32_t lrpc,
                         uint32_t start, uint32_t count,
                         uint32_t source)
{
    CPUVC4State *env = vc4_helper_env(envp);
    int i;

    vc4_trace_lrpc_stack(envp, env, source, push, lrpc, start, count, true);

    if (push) {
'''
    replace_one(
        Path("target/vc4/op_helper.c"),
        old_open,
        new_open,
        "push/pop helper opening",
    )

    replace_one(
        Path("target/vc4/op_helper.c"),
        '''        if (lrpc) {
            vc4_pop(envp, env, VC4_REG_PC);
        }
    }
}

void helper_vc4_rti(CPUArchState *envp)
''',
        '''        if (lrpc) {
            vc4_pop(envp, env, VC4_REG_PC);
        }
    }

    vc4_trace_lrpc_stack(envp, env, source, push, lrpc, start, count, false);
}

void helper_vc4_rti(CPUArchState *envp)
''',
        "push/pop helper closing",
    )

    print("instrumented focused VC4 stack-return tracing")


if __name__ == "__main__":
    main()
