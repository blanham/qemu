/*
 * Normalize an AArch64 direct-boot CPU into a Linux-valid entry state.
 *
 * The real kernel entry address is supplied at link time as LINUX_ENTRY.
 * x0 (the DTB pointer) is preserved; x1-x3 are cleared per the arm64 boot
 * protocol.  The code accepts EL3, EL2, or EL1 so the diagnostic remains
 * useful while the heterogeneous machine's reset contract is being refined.
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#ifndef LINUX_ENTRY
#define LINUX_ENTRY 0x00280000
#endif

        .section .text
        .align  7
        .global _start
        .type   _start, %function
_start:
        mov     x19, x0
        mrs     x22, CurrentEL
        bl      uart_init
        adr     x0, marker_start
        bl      uart_puts

        ldr     x0, =0x1040
        ldr     x1, =0x5643345f4e4f524d
        str     x1, [x0]
        str     x19, [x0, #8]
        str     x22, [x0, #16]
        dsb     sy

        cmp     x22, #0xc
        b.eq    enter_from_el3
        cmp     x22, #0x8
        b.eq    prepare_el2
        cmp     x22, #0x4
        b.eq    prepare_el1
        adr     x0, marker_bad_el
        bl      uart_puts
1:
        wfe
        b       1b

enter_from_el3:
        /* Non-secure AArch64 EL2 with HVC enabled. */
        mov     x0, #(1 << 10)
        orr     x0, x0, #(1 << 8)
        orr     x0, x0, #1
        msr     scr_el3, x0
        mov     x0, #0x3c9             /* DAIF masked, EL2h */
        msr     spsr_el3, x0
        adr     x0, prepare_el2
        msr     elr_el3, x0
        eret

prepare_el2:
        mov     x0, #(1 << 31)         /* EL1 executes AArch64 */
        msr     hcr_el2, x0
        mov     x0, #3                 /* EL1 physical timer/counter access */
        msr     cnthctl_el2, x0
        msr     cntvoff_el2, xzr
        msr     cptr_el2, xzr
        mrs     x0, sctlr_el2
        bic     x0, x0, #1             /* M */
        bic     x0, x0, #(1 << 2)      /* C */
        bic     x0, x0, #(1 << 12)     /* I */
        msr     sctlr_el2, x0
        isb
        b       jump_linux

prepare_el1:
        mrs     x0, sctlr_el1
        bic     x0, x0, #1
        bic     x0, x0, #(1 << 2)
        bic     x0, x0, #(1 << 12)
        msr     sctlr_el1, x0
        isb

jump_linux:
        ic      iallu
        dsb     sy
        isb
        adr     x0, marker_jump
        bl      uart_puts
        mrs     x0, CurrentEL
        ldr     x1, =0x1058
        str     x0, [x1]
        dsb     sy

        mov     x0, x19
        mov     x1, xzr
        mov     x2, xzr
        mov     x3, xzr
        ldr     x16, =LINUX_ENTRY
        br      x16

uart_init:
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
        ret

uart_puts:
        mov     x21, x0
2:
        ldrb    w0, [x21], #1
        cbz     w0, 4f
3:
        ldr     w1, [x20, #0x18]
        tbnz    w1, #5, 3b
        str     w0, [x20]
        b       2b
4:
        ret

        .align  3
marker_start:
        .asciz  "VC4_ARM_NORMALIZER_START\n"
marker_jump:
        .asciz  "VC4_ARM_NORMALIZER_JUMP\n"
marker_bad_el:
        .asciz  "VC4_ARM_NORMALIZER_BAD_EL\n"
        .size   _start, . - _start
