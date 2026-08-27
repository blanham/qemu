WD40 monitor-v2 foundations
============================

Structured HMP command discovery
--------------------------------

The ``query-hmp-commands`` QMP command exposes the human-monitor command
registry as structured data.  It walks the same runtime tables used for HMP
parsing, including target-specific registrations and nested command tables, so
clients do not need to scrape ``help`` output or carry architecture-specific
command lists.

Each result includes a canonical command path, raw aliases, the HMP argument
grammar, user-facing parameter and help strings, implementation state, and
separate architecture and machine-phase availability.  The combined
``available`` field is true only when all three conditions are satisfied.

For example::

  -> { "execute": "query-hmp-commands" }
  <- { "return": [
         { "path": "info registers",
           "names": "registers",
           "args-type": "",
           "parameters": "",
           "help": "show the cpu registers",
           "available": true,
           "implemented": true,
           "architecture-available": true,
           "phase-available": true,
           "preconfig": false,
           "coroutine": false,
           "subcommands": false,
           "arch-mask": 0 },
         ...
       ] }

The command is available during preconfiguration.  In that phase clients can
still discover the complete registry and distinguish commands unavailable only
because machine initialization has not completed from commands absent on the
current target architecture.

This API is deliberately read-only: command execution continues through QMP or
HMP exactly as before.  It is an experimental foundation for capability-driven
front ends such as TTYphoon and a future monitor v2.

Each command also exposes an ``arguments`` array that decodes the internal
``args-type`` mini-language into argument names, semantic kinds, optionality,
and short-option metadata.  Clients can therefore construct command UIs
without carrying a second copy of QEMU's HMP parser grammar.

Text output capture
-------------------

``x-wd40-capture-hmp`` is a structured bridge for commands that still produce
legacy monitor text.  It returns byte-counted output through QMP and can write
the exact same bytes to a host file in consistent-replace or append mode.
Setting ``return-text`` to false avoids returning a second copy after writing a
large dump.  The HMP ``capture-output`` command is a thin frontend over this
service; new typed WD40 commands should return structured QAPI objects instead.
Nested capture is rejected at the shared service boundary.

Context-sensitive HMP completion
--------------------------------

``x-wd40-complete-hmp`` exposes the exact completion engine used by interactive
HMP.  It covers command aliases, nested command tables, filename and block
backend arguments, and command-specific dynamic providers such as device,
chardev, migration, trace-event, and snapshot names.  Availability filtering
therefore remains identical to the active target and machine phase.

The request accepts an optional byte cursor so a frontend can complete text in
the middle of an editor buffer.  The response identifies the active token's
replacement span and returns sorted complete candidate strings; text after the
cursor is never inspected or discarded.  Offsets are UTF-8 byte offsets, and
the cursor must fall on a character boundary.

The result also reports when HMP's fixed completion capacity was filled and how
many filesystem candidates could not be represented as QMP UTF-8 strings.
This lets TTYphoon and other monitor-v2 clients reuse QEMU's live knowledge
without embedding another completion implementation.

Bounded guest-memory reads
--------------------------

``x-wd40-read-memory`` returns between one byte and one MiB of raw guest
memory as lowercase hexadecimal.  Virtual reads use the selected CPU's
debugger translation path and synchronize accelerator state first.  Physical
reads use the system address space directly and preserve memory-transaction
errors instead of silently returning filler bytes.

The command rejects wrapped address ranges, invalid CPU selections, and a CPU
selection on physical reads.  It is unavailable while incoming migration is
loading guest RAM.  Like the register snapshot command, it does not pause a
running machine; frontends should stop the guest when they require a coherent
view across multiple reads.

These are debugger accesses, not side-effect-free RAM snapshots.  A virtual or
physical address mapped to MMIO can invoke the device's read callback.  The
bounded response is intended for interactive memory panes and inspectors;
larger captures should continue to use file-oriented dump mechanisms.

Bounded guest-memory writes
---------------------------

``x-wd40-write-memory`` writes between one byte and one MiB from an exact
hexadecimal byte string.  It shares ``WD40MemorySpace`` with bounded reads:
virtual writes use the selected CPU's debugger path, while physical writes use
the system address space and preserve memory-transaction failures.

