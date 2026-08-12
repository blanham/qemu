#!/usr/bin/env python3
"""Materialize the exact side-effect-free VideoCore IV vector48 delay idiom."""

from __future__ import annotations

from pathlib import Path

MARKER = "Production bootcode.bin follows the vector80 delay with this exact vector48 word."
COMMENT_OLD = ''' * file remains unimplemented; one exact side-effect-free vector80 delay word
 * used by production bootcode.bin is accepted rather than guessed broadly.
'''
COMMENT_NEW = ''' * file remains unimplemented; exact side-effect-free vector80 and vector48
 * delay words used by production bootcode.bin are accepted rather than guessed.
'''
HELPER_ANCHOR = '''static bool vc4_decode_vector80_delay(uint16_t i1, uint16_t i2,
'''
HELPER = r'''static bool vc4_decode_vector48_delay(uint16_t i1, uint16_t i2,
                                      uint16_t i3)
{
    /*
     * Production bootcode.bin follows the vector80 delay with this exact
     * vector48 word:
     *
     *     00 f4 38 e0 00 04
     *
     * The public reverse-engineered encoding describes it as a 16-bit vector
     * MOV with D mapped to discard, A mapped to unused, and B equal to the
     * immediate zero.  SETF and scalar side effects are disabled, so the word
     * has no architectural result and serves only as a shorter settling delay.
     *
     * Accept only the production word.  Every vector48 instruction that can
     * expose vector, flag, or scalar state remains deliberately unsupported.
     */
    return i1 == 0xf400 && i2 == 0xe038 && i3 == 0x0400;
}

'''
DISPATCH_OLD = '''    } else {
        /* Vector48 remains separate work. */
        ctx->base.pc_next = ctx->pc + 6;
        decoded = false;
    }
'''
DISPATCH_NEW = '''    } else {
        i2 = vc4_lduw(ctx, ctx->pc + 2);
        i3 = vc4_lduw(ctx, ctx->pc + 4);
        ctx->base.pc_next = ctx->pc + 6;
        decoded = vc4_decode_vector48_delay(i1, i2, i3);
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
        print("Exact VideoCore IV vector48 delay word is already materialized.")
        return 0

    if "Production bootcode.bin uses this exact discard-only vector80 word." not in text:
        raise RuntimeError("vector80 delay support must be materialized first")

    text = replace_once(text, COMMENT_OLD, COMMENT_NEW, "file comment")
    text = replace_once(text, HELPER_ANCHOR, HELPER + HELPER_ANCHOR,
                        "vector48 helper insertion")
    text = replace_once(text, DISPATCH_OLD, DISPATCH_NEW,
                        "vector48 instruction dispatch")
    path.write_text(text, encoding="utf-8")
    print("Materialized exact discard-only VideoCore IV vector48 delay word.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
