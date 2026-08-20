# VC4 heterogeneous Linux framebuffer milestone

The heterogeneous Raspberry Pi 3 machine has passed the complete
direct-ARM Linux framebuffer evidence chain:

- both the AArch64 and VC4 QEMU frontends build together;
- the VC4 processor-control and BCM2835 multicore-sync regressions pass;
- the bare-metal ARM property-mailbox witness allocates and paints a
  640x480x32 framebuffer;
- a pinned Raspberry Pi AArch64 Linux kernel reaches the deterministic
  initramfs `/init`;
- `/init` opens and maps `/dev/fb0`, paints four deterministic quadrants,
  and emits the success marker;
- guest metadata, qtest framebuffer-RAM reads, and QMP `screendump`
  agree on the displayed pixels.

The optional direct-ARM control remains intentionally separate from
the stock `bootcode.bin -> start.elf -> kernel8.img` path.  It is a
validated Linux development lane, not a substitute for completing the
stock firmware handoff.

## Next graphics boundary

Accelerated 3D work should begin with narrow V3D witnesses rather than
a desktop compositor:

1. enumerate V3D identity, status, interrupt, and MMU registers;
2. validate firmware/property power and clock control for V3D;
3. submit a minimal control list that clears a tile buffer;
4. validate binning and rendering completion interrupts;
5. run a single-triangle test through the Linux DRM/V3D ABI;
6. only then attempt Mesa `v3d`/`vc4` acceleration and KMS composition.

Each stage must retain a guest-memory witness and an independently
captured QEMU scanout or rendered-buffer comparison.