The command rejects empty, odd-length, non-hexadecimal, oversized, and wrapped
requests before touching guest memory.  CPU selection follows the read
service: CPU 0 is the virtual default, invalid CPU numbers fail, and physical
writes reject ``cpu-index``.

Writes are debugger operations rather than side-effect-free RAM edits.  They
can invoke MMIO callbacks, and virtual debug writes can modify ROM through
QEMU's debugger path.  Neither virtual nor physical writes are atomic.  If a
multi-byte request fails, an earlier portion of the range may already have
been modified; QEMU does not roll it back.  Clients that need all-or-nothing
behavior must arrange their own validation and rollback.

The command synchronizes accelerator state but does not pause a running guest;
clients should issue ``stop`` before read-modify-write work that must be
coherent.

Typed virtual-to-physical translation
-------------------------------------

``x-wd40-translate-address`` exposes QEMU's common CPU debugger translation
hook without parsing target-specific monitor text.  The result identifies the
selected CPU and target, reports an ordinary translation miss as structured
state, and returns the physical address, CPU address-space index, aligned block
size, and raw transaction attributes after a successful translation.

The aligned block size describes the range for which the CPU translation and
attributes remain valid.  It does not prove that the resulting physical
address is backed by RAM or a device.  Frontends can combine this command with
``x-wd40-read-memory`` when they need both MMU provenance and bytes.

The command synchronizes accelerator state but does not pause a running guest.
Clients should stop the machine before combining translations, memory reads,
and register snapshots that must describe one coherent point in time.

Cross-architecture CPU register snapshots
-----------------------------------------

``x-wd40-query-cpu-registers`` reads the selected virtual CPU through QEMU's
GDB register registry and callbacks.  It returns canonical target and CPU type
metadata plus registers sorted by GDB number, including dynamically registered
supplemental feature sets.  Architectures that have not supplied register-name
metadata still expose their core register numbers with ``gdb-reg-N`` names.

The supporting GDB registry accounts for explicit register-number gaps before
appending later feature sets.  The snapshot service treats duplicate numbers as
an initialization error instead of silently pairing a name from one feature
with value bytes supplied by another callback.

Register values are the exact byte sequences produced by the target's GDB
callback, encoded as lowercase hexadecimal rather than converted through an
architecture-specific integer formatter.  The snapshot reports target word
size and default endianness, but clients should retain the register's GDB
feature when interpreting vector, floating-point, or special register layouts.

The command synchronizes accelerator state before reading.
It does not pause a running machine, so debugger frontends should issue
``stop`` first when they need a coherent snapshot across all registers or
virtual CPUs.  This gives TTYphoon a typed cross-target register foundation
without scraping ``info registers`` output or adding per-architecture monitor
parsers.

Structured log-category control
-------------------------------

``query-log-categories`` returns the same category registry used by ``-d`` and
the HMP ``log`` command, together with each category's current enabled state.
``set-log-categories`` applies an explicit ``replace``, ``enable``, or
``disable`` operation and returns the resulting registry.

For example::

  -> { "execute": "set-log-categories",
       "arguments": { "action": "replace",
                      "categories": [ "guest_errors", "unimp" ] } }
  <- { "return": [
         { "name": "guest_errors",
           "help": "log when the guest OS does something invalid ...",
           "enabled": true,
           "sticky": false },
         ...
       ] }

The commands are available during preconfiguration.  Unknown category names
are rejected atomically: no logging state changes unless every supplied name
is valid.  The ``tid`` category is reported as sticky because its state is
fixed at process startup.  It must be enabled with both a ``-D`` ``%d``
filename template and ``-d tid``; QMP rejects attempts either to enable it
later or to disable it after startup, rather than silently misreporting a
transition the logger cannot perform.

Trace-event patterns remain managed through QEMU's tracing interfaces.  This
API covers the ordinary named categories in ``qemu_log_items`` and deliberately
reuses ``qemu_set_log()`` for all state changes.

Composite aliases are reported as enabled only when all constituent bits are
active. Consequently, selecting x86 ``irq`` alone does not misreport the
compatible aggregate ``int`` as enabled; selecting ``int`` enables and reports
``int``, ``irq``, and ``exception`` together.
