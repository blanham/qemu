# VC4/AArch64 Raspberry Pi integration status

## Validated Linux DRM boundary

The pinned Raspberry Pi Linux path is now validated end to end on the
`raspi3b` machine using the real VC4 DRM driver and its matching module
dependency closure.

Validated at source commit `e9d2a607ff0ccf728e6a11c3fbf917dca7635010` and
recorded by `e066235e9f1eb554d0d37c0900defbe3497d1475`:

- the AArch64 kernel boots with all four Cortex-A53 CPUs;
- the firmware mailbox and power-domain requests complete;
- HVS, the headless TXP CRTC, and V3D bind to the VC4 DRM master;
- `/dev/dri` exposes a VC4 node accepted by the witness;
- `GET_PARAM` returns the expected V3D identity registers;
- VC4 GEM buffer creation, mapping, and coherent CPU access work;
- a clear-only `DRM_IOCTL_VC4_SUBMIT_CL` job is accepted;
- `DRM_IOCTL_VC4_WAIT_BO` completes; and
- every pixel in the destination BO is verified as the submitted clear color.

The minimum valid headless Linux component topology is therefore
`VC4 master + HVS + TXP + V3D`.  HVS plus V3D alone is invalid for the
pinned driver because plane creation requires an existing TXP CRTC.

## Durable regression witness

The module-aware witness now emits fixed markers for every successful DRM
stage and requests a guest reboot after success.  With QEMU's `-no-reboot`
option this terminates the probe immediately instead of consuming its full
timeout.  Its summarizer treats the final success markers as transitive proof
for older logs that lost formatted diagnostics while still requiring the
complete module, UAPI, submit, wait, and pixel-verification chain.

`VC4_LINUX_V3D_WITNESS_CLEANUP_STATUS.json` records successful Python
regression tests, marker checks, repository hygiene, and a static AArch64
`-Wall -Wextra -Werror` build of the witness.

## Retired experiments

The original driver-probe, direct-UAPI, kernel-boundary, and submit workflows
used the stock kernel without its out-of-tree VC4 module closure.  They could
not reach the now-validated driver path and repeatedly overwrote historical
status files with expected failures.  Their dispatch and isolated-monitor
helpers also targeted completed or deleted one-shot workflows.  These Actions
are retired; their status files remain historical evidence rather than the
current project frontier.

## Next implementation frontiers

1. Carry stock `bootcode.bin`/`start.elf` execution through the real ARM
   handoff and into the same validated Linux payload path.
2. Replace the headless TXP accommodation and remaining display MMIO stubs
   with sufficient HVS, pixel-valve, TXP, HDMI, and interrupt behavior for
   the full KMS topology.
3. Expand V3D from the validated clear-only render control list to binning,
   shader records, QPU execution, additional packet forms, error semantics,
   and asynchronous scheduling.
4. Consolidate the many completed one-shot probes into a small permanent
   regression suite for the VC4 CPU frontend, heterogeneous scheduling,
   firmware boot, ARM handoff, Linux DRM, storage, USB, and display paths.
