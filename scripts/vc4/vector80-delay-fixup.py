#!/usr/bin/env python3
"""Materialize the side-effect-free VideoCore IV vector80 delay idiom."""

from __future__ import annotations

from pathlib import Path

MARKER = "A discard-only vector ALU instruction has no architectural result."
COMMENT_OLD = ''' * The scalar decoder follows the public VideoCore IV VPU encoding recovered
 * by the Raspberry Pi reverse-engineering community.  The vector ISA is
 * deliberately rejected for now rather than guessed.
'''
COMMENT_NEW = ''' * The scalar decoder follows the public VideoCore IV VPU encoding recovered
 * by the Raspberry Pi reverse-engineering community.  The vector register
 * file remains unimplemented; one verified side-effect-free vector80 delay
 * idiom is accepted rather than treating the entire vector ISA as scalar.
'''
HELPER_ANCHOR = '''static void vc4_tr_init_disas_context(DisasContextBase *dcbase, CPUState *cs)
'''
HELPER = r'''static bool vc4_decode_vector80_delay(uint16_t i1, uint16_t i2,
                                      uint16_t i3, uint16_t i4,
                                      uint16_t i5)
{
    unsigned width = (i1 >> 9) & 1;
    unsigned op = (i1 >> 3) & 0x3f;
    unsigned repeat = i1 & 7;
    unsigned dst = i2 >> 6;
    unsigned src_a = ((i2 & 0x3f) << 4) | (i3 >> 12);
    bool set_flags = (i3 & 0x0800) != 0;
    bool immediate_b = (i3 & 0x0400) != 0;
    unsigned predicate = i5 >> 13;
    unsigned scalar_update = (i5 >> 6) & 0x7f;

    /*
     * A discard-only vector ALU instruction has no architectural result.
     * Production bootcode.bin uses v16mov REP32 with both D and A mapped to
     * the discard/unused encoding after a DBUS reset write.  B and its
     * addressing modifiers are read-only, while SETF, vector predication,
     * accumulator and scalar-reduction updates are all disabled.
     *
     * Accept only that narrow delay class.  The vector register file and all
     * vector instructions with visible state remain deliberately unsupported.
     */
    (void)i4;
    return (i1 & 0xfc00) == 0xfc00 &&
           width == 0 && op == 0 && repeat == 5 &&
           dst == 0x380 && src_a == 0x380 &&
           !set_flags && !immediate_b &&
           predicate == 0 && scalar_update == 0;
}

'''
DECL_OLD = '''    uint16_t i1, i2, i3;
'''
DECL_NEW = '''    uint16_t i1, i2, i3, i4, i5;
'''
DISPATCH_OLD = '''    } else {
        /* Vector48 (0xf000) and Vector80 (0xf800) are separate work. */
        ctx->base.pc_next = ctx->pc + ((i1 & 0xf800) == 0xf800 ? 10 : 6);
        decoded = false;
    }
'''
DISPATCH_NEW = '''    } else if ((i1 & 0xf800) == 0xf800) {
        i2 = vc4_lduw(ctx, ctx->pc + 2);
        i3 = vc4_lduw(ctx, ctx->pc + 4);
        i4 = vc4_lduw(ctx, ctx->pc + 6);
        i5 = vc4_lduw(ctx, ctx->pc + 8);
        ctx->base.pc_next = ctx->pc + 10;
        decoded = vc4_decode_vector80_delay(i1, i2, i3, i4, i5);
    } else {
        /* Vector48 remains separate work. */
        ctx->base.pc_next = ctx->pc + 6;
        decoded = false;
    }
'''


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one {label} anchor, found {count}")
    return text.replace(old, new)


def main() -> int:
    path = Path("target/vc4/translate.c")
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("VideoCore IV vector80 delay decoding is already materialized.")
        return 0

    text = replace_once(text, COMMENT_OLD, COMMENT_NEW, "file comment")
    text = replace_once(text, HELPER_ANCHOR, HELPER + HELPER_ANCHOR,
                        "vector80 helper insertion")
    text = replace_once(text, DECL_OLD, DECL_NEW, "translator halfword declaration")
    text = replace_once(text, DISPATCH_OLD, DISPATCH_NEW,
                        "vector instruction dispatch")
    path.write_text(text, encoding="utf-8")
    print("Materialized discard-only VideoCore IV vector80 delay decoding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
