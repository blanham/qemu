# VC4 stock-firmware unobserved execution control

Classification: **`stock-vpu-low-pc-persists-without-observer`**

- Probe error: `None`
- Quiet window requested: `30.0` s
- Quiet window elapsed: `30.000136422999958` s
- QMP connected before window: `False`
- Samples during window: `0`
- QEMU alive after window: `True`
- VPU PC after window: `0x00000544`
- A53 PCs after window: `['0x00000000', '0x00000000', '0x00000000', '0x00000000']`
- ARM witness reached: `False`
- System timer after window: `29974282` us
- ARM_CONTROL0: `0x00000000`
- ARM_CONTROL1: `0x00000000`
- ARM_STATUS: `0x00000000`
- PM_PROC: `0x00000000`

The primary quiet window contains no QMP connection, HMP register
query, physical-memory read, qtest clock operation, or periodic
sampler. The first guest-state observation occurs only after the
window has elapsed.
