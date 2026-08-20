# VC4 V3D bring-up

Validation passed: **true**

- Dual AArch64/VC4 build: `True`
- Processor-control and multicore-sync regressions: `True`
- V3D identity, IRQ, CT1 and clear/store witness: `True`
- Bare framebuffer regression: `True`
- Linux framebuffer regression: `True`

Primitive binning and QPU shader execution intentionally remain behind a CTERR/ERRSTAT boundary.
