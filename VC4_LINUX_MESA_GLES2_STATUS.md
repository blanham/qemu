# VC4 Linux Mesa GLES2 frontier

Validation passed: **false**

Harness valid: **false**

Frontier: **`workflow-mesa_root-modules-initramfs-dtb-build-regressions-runtime-failed`**

- Module closure preserved: `False`
- Handwritten DRM submit preserved: `False`
- Mesa process started: `False`
- VC4 hardware frontier reached: `False`
- Last stage: `None`
- Next missing stage: `VC4_LINUX_MESA_GLES2_SUPERVISOR_START`
- Renderer: `None`
- GL version: `None`
- Timed out: `False`
- Child exit: `None`
- Child signal: `None`
- Probe return code: `None`

This gate runs a pinned Mesa VC4 Gallium driver inside the AArch64 guest. It requires a hardware VC4 renderer, compiles real GLES2 shaders, queues a full-surface triangle, waits for GPU completion, and verifies readback pixels. A non-clear classification is therefore the next concrete V3D/QPU contract rather than a synthetic packet guess.
