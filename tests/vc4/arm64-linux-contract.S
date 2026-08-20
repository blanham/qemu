/*
 * AArch64 Linux boot-protocol witness for the heterogeneous VC4 machine.
 *
 * Records the architectural entry contract before changing SP or any system
 * state, emits a bounded PL011 marker, and then waits forever.  The host-side
 * probe reads the record through qtest while TCG is still running.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

    .section .text.boot, "ax"
    .align  7
    .global _start
    .type   _start, %function

    .equ RECORD_BASE,          0x00003000
    .equ RECORD_SIGNATURE,     0x00
    .equ RECORD_STAGE,         0x08
    .equ RECORD_X0,            0x10
    .equ RECORD_X1,            0x18
    .equ RECORD_X2,            0x20
    .equ RECORD_X3,            0x28
    .equ RECORD_SP,            0x30
    .equ RECORD_MPIDR,         0x38
    .equ RECORD_CURRENT_EL,    0x40
    .equ RECORD_DAIF,          0x48
    .equ RECORD_SCTLR,         0x50
    .equ RECORD_AUX,           0x58
    .equ RECORD_DTB_WORD,      0x60
    .equ RECORD_FLAGS,         0x68
    .equ RECORD_PC,            0x70

    .equ FLAG_DTB_POINTER,     (1 << 0)
    .equ FLAG_DTB_MAGIC,       (1 << 1)
    .equ FLAG_X1_X3_ZERO,      (1 << 2)
    .equ FLAG_EL1_OR_EL2,      (1 << 3)
    .equ FLAG_MMU_OFF,         (1 << 4)
    .equ FLAG_UART_WRITTEN,    (1 << 5)

    .equ FDT_MAGIC_BE,         0xd00dfeed
    .equ PL011_BASE,           0x3f201000
    .equ PL011_DR,             0x00
    .equ PL011_FR,             0x18
    .equ PL011_FR_TXFF,        (1 << 5)

_start:
    /* Preserve the complete incoming Linux entry contract first. */
    mov     x20, x0
    mov     x21, x1
    mov     x22, x2
    mov     x23, x3
    mov     x24, sp
    adr     x25, _start

    ldr     x9, =RECORD_BASE
    ldr     x10, =0x5643344c434f4e54 /* "VC4LCONT" */
    str     x10, [x9, #RECORD_SIGNATURE]
    mov     x10, #1
    str     x10, [x9, #RECORD_STAGE]
    str     x20, [x9, #RECORD_X0]
    str     x21, [x9, #RECORD_X1]
    str     x22, [x9, #RECORD_X2]
    str     x23, [x9, #RECORD_X3]
    str     x24, [x9, #RECORD_SP]
    str     x25, [x9, #RECORD_PC]

    mrs     x10, mpidr_el1
    str     x10, [x9, #RECORD_MPIDR]
    mrs     x11, CurrentEL
    str     x11, [x9, #RECORD_CURRENT_EL]
    mrs     x12, daif
    str     x12, [x9, #RECORD_DAIF]

    /* Read the active translation-control register without trapping. */
    and     x13, x11, #0xc
    cmp     x13, #0x8
    b.eq    1f
    cmp     x13, #0x4
    b.eq    2f
    mov     x14, xzr
    mov     x15, xzr
    b       3f
1:
    mrs     x14, sctlr_el2
    mrs     x15, hcr_el2
    b       3f
2:
    mrs     x14, sctlr_el1
    mov     x15, xzr
3:
    str     x14, [x9, #RECORD_SCTLR]
    str     x15, [x9, #RECORD_AUX]

    mov     x16, xzr

    /* A nonzero, 8-byte-aligned x0 is the minimum useful DTB contract. */
    cbz     x20, 5f
    tst     x20, #7
    b.ne    5f
    orr     x16, x16, #FLAG_DTB_POINTER
    ldr     w17, [x20]
    rev     w18, w17
    str     x18, [x9, #RECORD_DTB_WORD]
    ldr     w19, =FDT_MAGIC_BE
    cmp     w18, w19
    b.ne    5f
    orr     x16, x16, #FLAG_DTB_MAGIC
5:
    orr     x17, x21, x22
    orr     x17, x17, x23
    cbnz    x17, 6f
    orr     x16, x16, #FLAG_X1_X3_ZERO
6:
    cmp     x13, #0x4
    b.eq    7f
    cmp     x13, #0x8
    b.ne    8f
7:
    orr     x16, x16, #FLAG_EL1_OR_EL2
8:
    tbnz    x14, #0, 9f
    orr     x16, x16, #FLAG_MMU_OFF
9:
    str     x16, [x9, #RECORD_FLAGS]
    dsb     sy

    /* Use a private stack only after all incoming state has been captured. */
    ldr     x0, =0x00070000
    mov     sp, x0

    adr     x0, contract_message
    bl      bounded_puts
    cbz     x0, 10f
    ldr     x9, =RECORD_BASE
    ldr     x16, [x9, #RECORD_FLAGS]
    orr     x16, x16, #FLAG_UART_WRITTEN
    str     x16, [x9, #RECORD_FLAGS]
10:
    ldr     x9, =RECORD_BASE
    mov     x10, #2
    str     x10, [x9, #RECORD_STAGE]
    dsb     sy
    sev
11:
    wfe
    b       11b

/*
 * Write a NUL-terminated string with bounded polling.  Returns x0=1 when all
 * bytes were accepted, or zero if PL011 remained full for too long.
 */
bounded_puts:
    mov     x3, x0
    ldr     x4, =PL011_BASE
12:
    ldrb    w1, [x3], #1
    cbz     w1, 16f
    ldr     x2, =0x00100000
13:
    ldr     w0, [x4, #PL011_FR]
    tbz     w0, #5, 14f
    subs    x2, x2, #1
    b.ne    13b
    mov     x0, xzr
    ret
14:
    str     w1, [x4, #PL011_DR]
    b       12b
16:
    mov     x0, #1
    ret

    .align 3
contract_message:
    .asciz  "VC4_ARM64_CONTRACT_REACHED\n"

    .size _start, . - _start
