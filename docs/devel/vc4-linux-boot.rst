VideoCore IV stock-firmware Linux boot ladder
=============================================

The VC4 Raspberry Pi machine is developed against unchanged, hash-pinned
Raspberry Pi firmware.  The goal is not merely to execute ``bootcode.bin``:
the emulated VPU must load ``start.elf``, perform the normal ARM handoff, and
boot an ordinary Raspberry Pi Linux image from the emulated boot volume.

The validation ladder deliberately separates firmware, ARM handoff, and Linux
failures so that an instruction decoder bug cannot be hidden by direct kernel
loading or by a test-only firmware bypass.

Milestone 1: unchanged bootcode enters start.elf
------------------------------------------------

* Use the pinned ``bootcode.bin``, ``start.elf``, and ``fixup.dat`` trio.
* Reject low-memory PC corruption and unimplemented scalar instructions.
* Require SDHOST multi-block reads, ``CMD12`` completion, L1/L2 cache flush
  handshakes, and software exceptions to follow architectural state changes.
* Preserve the final PC, register state, ordered MMIO trace, and translated
  instruction context as a workflow artifact whenever progress stops.

Milestone 2: stock firmware enters a freestanding kernel8.img
--------------------------------------------------------------

The first ARM-side payload is a tiny AArch64 binary linked at ``0x80000``.  It
writes a fixed signature, MPIDR, initial stack pointer, and firmware argument
register to low RAM before entering a ``wfe`` loop.  The boot volume contains
normal firmware files and ``config.txt``; the payload must be loaded by
``start.elf`` rather than QEMU's direct ``-kernel`` path.

Acceptance requires:

* the signature to be visible through QMP physical-memory inspection;
* ARM CPU 0 to execute at the payload address;
* no test-only write of the signature by the VPU, machine model, or probe;
* an artifact containing the FAT directory, payload disassembly, QMP CPU
  state, and the complete QEMU diagnostic log.

Milestone 3: Linux reaches earlycon
-----------------------------------

Replace the freestanding payload with a pinned Raspberry Pi AArch64 ``Image``,
matching DTB, ``config.txt``, and ``cmdline.txt``.  Use an initramfs initially
so block-device support is not confused with early CPU, interrupt, or timer
failures.

The first Linux gate is an ``earlycon`` line from the kernel itself.  Direct
kernel loading remains a separate control test and does not satisfy this
milestone.

Milestone 4: Linux starts init from an initramfs
------------------------------------------------

Require the kernel to initialize the architectural timer, GIC, SMP bring-up,
mailbox/property interface, and enough serial support to execute a static
``/init``.  The initramfs prints a unique success token and exposes
``/proc/cpuinfo``, the kernel command line, and the discovered device tree.

Milestone 5: root filesystem and normal userspace
--------------------------------------------------

Move the same kernel to a writable SD-backed root filesystem.  Validate
multi-block reads and writes, cache maintenance, interrupts, and reboot/reset
semantics under sustained I/O rather than only during firmware loading.

Milestone 6: release-quality Raspberry Pi 3 support
---------------------------------------------------

The release gate adds deterministic tests for:

* all four Cortex-A53 cores and VPU coexistence under single-threaded TCG;
* GIC and local-interrupt routing;
* system and architectural timers;
* SDHOST and, where selected by firmware, EMMC behavior;
* mailbox/property calls used by Linux;
* USB host initialization and the onboard LAN9514 topology;
* framebuffer/display setup or an explicitly documented headless profile;
* migration/reset coverage for every new BCM2835 device state;
* boot with both the minimal pinned CI image and a documented contemporary
  Raspberry Pi Linux image.

Branch and test discipline
--------------------------

Each hardware or ISA correction is developed on an ``agent/vc4-*`` feature
branch.  It is merged into ``agent/vc4-pc-write-origin-refresh`` only after a
focused regression and the unchanged-firmware frontier test both pass.  Tests
must fail on the old implementation, must not special-case a firmware PC, and
must retain the first later barrier instead of treating any changed PC as
success.
