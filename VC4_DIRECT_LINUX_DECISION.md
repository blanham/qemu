# VC4 direct Linux decision

- Direct Linux plus QMP framebuffer scanout: `false`
- AArch64 Linux entry contract: `true`
- Classification: `unknown`
- Eligible for the stock-firmware continuation branch: `false`

The direct control bypasses only the stock VideoCore firmware
handoff.  A passing result proves the ARM CPU, Linux image/DTB,
initramfs, property mailbox, framebuffer memory, and QEMU display
scanout work together.  It does not by itself prove the stock
`bootcode.bin -> start.elf` path.
