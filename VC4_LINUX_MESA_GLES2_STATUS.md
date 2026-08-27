# VC4 Linux Mesa GLES2 frontier

Validation passed: **false**

Harness valid: **true**

Frontier: **`vc4-v3d-unsupported-gl-array-primitive-0x21`**

- Module closure preserved: `True`
- Handwritten DRM submit preserved: `True`
- Mesa process started: `True`
- VC4 hardware frontier reached: `True`
- Last stage: `VC4_LINUX_MESA_GLES2_FINISH_START`
- Next missing stage: `VC4_LINUX_MESA_GLES2_FINISH_OK`
- Renderer: `VC4 V3D 2.1`
- GL version: `OpenGL ES 2.0 Mesa 24.0.2`
- Timed out: `True`
- Child exit: `124`
- Child signal: `None`
- Probe return code: `0`

## First unsupported V3D packet

- Opcode: `0x21`
- Name: `gl-array-primitive`
- Command-list address: `0xc3aba050`

This gate runs a pinned Mesa VC4 Gallium driver inside the AArch64 guest. It requires a hardware VC4 renderer, compiles real GLES2 shaders, queues a full-surface triangle, waits for GPU completion, and verifies readback pixels. A non-clear classification is therefore the next concrete V3D/QPU contract rather than a synthetic packet guess.
