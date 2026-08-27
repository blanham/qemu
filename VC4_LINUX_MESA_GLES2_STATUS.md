# VC4 Linux Mesa GLES2 frontier

Validation passed: **false**

Harness valid: **false**

Frontier: **`vc4-mesa-gles2-not-reached`**

- Module closure preserved: `True`
- Handwritten DRM submit preserved: `True`
- Mesa process started: `False`
- VC4 hardware frontier reached: `False`
- Last stage: `VC4_LINUX_MESA_GLES2_SUPERVISOR_START`
- Next missing stage: `VC4_LINUX_MESA_GLES2_START`
- Renderer: `None`
- GL version: `None`
- Timed out: `False`
- Child exit: `124`
- Child signal: `None`
- Probe return code: `0`

## First unsupported V3D packet

- Opcode: `0x21`
- Name: `gl-array-primitive`
- Command-list address: `0xc8170050`

This gate runs a pinned Mesa VC4 Gallium driver inside the AArch64 guest. It requires a hardware VC4 renderer, compiles real GLES2 shaders, queues a full-surface triangle, waits for GPU completion, and verifies readback pixels. A non-clear classification is therefore the next concrete V3D/QPU contract rather than a synthetic packet guess.
