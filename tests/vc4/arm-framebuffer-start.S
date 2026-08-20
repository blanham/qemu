.section .text.boot, "ax"
.global _start
.type _start, %function
_start:
    msr daifset, #0xf
    /* ARM SEV must ignore the linked non-ARM VPU CPU. */
    sev
    ldr x0, =0x00800000
    mov sp, x0
    bl vc4_framebuffer_main
1:
    wfe
    b 1b
.size _start, . - _start
