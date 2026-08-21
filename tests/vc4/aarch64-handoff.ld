/* SPDX-License-Identifier: GPL-2.0-or-later */
ENTRY(_start)

SECTIONS
{
    . = 0x00080000;

    .text : ALIGN(128) {
        KEEP(*(.text.boot))
        *(.text .text.*)
    }

    .rodata : ALIGN(16) {
        *(.rodata .rodata.*)
    }

    .data : ALIGN(16) {
        *(.data .data.*)
    }

    .bss (NOLOAD) : ALIGN(16) {
        *(.bss .bss.* COMMON)
    }

    /DISCARD/ : {
        *(.comment)
        *(.note .note.*)
        *(.eh_frame .eh_frame.*)
    }
}
