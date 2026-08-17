#!/usr/bin/env python3
"""Check the committed VC4 software-exception architecture."""

from pathlib import Path


cpu_h = Path("target/vc4/cpu.h").read_text()
cpu_c = Path("target/vc4/cpu.c").read_text()
helper_h = Path("target/vc4/helper.h").read_text()
helper_c = Path("target/vc4/op_helper.c").read_text()
translate_c = Path("target/vc4/translate.c").read_text()
intc_h = Path("include/hw/vc4/bcm2835_vc4_intc.h").read_text()
intc_c = Path("hw/vc4/bcm2835_vc4_intc.c").read_text()

required = {
    "VC4_EXCP_SWI": cpu_h,
    "VC4_MAX_EXCEPTION_DEPTH": cpu_h,
    "external_irq_frames": cpu_h,
    "swi_vector": cpu_h,
    "case VC4_EXCP_SWI:": cpu_c,
    "bcm2835_vc4_intc_vector_base(cpu->intc)": cpu_c,
    "DEF_HELPER_3(vc4_swi": helper_h,
    "cs->exception_index = VC4_EXCP_SWI;": helper_c,
    "gen_helper_vc4_swi": translate_c,
    "bcm2835_vc4_intc_vector_base": intc_h,
    "return s->vaddr;": intc_c,
}
missing = [name for name, text in required.items() if name not in text]
if missing:
    raise SystemExit("missing SWI architecture: " + ", ".join(missing))

forbidden = {
    "public synchronous SWI entry": "bool vc4_cpu_enter_swi" in cpu_c,
    "CPU reach-through to INTC state": "cpu->intc->vaddr" in cpu_c,
}
present = [name for name, found in forbidden.items() if found]
if present:
    raise SystemExit("stale SWI architecture: " + ", ".join(present))

for opcode in range(0x01C0, 0x0200):
    assert 0x20 <= 0x20 + (opcode & 0x1F) <= 0x3F
for opcode in range(0x0020, 0x0040):
    assert 0 <= (opcode & 0x1F) <= 31

print("VC4 first-class SWI architecture checks passed")
