VideoCore IV VPU System Emulator
====================================

QEMU's ``vc4`` target models the scalar VideoCore IV VPU used as the boot
processor in BCM2835/BCM2836/BCM2837 Raspberry Pi SoCs.  It is not the V3D QPU
graphics ISA.

``vc4-vpu`` development machine
-------------------------------

The initial ``vc4-vpu`` machine is deliberately small: one VPU CPU, flat
32-bit physical memory, and a raw firmware image loaded at address zero.

Example::

  qemu-system-vc4 -M vc4-vpu -m 128M -kernel firmware.bin -nographic

Implemented in the first bring-up slice:

* 16-, 32-, and 48-bit scalar instruction length decoding
* integer ALU and predication
* direct and indirect branches
* scalar loads and stores, including indexed and pre/post update forms
* register-list push/pop
* GDB access to r0-r31
* clean traps for unsupported scalar, floating-point, and vector instructions

The 48/80-bit vector ISA, BCM283x peripherals, VPU interrupt/exception state,
and ARM+VPU heterogeneous execution remain separate follow-on work.  The
standalone target exists so these pieces can be tested before the Raspberry Pi
machine is converted from its current ARM-first shortcut to the hardware's
VideoCore-first boot sequence.
