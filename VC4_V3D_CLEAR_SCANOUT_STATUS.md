# VC4 V3D clear scanout

Validation passed: **false**

- Dual AArch64/VC4 build: `False`
- Processor-control, multicore-sync, and V3D MMIO regressions: `False`
- V3D central-tile clear visible in QMP scanout: `False`
- Pinned Linux framebuffer regression: `False`

The control-list clear path is hardware accelerated inside the emulated V3D block. Primitive binning and QPU shader execution remain the next frontier.
