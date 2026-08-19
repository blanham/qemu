# VC4 post-p-register recovery

The BCM2835 multicore-synchronization device, SoC wiring,
AArch64 frontend, and VC4 frontend all built successfully.
The processor-control and multicore-sync register smoke tests
both passed, and the multicore-sync source compiled without
warnings.

## Stock-firmware frontier

- ARM payload signature reached: **False**
- Probe return code: `2`
- Workflow run: `https://github.com/blanham/qemu/actions/runs/32302128876`

The production multicore-sync slice is validated; the
remaining ARM-handoff frontier is recorded in the JSON
report and the workflow artifact rather than guessed at
by another self-modifying diagnostic workflow.
