/*
 * Direct ARM entry witness for the heterogeneous VC4/Raspberry Pi machine.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

        .section .text
        .align  7
        .global _start
        .type   _start, %function
_start:
        mov     x19, x0

        /* Configure the BCM2835 PL011 sufficiently for a QEMU chardev. */
        ldr     x20, =0x3f201000
        str     wzr, [x20, #0x30]
        mov     w0, #0x7ff
        str     w0, [x20, #0x44]
        mov     w0, #1
        str     w0, [x20, #0x24]
        mov     w0, #40
        str     w0, [x20, #0x28]
        mov     w0, #(3 << 5)
        str     w0, [x20, #0x2c]
        mov     w0, #0x301
        str     w0, [x20, #0x30]

        adr     x21, message
1:
        ldrb    w0, [x21], #1
        cbz     w0, 3f
2:
        ldr     w1, [x20, #0x18]
        tbnz    w1, #5, 2b
        str     w0, [x20]
        b       1b

3:
        ldr     x0, =0x1000
        ldr     x1, =0x5643345f44495245
        str     x1, [x0]
        str     x19, [x0, #8]
        mrs     x1, CurrentEL
        str     x1, [x0, #16]
        mrs     x1, mpidr_el1
        str     x1, [x0, #24]
        dsb     sy
4:
        wfe
        b       4b

        .align  3
message:
        .asciz  "VC4_ARM_DIRECT_ENTRY\n"
        .size   _start, . - _start
