#!/usr/bin/env python3
"""Materialize scalar48 PC-relative VideoCore IV load/store decoding."""

from __future__ import annotations

from pathlib import Path

MARKER = "48-bit PC-relative load/store uses short0, short2, short1"
ANCHOR = """    if ((i1 & 0xfc00) == 0xec00) {
"""
BLOCK = """    /*
     * 48-bit PC-relative load/store uses short0, short2, short1 physical
     * halfword order.  i2 therefore holds offset bits 15:0 while i3 holds
     * the fixed PC selector in bits 15:11 and offset bits 26:16.
     */
    if ((i1 & 0xff00) == 0xe700 && (i3 & 0xf800) == 0xf800) {
        bool store = (i1 & 0x20) != 0;

        format = (i1 >> 6) & 3;
        rd = i1 & 0x1f;
        raw = i2 | ((uint32_t)(i3 & 0x7ff) << 16);
        offset = vc4_sext(raw, 0x04000000);
        vc4_gen_load_store_offset(ctx, 14, store, format, rd,
                                  VC4_REG_PC, offset, false, false);
        return true;
    }

"""


def main() -> int:
    path = Path("target/vc4/translate.c")
    text = path.read_text(encoding="utf-8")

    if MARKER in text:
        print("VideoCore IV scalar48 PC-relative memory is already materialized.")
        return 0

    count = text.count(ANCHOR)
    if count != 1:
        raise RuntimeError(
            f"{path}: expected one scalar48 insertion anchor, found {count}"
        )

    path.write_text(text.replace(ANCHOR, BLOCK + ANCHOR), encoding="utf-8")
    print("Materialized VideoCore IV scalar48 PC-relative load/store.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
