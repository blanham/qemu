/*
 * Diagnostic AArch64 trampoline for the heterogeneous VC4 machine.
 *
 * QEMU loads this image through the machine's normal direct-ARM path while
 * the real Linux Image is placed at 0x00280000 (2 MiB base + 512 KiB
 * text_offset).  The trampoline proves ARM entry and PL011 output, preserves
 * the DTB pointer in x0, and then transfers control to Linux.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

        .section .text
        .align  7
        .global _start
        .type   _start, %function
_start:
        mov     x19, x0
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
        ldr     x1, =0x1020
        ldr     x2, =0x5643345f4a554d50
        str     x2, [x1]
        str     x19, [x1, #8]
        mrs     x2, CurrentEL
        str     x2, [x1, #16]
        dsb     sy
        ic      iallu
        dsb     sy
        isb

        mov     x0, x19
        mov     x1, xzr
        mov     x2, xzr
        mov     x3, xzr
        ldr     x16, =0x00280000
        br      x16

        .align  3
message:
        .asciz  "VC4_ARM_LINUX_JUMP\n"
        .size   _start, . - _start
