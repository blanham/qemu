Raspberry Pi boards (``raspi0``, ``raspi1ap``, ``raspi2b``, ``raspi3ap``, ``raspi3b``, ``raspi4b``)
===================================================================================================


QEMU provides models of the following Raspberry Pi boards:

``raspi0`` and ``raspi1ap``
  ARM1176JZF-S core, 512 MiB of RAM
``raspi2b``
  Cortex-A7 (4 cores), 1 GiB of RAM
``raspi3ap``
  Cortex-A53 (4 cores), 512 MiB of RAM
``raspi3b``
  Cortex-A53 (4 cores), 1 GiB of RAM
``raspi4b``
  Cortex-A72 (4 cores), 2 GiB of RAM

Implemented devices
-------------------

 * ARM1176JZF-S, Cortex-A7, Cortex-A53 or Cortex-A72 CPU
 * Interrupt controller
 * DMA controller
 * Clock and reset controller (CPRMAN)
 * System Timer
 * GPIO controller
 * Serial ports (BCM2835 AUX - 16550 based - and PL011)
 * Random Number Generator (RNG)
 * Frame Buffer
 * USB host (USBH)
 * GPIO controller
 * SD/MMC host controller
 * SoC thermal sensor
 * USB2 host controller (DWC2 and MPHI)
 * MailBox controller (MBOX)
 * VideoCore firmware (property)
 * Peripheral SPI controller (SPI)
 * Broadcom Serial Controller (I2C)

Missing devices
---------------

 * Pulse Width Modulation (PWM)
 * PCIE Root Port (raspi4b)
 * GENET Ethernet Controller (raspi4b)

VC4 and native Linux DRM validation
-----------------------------------

The VC4 development tests keep render-node and display-controller validation
separate.  The render-only boot proves module loading, DRM identification,
buffer allocation and mapping, ``SUBMIT_CL`` completion, and GPU-written
memory.  The native-KMS boot then enables the HVS, pixel valves, HDMI, TXP,
V3D, and the VC4 component master and records the first display-topology
frontier reached by the pinned Linux fixture.

A minimal initramfs cannot infer every required driver from ``modules.dep``.
The VC4 fixture therefore resolves three kinds of module relationship:

* hard dependencies from ``modules.dep``;
* soft pre- and post-dependencies from ``modules.softdep``; and
* device-tree supplier drivers supplied as additional module roots.

For the Raspberry Pi 3 HDMI path, ``snd-soc-hdmi-codec`` is a soft
predependency of ``vc4``, while ``i2c-bcm2835`` is an independent supplier
for the BSC2 DDC adapter.  The latter must be loaded before ``vc4`` even
though no ELF dependency connects the two modules.  Omitting it leaves the
HDMI component permanently deferred after the HVS has bound.

BSC2 is wired to a deterministic DDC monitor at I2C address ``0x50``.  The
register-level smoke test checks the EDID header and checksum, pointer
addressing, controller reset, and the two-message write/read sequence used by
the Linux ``i2c-bcm2835`` driver.  This avoids making native KMS results depend
on a host display server or physical monitor.

The machine-readable results are committed as
``VC4_LINUX_KMS_BIND_STATUS.json``.  The adjacent Markdown status is intended
for quick inspection.  A failed native-KMS frontier does not invalidate a
successful render-only boot; the workflow keeps the completed render witness
as an independent mandatory regression gate.
