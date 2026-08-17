#!/usr/bin/env python3
"""Reconcile the VC4 SWI implementation onto the integration branch.

The production branch already has working SWI frames, nesting bounds, and
IRQ-frame provenance.  This script retains those safeguards while routing SWI
through QEMU's CPU exception dispatcher, as validated on the independent v4
feature lane.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent, indent
import re
import sys


def replace_once(path: str, old: str, new: str, label: str) -> None:
    file = Path(path)
    text = file.read_text()
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    file.write_text(text.replace(old, new, 1))


def regex_once(path: str, pattern: str, replacement: str, label: str) -> None:
    file = Path(path)
    text = file.read_text()
    updated, count = re.subn(
        pattern,
        replacement,
        text,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    file.write_text(updated)


def patch_cpu_header() -> None:
    path = Path("target/vc4/cpu.h")
    text = path.read_text()

    if "VC4_EXCP_SWI" not in text:
        replace_once(
            str(path),
            "enum {\n"
            "    VC4_EXCP_ILLEGAL = 1,\n"
            "    VC4_EXCP_IRQ,\n"
            "};\n",
            "enum {\n"
            "    VC4_EXCP_ILLEGAL = 1,\n"
            "    VC4_EXCP_IRQ,\n"
            "    VC4_EXCP_SWI,\n"
            "};\n",
            "software-exception index",
        )
        text = path.read_text()

    if "uint32_t swi_vector;" not in text:
        replace_once(
            str(path),
            "    uint32_t normal_sp;\n"
            "    uint32_t external_irq_frames;\n"
            "    uint8_t exception_depth;\n",
            "    uint32_t normal_sp;\n"
            "    uint32_t external_irq_frames;\n"
            "    uint32_t swi_vector;\n"
            "    uint8_t exception_depth;\n",
            "pending software-exception vector",
        )
        text = path.read_text()

    declaration = (
        "bool vc4_cpu_enter_swi(VC4CPU *cpu, uint32_t number,\n"
        "                       uint32_t return_pc);\n\n"
    )
    if declaration in text:
        path.write_text(text.replace(declaration, "", 1))


def patch_intc() -> None:
    header = Path("include/hw/vc4/bcm2835_vc4_intc.h")
    text = header.read_text()
    if "bcm2835_vc4_intc_vector_base" not in text:
        replace_once(
            str(header),
            "void bcm2835_vc4_intc_complete(BCM2835VC4IntcState *s);\n"
            "\n#endif\n",
            "void bcm2835_vc4_intc_complete(BCM2835VC4IntcState *s);\n"
            "uint32_t bcm2835_vc4_intc_vector_base(\n"
            "    const BCM2835VC4IntcState *s);\n"
            "\n#endif\n",
            "interrupt-vector base accessor declaration",
        )

    source = Path("hw/vc4/bcm2835_vc4_intc.c")
    text = source.read_text()
    if "bcm2835_vc4_intc_vector_base" not in text:
        replace_once(
            str(source),
            "type_init(vc4_intc_register_types)",
            "type_init(vc4_intc_register_types)\n\n"
            "uint32_t bcm2835_vc4_intc_vector_base(\n"
            "    const BCM2835VC4IntcState *s)\n"
            "{\n"
            "    return s->vaddr;\n"
            "}\n",
            "interrupt-vector base accessor implementation",
        )


def patch_cpu_dispatch() -> None:
    path = Path("target/vc4/cpu.c")
    text = path.read_text()

    if "bool vc4_cpu_enter_swi" in text:
        regex_once(
            str(path),
            r"\nbool vc4_cpu_enter_swi\(VC4CPU \*cpu, uint32_t number,\n"
            r"\s+uint32_t return_pc\)\n"
            r"\{.*?\n\}\n(?=\nstatic void vc4_cpu_do_interrupt)",
            "\n",
            "remove synchronous SWI shortcut",
        )
        text = path.read_text()

    if "case VC4_EXCP_SWI:" not in text:
        swi_case = indent(
            dedent(
                """\
                case VC4_EXCP_SWI: {
                    VC4CPU *cpu = VC4_CPU(cs);

                    if (!cpu->intc) {
                        qemu_log_mask(
                            LOG_GUEST_ERROR,
                            "VideoCore IV: SWI without interrupt controller\\n");
                        cs->halted = 1;
                        break;
                    }
                    if (!vc4_cpu_enter_vector(
                            cpu, env->swi_vector,
                            bcm2835_vc4_intc_vector_base(cpu->intc),
                            env->pc, false)) {
                        qemu_log_mask(
                            LOG_GUEST_ERROR,
                            "VideoCore IV: could not enter SWI vector %u at "
                            "0x%08x\\n", env->swi_vector, env->pc);
                        cs->halted = 1;
                    }
                    break;
                }
                """
            ),
            "    ",
        )
        replace_once(
            str(path),
            "    case VC4_EXCP_ILLEGAL:\n",
            swi_case + "    case VC4_EXCP_ILLEGAL:\n",
            "first-class SWI dispatch",
        )


def patch_helper() -> None:
    path = Path("target/vc4/op_helper.c")
    text = path.read_text()
    if "cs->exception_index = VC4_EXCP_SWI;" in text:
        return

    replacement = dedent(
        """\
        G_NORETURN void helper_vc4_swi(CPUArchState *envp,
                                       uint32_t number,
                                       uint32_t return_pc)
        {
            CPUVC4State *env = vc4_helper_env(envp);
            CPUState *cs = env_cpu(envp);

            env->swi_vector = UINT32_C(0x20) + (number & 0x1f);
            env->pc = return_pc;
            cs->exception_index = VC4_EXCP_SWI;
            cpu_loop_exit(cs);
        }

        void helper_vc4_rti"""
    )
    regex_once(
        str(path),
        r"G_NORETURN void helper_vc4_swi\(CPUArchState \*envp,\n"
        r"\s+uint32_t number,\n"
        r"\s+uint32_t return_pc\)\n"
        r"\{.*?\n\}\n\n"
        r"void helper_vc4_rti",
        replacement,
        "first-class SWI helper",
    )


def main() -> int:
    patch_cpu_header()
    patch_intc()
    patch_cpu_dispatch()
    patch_helper()
    print("reconciled first-class VC4 software exceptions")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1) from error
