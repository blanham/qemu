# VC4 Linux and framebuffer bring-up

Classification: **`stock-firmware-handoff`**

## Gates

- Stock firmware entered ARM payload: **False**
- Bare-metal mailbox framebuffer and QMP scanout: **False**
- Linux banner observed: **False**
- Linux `/init` observed: **False**
- Linux `/dev/fb0` painter completed: **False**
- Linux QMP quadrant scanout matched: **False**

## Exact next frontier

Continue from the post-clock-step VPU/ARM register frontier; do not diagnose Linux or framebuffer yet.
