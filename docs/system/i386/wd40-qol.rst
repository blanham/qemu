WD40 x86 quality-of-life controls
===================================

AMD 1 TiB HyperTransport reservation
------------------------------------

AMD systems with an IOMMU reserve guest physical addresses immediately below
1 TiB for HyperTransport and interrupt-controller functions. For AMD virtual
CPUs, QEMU normally moves RAM and the 64-bit PCI aperture above that range when
the configured address space would overlap it.

The WD40 PC and Q35 machines expose this policy as the ``amd-1tb-hole`` machine
property:

``auto``
  Preserve the selected versioned machine type's compatibility behavior. This
  is the default. Machine versions 7.1 and newer enable the reservation, while
  versions 7.0 and older retain their historical disabled behavior.

``on``
  Force the reservation and, when required, move RAM above 1 TiB. This is useful
  when an older versioned machine must model the modern safe layout.

``off``
  Suppress the reservation and relocation. This is useful for operating-system
  development and retro configurations that deliberately need the contiguous
  pre-1-TiB guest-physical layout.

The property does not change the layout of Intel virtual CPUs. Migration peers
must use the same explicit value whenever ``on`` or ``off`` is selected.

Examples::

  qemu-system-x86_64 -machine q35,amd-1tb-hole=off -cpu EPYC ...
  qemu-system-x86_64 -machine pc-q35-7.0,amd-1tb-hole=on -cpu EPYC ...
