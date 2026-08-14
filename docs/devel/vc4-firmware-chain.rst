VideoCore IV Raspberry Pi firmware chain
=======================================

This document records the acceptance gates for firmware-first Raspberry Pi 3
emulation.  The defining rule is that files are discovered and transferred by
the emulated VideoCore and SD hardware.  QEMU must not silently inject a later
firmware stage or ARM payload into RAM merely to advance a test.

Current first-stage path
------------------------

The ``raspi3b-vc4`` development machine starts the scalar VideoCore IV VPU
before the Cortex-A53 cluster.  The current branch can present an
MBR-partitioned FAT32 SD image and execute ``BOOTCODE.BIN`` from the VPU-private
boot cache.  The image builder supports complete mirrored FATs, arbitrary
cluster chains, and multiple root-directory files.

The commit-pinned test volume contains::

  BOOTCODE.BIN
  START.ELF
  FIXUP.DAT
  CONFIG.TXT
  KERNEL8.IMG

``CONFIG.TXT`` requests AArch64 operation and names ``KERNEL8.IMG`` explicitly.
The kernel fixture is a tiny AArch64 program which writes ``0x4a11c0de`` to
shared RAM and then loops.  It gives the first ARM handoff a deterministic
success condition without requiring Linux yet.

Acceptance gates
----------------

Gate 1: first-stage firmware volume
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* The official, mutually matching ``bootcode.bin``, ``start.elf``, and
  ``fixup.dat`` files are downloaded from one pinned Raspberry Pi firmware
  commit.
* Their Git blob identities are verified before image construction.
* Every file is reconstructed through the generated FAT cluster chain and
  compared byte for byte.
* ``START.ELF`` crosses multiple FAT sectors, proving that the test is not
  relying on the earlier compact one-sector FAT shortcut.

Gate 2: ``bootcode.bin`` loads ``start.elf``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* The VPU issues real SDHOST commands and reads the FAT through the emulated SD
  card.
* ``start.elf`` and ``fixup.dat`` reach the addresses selected by first-stage
  firmware, not addresses selected by the QEMU test harness.
* Execution transfers from ``bootcode.bin`` into ``start.elf`` without a host
  loader.
* A trace point records the first ``start.elf`` PC and the source SD cluster
  chain.

Gate 3: ``start.elf`` loads the ARM kernel fixture
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``start.elf`` parses ``CONFIG.TXT`` from the same FAT volume.
* It loads ``KERNEL8.IMG`` into ARM SDRAM.
* Firmware configures the ARM execution mode and reset address.
* The real BCM2837 power/reset path releases Cortex-A53 core 0.
* The ARM payload writes ``0x4a11c0de`` to shared RAM.

Gate 4: Linux kernel boot
~~~~~~~~~~~~~~~~~~~~~~~~~

After the marker fixture passes, replace it with a pinned Raspberry Pi Linux
kernel and a minimal initramfs.  Serial output must reach an unambiguous
userspace marker.  Device-tree, mailbox, interrupt, timer, SD, USB, display,
and V3D work should be enabled incrementally rather than bypassed.

Gate 5: Raspberry Pi UEFI firmware
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A separate regression will replace the Linux kernel payload with a pinned Pi 3
UEFI firmware image and matching ``CONFIG.TXT``.  Acceptance requires:

* VideoCore firmware loading the UEFI image from the emulated FAT volume;
* serial UEFI banner and shell or boot-manager output;
* stable memory map, timer, interrupt-controller, and framebuffer protocols;
* booting a small EFI application; and
* later, booting the same Linux fixture through UEFI.

The UEFI test remains separate from direct Linux boot so a shortcut in one path
cannot conceal a defect in the other.

Graphics relationship
---------------------

The firmware chain and graphics work are complementary.  ``start.elf`` is the
first production consumer of the mailbox, clock, HVS, pixel-valve, framebuffer,
and V3D-related platform state.  Display bring-up must therefore use the same
shared peripheral objects visible to both ARM and VideoCore.  A software HVS
and V3D reference path remains the correctness baseline; optional host GPU
acceleration is an optimization behind that model.
