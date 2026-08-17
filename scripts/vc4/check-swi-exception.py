#!/usr/bin/env python3
from pathlib import Path

cpu_h = Path('target/vc4/cpu.h').read_text()
helper_h = Path('target/vc4/helper.h').read_text()
translate = Path('target/vc4/translate.c').read_text()
helper = Path('target/vc4/op_helper.c').read_text()
cpu = Path('target/vc4/cpu.c').read_text()

required = {
    'VC4_EXCP_SWI': cpu_h,
    'exception_irq_stack': cpu_h,
    'DEF_HELPER_3(vc4_swi': helper_h,
    'gen_helper_vc4_swi': translate,
    'helper_vc4_swi': helper,
    'case VC4_EXCP_SWI': cpu,
    'bcm2835_vc4_intc_vector_base': cpu,
}
missing = [name for name, text in required.items() if name not in text]
if missing:
    raise SystemExit('missing SWI implementation pieces: ' + ', '.join(missing))

for opcode in range(0x01c0, 0x0200):
    selector = opcode & 0x1f
    vector = 0x20 + selector
    assert 0x20 <= vector <= 0x3f
for opcode in range(0x0020, 0x0040):
    register = opcode & 0x1f
    assert 0 <= register <= 31
print('VC4 SWI encoding and exception-state checks passed')
