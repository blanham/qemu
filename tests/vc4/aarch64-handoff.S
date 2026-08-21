/*
 * Freestanding AArch64 payload for the VC4 unchanged-firmware handoff test.
 *
 * start.elf loads this image at 0x80000 and enters it through the normal
 * Raspberry Pi ARM reset path.  The payload records reset state in low RAM
 * and publishes its signature last so the host never observes a partial
 * record as a successful handoff.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

.section .text.boot, "ax", %progbits
.align 7
.global _start
.type _start, %function
_start:
    movz    x9, #0x1000

    mrs     x10, mpidr_el1
    str     x10, [x9, #8]
    mov     x10, sp
    str     x10, [x9, #16]
    str     x0, [x9, #24]
    mrs     x10, CurrentEL
    str     x10, [x9, #32]
    mrs     x10, sctlr_el1
    str     x10, [x9, #40]

    /* 0x5643345f41524d21 == "VC4_ARM!" in big-endian text order. */
    movz    x10, #0x4d21
    movk    x10, #0x4152, lsl #16
    movk    x10, #0x345f, lsl #32
    movk    x10, #0x5643, lsl #48
    dmb     sy
    str     x10, [x9]
    dsb     sy

1:
    wfe
    b       1b
.size _start, . - _start
