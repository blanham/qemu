# VC4 heterogeneous Linux/framebuffer control proof

Source commit: `dac6323557f8b3535c7d6bac1c5dbfffc9a1eb01`

Workflow run: `32353720694`

Validated results:

- both AArch64 and VC4 QEMU frontends built;
- processor-control and multicore-sync regressions passed;
- the bare-metal property-mailbox framebuffer witness passed;
- the pinned Raspberry Pi Linux kernel reached `/init`;
- `/init` opened and painted `/dev/fb0`;
- QMP scanout matched the four-quadrant framebuffer witness.
