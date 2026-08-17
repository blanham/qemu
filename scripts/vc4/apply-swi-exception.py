#!/usr/bin/env python3
"""Apply the VC4 software-interrupt and interrupt-wiring source changes."""

from __future__ import annotations

from pathlib import Path


def replace_once(path: str, old: str, new: str, label: str) -> None:
    file = Path(path)
    text = file.read_text()
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    file.write_text(text.replace(old, new, 1))


def main() -> int:
    replace_once(
        "target/vc4/cpu.h",
        "#define VC4_CPUID_VALUE 0x04000104u\n",
        "#define VC4_CPUID_VALUE 0x04000104u\n"
        "#define VC4_MAX_EXCEPTION_DEPTH 32\n",
        "exception-depth limit",
    )

    replace_once(
        "target/vc4/cpu.h",
        "    uint32_t normal_sp;\n"
        "    uint8_t exception_depth;\n",
        "    uint32_t normal_sp;\n"
        "    uint32_t external_irq_frames;\n"
        "    uint8_t exception_depth;\n",
        "exception-frame provenance",
    )

    replace_once(
        "target/vc4/cpu.h",
        "void vc4_translate_init(void);\n",
        "bool vc4_cpu_enter_swi(VC4CPU *cpu, uint32_t number,\n"
        "                       uint32_t return_pc);\n\n"
        "void vc4_translate_init(void);\n",
        "SWI entry declaration",
    )

    replace_once(
        "target/vc4/cpu-qom.h",
        "#include \"hw/core/cpu.h\"\n",
        "#include \"hw/core/cpu.h\"\n\n"
        "typedef struct BCM2835VC4IntcState BCM2835VC4IntcState;\n",
        "interrupt-controller forward declaration",
    )

    replace_once(
        "target/vc4/cpu-qom.h",
        "#endif /* VC4_CPU_QOM_H */\n",
        "void vc4_cpu_set_intc(CPUState *cs, BCM2835VC4IntcState *intc);\n\n"
        "#endif /* VC4_CPU_QOM_H */\n",
        "interrupt-controller attachment declaration",
    )

    replace_once(
        "target/vc4/cpu.c",
        "static inline CPUArchState *vc4_tcg_env(CPUState *cs)\n"
        "{\n"
        "    return (CPUArchState *)(void *)vc4_cpu_env(cs);\n"
        "}\n",
        "static inline CPUArchState *vc4_tcg_env(CPUState *cs)\n"
        "{\n"
        "    return (CPUArchState *)(void *)vc4_cpu_env(cs);\n"
        "}\n\n"
        "void vc4_cpu_set_intc(CPUState *cs, BCM2835VC4IntcState *intc)\n"
        "{\n"
        "    VC4_CPU(cs)->intc = intc;\n"
        "}\n",
        "interrupt-controller attachment implementation",
    )

    old = """static bool vc4_cpu_enter_irq(VC4CPU *cpu)
{
    CPUState *cs = CPU(cpu);
    CPUVC4State *env = vc4_cpu_env(cs);
    uint32_t vector;
    uint32_t vector_base;
    uint32_t vector_entry;
    uint32_t saved_sr = env->sr;

    if (!cpu->intc ||
        !bcm2835_vc4_intc_acknowledge(cpu->intc, &vector, &vector_base)) {
        return false;
    }

    if (env->exception_depth == 0) {
        env->normal_sp = env->gpr[VC4_REG_SP];
        env->gpr[VC4_REG_SP] = env->gpr[28];
    }

    vc4_irq_push(cs, env, env->pc);
    vc4_irq_push(cs, env, saved_sr);
    env->exception_depth++;

    vector_entry = cpu_ldl_le_data(vc4_tcg_env(cs),
                                   vector_base + vector * 4);

    env->sr = saved_sr & ~(VC4_SR_U | VC4_SR_I | VC4_SR_S);
    if (vector_entry & 1) {
        env->sr |= VC4_SR_S;
    }
    env->pc = vector_entry & ~1u;
    cs->halted = 0;
    return true;
}
"""
    new = """static bool vc4_cpu_enter_vector(VC4CPU *cpu, uint32_t vector,
                                  uint32_t vector_base,
                                  uint32_t return_pc,
                                  bool external)
{
    CPUState *cs = CPU(cpu);
    CPUVC4State *env = vc4_cpu_env(cs);
    uint32_t vector_entry;
    uint32_t saved_sr = env->sr;
    uint32_t frame_bit;

    if (env->exception_depth >= VC4_MAX_EXCEPTION_DEPTH) {
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VideoCore IV: exception nesting overflow at "
                      "0x%08x\\n", return_pc);
        return false;
    }

    if (env->exception_depth == 0) {
        env->normal_sp = env->gpr[VC4_REG_SP];
        env->gpr[VC4_REG_SP] = env->gpr[28];
    }

    /* RTI consumes SR at SP and PC at SP + 4. */
    vc4_irq_push(cs, env, return_pc);
    vc4_irq_push(cs, env, saved_sr);

    frame_bit = UINT32_C(1) << env->exception_depth;
    if (external) {
        env->external_irq_frames |= frame_bit;
    } else {
        env->external_irq_frames &= ~frame_bit;
    }
    env->exception_depth++;

    vector_entry = cpu_ldl_le_data(vc4_tcg_env(cs),
                                   vector_base + vector * 4);

    env->sr = saved_sr & ~(VC4_SR_U | VC4_SR_I | VC4_SR_S);
    if (vector_entry & 1) {
        env->sr |= VC4_SR_S;
    }
    env->pc = vector_entry & ~1u;
    cs->halted = 0;

    qemu_log_mask(CPU_LOG_INT,
                  "VideoCore IV: %s vector=%u entry=0x%08x "
                  "return=0x%08x depth=%u\\n",
                  external ? "IRQ" : "SWI", vector, vector_entry,
                  return_pc, env->exception_depth);
    return true;
}

static bool vc4_cpu_enter_irq(VC4CPU *cpu)
{
    CPUState *cs = CPU(cpu);
    CPUVC4State *env = vc4_cpu_env(cs);
    uint32_t vector;
    uint32_t vector_base;

    if (!cpu->intc ||
        !bcm2835_vc4_intc_acknowledge(cpu->intc, &vector, &vector_base)) {
        return false;
    }

    if (!vc4_cpu_enter_vector(cpu, vector, vector_base, env->pc, true)) {
        bcm2835_vc4_intc_complete(cpu->intc);
        return false;
    }
    return true;
}

bool vc4_cpu_enter_swi(VC4CPU *cpu, uint32_t number,
                       uint32_t return_pc)
{
    if (!cpu->intc) {
        return false;
    }

    return vc4_cpu_enter_vector(cpu, 32 + (number & 31),
                                cpu->intc->vaddr, return_pc, false);
}
"""
    replace_once("target/vc4/cpu.c", old, new, "common vector entry")

    replace_once(
        "target/vc4/helper.h",
        "DEF_HELPER_1(vc4_rti, void, env)\n",
        "DEF_HELPER_3(vc4_swi, noreturn, env, i32, i32)\n"
        "DEF_HELPER_1(vc4_rti, void, env)\n",
        "SWI helper declaration",
    )

    old = """void helper_vc4_rti(CPUArchState *envp)
{
    CPUVC4State *env = vc4_helper_env(envp);
    VC4CPU *cpu = vc4_env_archcpu(env);
    uint32_t sp = env->gpr[VC4_REG_SP];

    env->sr = cpu_ldl_le_data(envp, sp);
    env->pc = cpu_ldl_le_data(envp, sp + 4);
    env->gpr[VC4_REG_SP] = sp + 8;

    if (env->exception_depth) {
        env->exception_depth--;
        if (env->exception_depth == 0) {
            env->gpr[28] = env->gpr[VC4_REG_SP];
            env->gpr[VC4_REG_SP] = env->normal_sp;
        }
    }

    if (cpu->intc) {
        bcm2835_vc4_intc_complete(cpu->intc);
    }
}
"""
    new = """G_NORETURN void helper_vc4_swi(CPUArchState *envp,
                                   uint32_t number,
                                   uint32_t return_pc)
{
    CPUVC4State *env = vc4_helper_env(envp);
    CPUState *cs = env_cpu(envp);
    VC4CPU *cpu = vc4_env_archcpu(env);

    if (!vc4_cpu_enter_swi(cpu, number, return_pc)) {
        env->pc = return_pc - 2;
        cs->exception_index = VC4_EXCP_ILLEGAL;
        qemu_log_mask(LOG_GUEST_ERROR,
                      "VideoCore IV: could not enter SWI %u at "
                      "0x%08x\\n", number & 31, env->pc);
    }
    cpu_loop_exit(cs);
}

void helper_vc4_rti(CPUArchState *envp)
{
    CPUVC4State *env = vc4_helper_env(envp);
    VC4CPU *cpu = vc4_env_archcpu(env);
    uint32_t sp = env->gpr[VC4_REG_SP];
    bool external = false;

    env->sr = cpu_ldl_le_data(envp, sp);
    env->pc = cpu_ldl_le_data(envp, sp + 4);
    env->gpr[VC4_REG_SP] = sp + 8;

    if (env->exception_depth) {
        uint32_t frame_bit = UINT32_C(1) <<
                             (env->exception_depth - 1);

        external = (env->external_irq_frames & frame_bit) != 0;
        env->external_irq_frames &= ~frame_bit;
        env->exception_depth--;
        if (env->exception_depth == 0) {
            env->gpr[28] = env->gpr[VC4_REG_SP];
            env->gpr[VC4_REG_SP] = env->normal_sp;
        }
    }

    if (external && cpu->intc) {
        bcm2835_vc4_intc_complete(cpu->intc);
    }

    qemu_log_mask(CPU_LOG_INT,
                  "VideoCore IV: RTI pc=0x%08x sr=0x%08x depth=%u\\n",
                  env->pc, env->sr, env->exception_depth);
}
"""
    replace_once(
        "target/vc4/op_helper.c", old, new, "SWI and RTI helpers"
    )

    old = """    if ((insn & 0xffe0) == 0x0040) {
        tcg_gen_mov_i32(cpu_pc, vc4_get_reg(ctx, insn & 0x1f));
"""
    new = """    if ((insn & 0xffe0) == 0x0020) {
        gen_helper_vc4_swi(tcg_env,
                            vc4_get_reg(ctx, insn & 0x1f),
                            tcg_constant_i32(ctx->base.pc_next));
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    }
    if ((insn & 0xffc0) == 0x01c0) {
        gen_helper_vc4_swi(tcg_env,
                            tcg_constant_i32(insn & 0x3f),
                            tcg_constant_i32(ctx->base.pc_next));
        ctx->base.is_jmp = DISAS_NORETURN;
        return true;
    }
    if ((insn & 0xffe0) == 0x0040) {
        tcg_gen_mov_i32(cpu_pc, vc4_get_reg(ctx, insn & 0x1f));
"""
    replace_once(
        "target/vc4/translate.c", old, new, "16-bit SWI decoder"
    )

    replace_once(
        "hw/arm/vc4_raspi3_hetero.c",
        "#include \"target/arm/cpu.h\"\n",
        "#include \"target/arm/cpu.h\"\n"
        "#define VC4_SECONDARY_FRONTEND 1\n"
        "#include \"target/vc4/cpu-qom.h\"\n"
        "#undef VC4_SECONDARY_FRONTEND\n",
        "secondary VC4 CPU API include",
    )

    replace_once(
        "hw/arm/vc4_raspi3_hetero.c",
        "    if (!qdev_realize(DEVICE(s->vpu_cpu), NULL, &error_fatal)) {\n"
        "        g_assert_not_reached();\n"
        "    }\n"
        "    s->arm_power_irq = qemu_allocate_irq(vc4_raspi3_arm_power_on, s, 0);\n",
        "    if (!qdev_realize(DEVICE(s->vpu_cpu), NULL, &error_fatal)) {\n"
        "        g_assert_not_reached();\n"
        "    }\n"
        "    vc4_cpu_set_intc(s->vpu_cpu, &s->vpu_intc[0]);\n"
        "    sysbus_connect_irq(SYS_BUS_DEVICE(&s->vpu_intc[0]), 0,\n"
        "                       qdev_get_gpio_in(DEVICE(s->vpu_cpu), 0));\n"
        "    s->arm_power_irq = qemu_allocate_irq(vc4_raspi3_arm_power_on, s, 0);\n",
        "VPU interrupt-controller wiring",
    )

    Path("scripts/vc4/check-swi-exception.py").chmod(0o755)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
