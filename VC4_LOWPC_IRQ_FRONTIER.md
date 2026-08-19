# VC4 low-PC interrupt frontier

Stop reason: **`stable-low-pc`**
ARM payload signature: **False**
VPU CPU: `vpu-vc4-cpu` index `4`
Samples: **256** over **1.828 s**

## VPU PC histogram

- `0x0000766a`: 102 sample(s)
- `0x00007670`: 50 sample(s)
- `0x0000768a`: 49 sample(s)
- `0x0000767c`: 37 sample(s)
- `0x00000542`: 14 sample(s)
- `0x000028ec`: 1 sample(s)
- `0x00006b90`: 1 sample(s)
- `0x00006d14`: 1 sample(s)
- `0x00007682`: 1 sample(s)

## Non-zero MMIO snapshot

- `multicore_sync 0x3f000080 = 0xffffffff`
- `vpu_intc0 0x3f002034 = 0x10000000`
- `vpu_intc1 0x3f002834 = 0x10000000`
- `system_timer 0x3f003004 = 0x001b6944`
- `arm_interrupt_controller 0x3f00b21c = 0xffffffff`
- `arm_interrupt_controller 0x3f00b220 = 0xffffffff`
- `arm_interrupt_controller 0x3f00b224 = 0xffffffff`
- `power_management_proc 0x3f100108 = 0x00001000`

## Final CPU state

- CPU `0` `cortex-a53-arm-cpu`: halted=`None`, pc=`0x00000000`
- CPU `1` `cortex-a53-arm-cpu`: halted=`None`, pc=`0x00000000`
- CPU `2` `cortex-a53-arm-cpu`: halted=`None`, pc=`0x00000000`
- CPU `3` `cortex-a53-arm-cpu`: halted=`None`, pc=`0x00000000`
- CPU `4` `vpu-vc4-cpu`: halted=`None`, pc=`0x0000766c`

## Interpretation rule

The next implementation change must follow the captured path: peripheral source → raw GPU line → VPU interrupt-controller raw/enable and status state → VC4 CPU external-interrupt condition.  A zero at an earlier stage rules out speculative fixes at later stages.
