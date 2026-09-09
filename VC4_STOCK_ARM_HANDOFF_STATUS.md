# VC4 stock-firmware AArch64 handoff

Validation passed: **false**

Frontier: **`stock-handoff-probe-error`**

- Virtual clock recovered: `False`
- qtest clock steps: `0`
- Virtual time advanced: `0` ns
- BCM2835 system timer advanced: `0` us
- ARM witness signature: `False`
- Observed signature: `None`
- Kernel word after firmware: `None`
- Firmware x0: `None`
- Initial SP: `None`
- MPIDR_EL1: `None`
- ARM_CONTROL0: `None`
- ARM_CONTROL1: `None`
- ARM_STATUS: `None`
- PM_PROC: `None`
- Final VPU PC: `None`
- Last unimplemented opcode: `None` at `None`
- Last illegal PC: `None`
- Probe return code: `2`

## Probe error

`RuntimeError: unexpected qtest reply: "FAIL Unknown command 'clock_step'"`
