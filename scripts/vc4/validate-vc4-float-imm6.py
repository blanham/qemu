#!/usr/bin/env python3
"""Independent host validation for the VC4 float-immediate repair."""

from __future__ import annotations

import math
import struct


def implementation_bits(imm: int) -> int:
    if not 0 <= imm < 64:
        raise ValueError(imm)

    bits = (imm & 0x20) << 26
    exponent = (imm >> 2) & 0x7
    if exponent != 0:
        bits |= (exponent + 124) << 23
        bits |= (imm & 0x3) << 21
    return bits


def reference_bits(imm: int) -> int:
    if not 0 <= imm < 64:
        raise ValueError(imm)

    negative = bool(imm & 0x20)
    exponent = (imm >> 2) & 0x7
    if exponent == 0:
        value = math.copysign(0.0, -1.0 if negative else 1.0)
    else:
        significand = 1.0 + (imm & 0x3) / 4.0
        value = math.ldexp(significand, exponent - 3)
        if negative:
            value = -value
    return struct.unpack("<I", struct.pack("<f", value))[0]


def decode_instruction(i1: int, i2: int) -> tuple[int, int, int, int]:
    if (i1 & 0xFE00) != 0xC800 or not (i2 & 0x40):
        raise ValueError("not a scalar floating-immediate instruction")
    op = (i1 >> 5) & 0xF
    rd = i1 & 0x1F
    ra = (i2 >> 11) & 0x1F
    imm = i2 & 0x3F
    return op, rd, ra, imm


def main() -> int:
    mismatches = [
        imm for imm in range(64)
        if implementation_bits(imm) != reference_bits(imm)
    ]
    if mismatches:
        raise SystemExit(f"mapping mismatches: {mismatches}")

    if {implementation_bits(i) for i in range(4)} != {0x00000000}:
        raise SystemExit("positive exponent-zero aliases are not +0.0")
    if {implementation_bits(i) for i in range(32, 36)} != {0x80000000}:
        raise SystemExit("negative exponent-zero aliases are not -0.0")

    firmware_cases = {
        (0xC800, 0x0748): (0, 0, 0, 8),  # fadd r0, r0, #0.5
        (0xC844, 0x2748): (2, 4, 4, 8),  # fmul r4, r4, #0.5
        (0xC805, 0x2F48): (0, 5, 5, 8),  # fadd r5, r5, #0.5
    }
    for insn, expected in firmware_cases.items():
        actual = decode_instruction(*insn)
        if actual != expected:
            raise SystemExit(
                f"decode mismatch for {insn}: {actual} != {expected}"
            )

    # The first firmware case is immediately followed by floor conversion.
    # That x + 0.5; floor(x) sequence independently witnesses #8 == 0.5.
    round_sequence = (0xC800, 0x0748, 0xCA23, 0x0740)
    if decode_instruction(*round_sequence[:2])[-1] != 8:
        raise SystemExit("firmware round-to-nearest sequence changed")
    if reference_bits(8) != 0x3F000000:
        raise SystemExit("firmware immediate #8 is not 0.5f")

    print(
        "VC4 float imm6 reference passed: "
        "encodings=64 signed-zero-aliases=8 firmware-opcodes=3 "
        "imm8=0x3f000000 imm40=0xbf000000"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
