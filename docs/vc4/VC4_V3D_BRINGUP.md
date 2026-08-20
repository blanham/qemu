# VideoCore IV V3D 2.1 bring-up

Raspberry Pi 3 exposes the VideoCore IV-era V3D 2.1 block. Linux
drives it through the `vc4` DRM driver's `vc4_v3d` component, not
through the newer `v3d` DRM driver used by later V3D generations.

This branch is created only after a pinned Raspberry Pi Linux kernel
has booted through stock firmware and the `/dev/fb0` four-quadrant
witness has been verified in QMP scanout.

## Acceptance ladder

1. Map the BCM2835/BCM2837 V3D register aperture and model reset,
   power-domain visibility, identification, scratch, and interrupt
   registers.
2. Add qtest coverage for reset values, writable masks, W1C behavior,
   and interrupt enable/disable edges.
3. Make Linux's `vc4_v3d` component bind without probe-time register,
   power, or interrupt errors. Preserve the complete kernel log as a
   test artifact.
4. Implement binning/render control-thread state and the minimum BO,
   MMU, cache, and interrupt semantics needed by `DRM_IOCTL_VC4_SUBMIT_CL`.
5. Add a tiny deterministic userspace witness that creates a VC4 BO,
   submits one binning/render control list, and verifies rendered
   pixels through both guest memory and QMP scanout.
6. Establish a deterministic software/reference backend for CI and
   differential tests.
7. Add an accelerated host backend behind the same validated command
   boundary. Host acceleration must never substitute for missing
   guest-visible register, MMU, cache, synchronization, or fault
   semantics.
8. Run Mesa VC4 GBM/EGL proofs (`kmscube` first), compare images with
   the reference backend, then expand to piglit/deqp coverage.

## First implementation slice

The first code slice is deliberately small: identification and
scratch registers, interrupt state, power/reset integration, qtest
coverage, and a Linux `vc4_v3d` bind probe. No command execution is
claimed until a submitted control list produces verified pixels.
