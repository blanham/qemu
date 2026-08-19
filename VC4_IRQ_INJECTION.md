# VC4 interrupt delivery injection probe

Classification: **`raw-source-or-edge-delivery`**
Baseline low-PC reached: **True**
Baseline stable PC: `0x0000766a`
VPU interrupt controller: `{'path': '/machine/vpu-intc0', 'type': 'bcm2835-vc4-intc'}`
VPU CPU object: `{'path': '/machine/unattached/device[2]', 'type': 'vpu-vc4-cpu'}`

## VPU interrupt-controller input injections

- line `0` via `gpu-irq`: changed_pc=`True`, signature=`False`, before=`0x0000768a`, after=`0x0000766a`

## Direct VC4 CPU input injection

- changed_pc=`None`, signature=`None`, GPIO=`None`, before=`None`, after=`None`

## Decision rule

A controller-input injection that moves the VPU proves the CPU and controller path can deliver an interrupt and points upstream at the raw peripheral source/mirror.  A direct CPU injection that moves the VPU while controller injection does not points at controller register or mask semantics.  Neither candidate is retained by this probe.
