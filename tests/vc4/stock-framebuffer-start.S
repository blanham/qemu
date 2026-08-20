/*
 * Minimal AArch64 entry point for the stock-firmware framebuffer witness.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

    .section .text.boot, "ax"
    .align  7
    .global _start
    .type   _start, %function

_start:
    /* Preserve the firmware-provided DTB pointer across BSS setup. */
    mov     x19, x0

    /* Use ordinary ARM RAM, well below the VideoCore-reserved top 64 MiB. */
    ldr     x0, =0x01000000
    mov     sp, x0

    ldr     x0, =__bss_start
    ldr     x1, =__bss_end
1:
    cmp     x0, x1
    b.hs    2f
    str     xzr, [x0], #8
    b       1b

2:
    mov     x0, x19
    bl      vc4_framebuffer_main

3:
    wfe
    b       3b

    .size _start, . - _start
