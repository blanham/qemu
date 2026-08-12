#!/usr/bin/env python3
"""Materialize the exact side-effect-free VideoCore IV vector80 delay idiom."""

from __future__ import annotations

from pathlib import Path

MARKER = "Production bootcode.bin uses this exact discard-only vector80 word."
COMMENT_OLD = ''' * The scalar decoder follows the public VideoCore IV VPU encoding recovered
 * by the Raspberry Pi reverse-engineering community.  The vector ISA is
 * deliberately rejected for now rather than guessed.
'''
COMMENT_NEW = ''' * The scalar decoder follows the public VideoCore IV VPU encoding recovered
 * by the Raspberry Pi reverse-engineering community.  The vector register
 * file remains unimplemented; one exact side-effect-free vector80 delay word
 * used by production bootcode.bin is accepted rather than guessed broadly.
'''
HELPER_ANCHOR = '''static void vc4_tr_init_disas_context(DisasContextBase *dcbase, CPUState *cs)
'''
HELPER = r'''static bool vc4_decode_vector80_delay(uint16_t i1, uint16_t i2,
                                      uint16_t i3, uint16_t i4,
                                      uint16_t i5)
{
    /*
     * Production bootcode.bin uses this exact discard-only vector80 word
     * immediately after writing the DBUS reset command:
     *
     *     05 fc 38 e0 00 04 c0 f3 00 00
     *
     * The public reverse-engineered encoding describes it as a 16-bit vector
     * MOV, REP32, with D mapped to discard, A mapped to unused, and B supplied
     * by an immediate.  SETF, vector predication, accumulator updates, and
     * scalar reductions are disabled, so it has no architectural result and
     * serves only as a hardware-settling delay.
     *
     * Accept only the production word.  The vector register file and every
     * vector instruction with visible state remain deliberately unsupported.
     */
    return i1 == 0xfc05 && i2 == 0xe038 && i3 == 0x0400 &&
           i4 == 0xf3c0 && i5 == 0x0000;
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
        print("Exact VideoCore IV vector80 delay word is already materialized.")
        return 0

    text = replace_once(text, COMMENT_OLD, COMMENT_NEW, "file comment")
    text = replace_once(text, HELPER_ANCHOR, HELPER + HELPER_ANCHOR,
                        "vector80 helper insertion")
    text = replace_once(text, DECL_OLD, DECL_NEW,
                        "translator halfword declaration")
    text = replace_once(text, DISPATCH_OLD, DISPATCH_NEW,
                        "vector instruction dispatch")
    path.write_text(text, encoding="utf-8")
    print("Materialized exact discard-only VideoCore IV vector80 delay word.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
