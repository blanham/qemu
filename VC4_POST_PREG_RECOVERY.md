# VC4 post-p-register recovery

The BCM2835 multicore-synchronization device, SoC wiring,
AArch64 frontend, and VC4 frontend all built successfully.
The processor-control and multicore-sync register smoke tests
both passed, and the multicore-sync source compiled without
warnings.

## Stock-firmware frontier

- ARM payload signature reached: **false**
- Probe return code: `2`
- Workflow run: `32302128876`
- Diagnostic artifact: `vc4-post-preg-recovery-1` (`9383692766`)
- The 64-byte AArch64 witness was copied into guest RAM at `0x80000`.
- `ARM_CONTROL0`, `ARM_CONTROL1`, `ARM_STATUS`, `ARM_ID`, and `PM_PROC`
  remained zero throughout the 120-second probe.
- All four ARM CPUs remained halted at PC `0x0`.
- The running VC4 CPU stabilized at low PC `0x544`, with no
  unimplemented-access, guest-error, or illegal-instruction diagnostics.

The production multicore-sync slice is validated.  The remaining
ARM-handoff problem is now an exact low-PC firmware frontier at `0x544`,
not a multicore-sync build or MMIO problem.  Continue from a dedicated
trace branch and preserve this recovery point rather than reviving the
removed self-modifying scheduler experiments.
