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
