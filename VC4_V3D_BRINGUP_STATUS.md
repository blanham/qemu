# VC4 V3D bring-up

Validation passed: **false**

- Dual AArch64/VC4 build: `False`
- Processor-control and multicore-sync regressions: `False`
- V3D identity, IRQ, CT1 and clear/store witness: `False`
- Bare framebuffer regression: `False`
- Linux framebuffer regression: `False`

Primitive binning and QPU shader execution remain behind the explicit CTERR/ERRSTAT boundary.
