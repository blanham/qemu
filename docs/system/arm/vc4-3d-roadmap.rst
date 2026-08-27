VC4 and V3D 3D acceleration roadmap
====================================

Purpose
-------

The Raspberry Pi display work has crossed the native Linux KMS boundary: the
guest can bind VC4, modeset HDMI, page flip, perform complete atomic modesets,
and preserve the renderer submit witness.  The next objective is real
userspace 3D acceleration rather than additional synthetic command lists.

There are two related, but distinct, hardware targets:

``VC4 / V3D 2.x``
    Raspberry Pi 0 through 3.  Mesa's ``vc4`` Gallium driver is the primary
    userspace target.  The hardware's practical API goal is OpenGL ES 2.0 and
    the desktop OpenGL level exposed by that driver.

``V3D 4.x+``
    Raspberry Pi 4 and later generations.  Mesa's ``v3d`` Gallium driver and
    ``v3dv`` Vulkan driver require a separate BCM2711/V3D-generation model.
    Vulkan must not be faked by advertising Pi 4 capabilities on the Pi 3
    machine.

The software model remains the architectural reference.  A later optional
host-accelerated backend may translate validated jobs to host OpenGL or Vulkan,
but it must produce the same guest-visible register, interrupt, memory, and
synchronization behavior as the reference implementation.

Current boundary
----------------

The BCM2835 V3D model currently provides:

* architected identity, control-thread, cache, interrupt, and debug registers;
* synchronous CT0 and CT1 command-list execution;
* render-list clear and linear RGBA8888 tile stores;
* Linux VC4 DRM buffer allocation, mapping, submission, waiting, and pixel
  verification;
* explicit failure for primitive packets that require binning or QPU
  execution.

Treating shader-state and configuration packets as harmless until a primitive
appears is sufficient for the clear witness, but not for Mesa.  The first real
GLES2 draw is expected to cross that boundary.

Measured Mesa frontier
----------------------

``agent/vc4-linux-mesa-gles2-frontier`` builds a pinned AArch64 Mesa userspace
with only the VC4 Gallium driver, packages its dynamic-library closure into the
existing pinned Linux initramfs, and executes a surfaceless GLES2 program.

The program requires all of the following:

#. the renderer string names VC4, ruling out software fallback;
#. vertex and fragment shader compilation;
#. program linking;
#. a full-surface triangle draw;
#. ``glFinish()`` completion;
#. exact-color ``glReadPixels()`` verification.

The workflow records the final Mesa stage and the first unsupported V3D packet
reported by QEMU.  Hardware implementation work should advance this gate one
measured contract at a time.

VC4 execution milestones
------------------------

1. **Command-list control flow**

   Implement bounded branch and sub-list execution, semaphore behavior, and
   binning-memory setup.  Preserve loop limits, DMA validation, and CTERR on
   malformed lists.

2. **Shader records and relocations**

   Parse the validated shader-state records emitted by the Linux VC4 driver.
   Resolve attribute, uniform, shader-code, texture, and tile-buffer addresses
   from guest memory without bypassing the kernel's validation model.

3. **QPU scalar and vector core**

   Add a separately testable VideoCore IV QPU engine.  Initial coverage should
   include register files, pack/unpack, add and multiply ALUs, small
   immediates, conditions, signals, branches and delay slots, and uniform
   reads.  Every new instruction class needs direct ISA tests independent of
   Mesa.

4. **VPM and vertex pipeline**

   Implement VPM reads/writes, VCD setup, vertex and coordinate shader
   execution, varying production, clipping, and primitive assembly.  This
   clears CT0 binning for a basic triangle.

5. **Tile binning and rasterization**

   Model tile allocation, primitive lists, edge setup, coverage, interpolation,
   depth/stencil state, and tile-buffer load/store behavior.

6. **Fragment QPUs and TMUs**

   Add fragment shader dispatch, interpolated varyings, texture-coordinate
   queues, TMU cache and filtering behavior, thread switching, scoreboard
   synchronization, and color/depth writes.

7. **Mesa GLES2 conformance ladder**

   Advance from a constant-color triangle through interpolated colors,
   blending, depth, indexed draws, textures, mipmaps, render-to-texture,
   scissor, multisampling, and representative GLES2 conformance tests.

8. **Desktop OpenGL on VC4**

   Run the desktop API level genuinely exposed by Mesa's VC4 driver.  Do not
   claim API versions or extensions that the physical VC4 hardware cannot
   support.

9. **BCM2711 / V3D / Vulkan**

   Add a Pi 4 machine and V3D 4.x model as a separate generation.  Bring up
   the Linux ``v3d`` DRM driver, Mesa ``v3d`` OpenGL driver, then ``v3dv``.
   Vulkan milestones should begin with instance/device enumeration and a
   compute-free clear, then progress through render passes, shaders,
   synchronization, descriptors, and the Vulkan CTS.

Validation policy
-----------------

Each milestone must retain:

* the VC4 CPU and heterogeneous-machine regressions;
* firmware power-domain and clock contracts;
* Linux module closure;
* native KMS modeset and page-flip gates;
* the handwritten DRM clear witness;
* exact output pixels;
* migration/reset behavior for every new stateful device component.

Unsupported behavior must remain explicit.  A failed primitive, shader, or
synchronization contract is preferable to a fake completion that lets Mesa
continue with corrupted state.
