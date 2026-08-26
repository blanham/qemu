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
