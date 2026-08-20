/*
 * Minimal libc-compatible memory primitives for the freestanding Linux /init.
 *
 * GCC may lower structure zero-initialization and block copies to these
 * symbols even with -ffreestanding and -fno-builtin.  Keep the implementations
 * deliberately simple; this payload runs once during bring-up and correctness
 * is more valuable than tuned cache-line loops.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

    .section .text, "ax"
    .align  2

    .global memset
    .type   memset, %function
memset:
    mov     x3, x0
    cbz     x2, 2f
1:
    strb    w1, [x3], #1
    subs    x2, x2, #1
    b.ne    1b
2:
    ret
    .size   memset, . - memset

    .global memcpy
    .type   memcpy, %function
memcpy:
    mov     x3, x0
    cbz     x2, 2f
1:
    ldrb    w4, [x1], #1
    strb    w4, [x3], #1
    subs    x2, x2, #1
    b.ne    1b
2:
    ret
    .size   memcpy, . - memcpy

    .global memmove
    .type   memmove, %function
memmove:
    mov     x3, x0
    cbz     x2, 4f
    cmp     x0, x1
    b.ls    2f
    add     x1, x1, x2
    add     x3, x3, x2
1:
    ldrb    w4, [x1, #-1]!
    strb    w4, [x3, #-1]!
    subs    x2, x2, #1
    b.ne    1b
    b       4f
2:
    ldrb    w4, [x1], #1
    strb    w4, [x3], #1
    subs    x2, x2, #1
    b.ne    2b
4:
    ret
    .size   memmove, . - memmove
