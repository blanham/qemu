# VC4 V3D RGBA8888 tiling checkpoint

Validation passed: **true**

- Linear, LT, and T RGBA8888 address translation passed.
- T-tile odd-row reversal and subtile ordering passed.
- CT1 stores completed for T and LT render targets.
- Existing QPU, primitive-pipeline, and linear-render gates passed.
- Both AArch64/VC4 QEMU frontends built successfully.

This checkpoint removes the measured `config=0x0044` render-store failure. It does not claim the later Mesa TMU/readback resolve is implemented; the real Mesa workflow determines that frontier.
