#!/usr/bin/env python3
"""Add structured HMP command discovery over QMP.

The transformation is marker-based and idempotent so it can be replayed after
routine upstream rebases.  It exposes the existing HMP tables without creating
a second command registry or changing command execution semantics.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str,
                 owned_markers: tuple[str, ...] = ()) -> None:
    file_path, text = load(path)
    new_count = text.count(new)
    if new_count == 1:
        return
    if new_count > 1:
        raise RuntimeError(f"{path}: generated block appears {new_count} times")
    if owned_markers:
        marker_counts = [text.count(marker) for marker in owned_markers]
        if all(count == 1 for count in marker_counts):
            return
        if any(marker_counts):
            raise RuntimeError(
                f"{path}: partially applied generated block: "
                f"marker counts={marker_counts}"
            )
    if new.endswith(old):
        owned_prefix = new[:-len(old)]
        prefix_count = text.count(owned_prefix) if owned_prefix else 0
        if prefix_count == 1:
            return
        if prefix_count > 1:
            raise RuntimeError(
                f"{path}: generated prefix appears {prefix_count} times"
            )
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    store(file_path, text.replace(old, new, 1))


def write_extensible(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        current = file_path.read_text(encoding="utf-8")
        if current == content or current.startswith(content + "\n"):
            return
        raise RuntimeError(
            f"{path}: existing file is not the WD40 base or an append-only "
            "extension"
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")


def main() -> None:
    replace_once(
        "qapi/misc.json",
        """##
# @human-monitor-command:
""",
        """##
# @HMPCommandInfo:
#
# Structured metadata for a human-monitor command.
#
# @path: Canonical full command path.  For aliases, the first name is
#     used in each path component.
#
# @names: Pipe-separated aliases for this command component.
#
# @args-type: Internal HMP argument-parser grammar.
#
# @parameters: Human-readable parameter syntax.
#
# @help: Short help text.
#
# @available: Whether the command is implemented and usable for the
#     current target architecture and machine-initialization phase.
#
# @implemented: Whether a handler, human-readable QMP handler, or
#     subcommand table is registered.
#
# @architecture-available: Whether the command applies to the current
#     target architecture.
#
# @phase-available: Whether the command may run in the current
#     machine-initialization phase.
#
# @preconfig: Whether the command may run before machine
#     initialization.
#
# @coroutine: Whether the command handler runs in a coroutine.
#
# @subcommands: Whether the command has a nested command table.
#
# @arch-mask: QEMU architecture bitmask; zero means unrestricted.
#
# Since: 11.2
##
{ 'struct': 'HMPCommandInfo',
  'data': {
      'path': 'str',
      'names': 'str',
      'args-type': 'str',
      'parameters': 'str',
      'help': 'str',
      'available': 'bool',
      'implemented': 'bool',
      'architecture-available': 'bool',
      'phase-available': 'bool',
      'preconfig': 'bool',
      'coroutine': 'bool',
      'subcommands': 'bool',
      'arch-mask': 'uint32'
  } }

##
# @query-hmp-commands:
#
# Return structured metadata for the HMP command set compiled for this
# target.  Nested command tables are flattened into canonical command
# paths while raw aliases and parser metadata remain available to
# clients.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: All HMP command and subcommand entries for this target.
#
# Since: 11.2
##
{ 'command': 'query-hmp-commands',
  'returns': [ 'HMPCommandInfo' ],
  'allow-preconfig': true,
  'features': [ 'unstable' ] }

##
# @human-monitor-command:
""",
        owned_markers=(
            "{ 'struct': 'HMPCommandInfo',",
            "{ 'command': 'query-hmp-commands',",
        ),
    )
    replace_once(
        "monitor/hmp.c",
        """#include "qapi/error.h"
""",
        """#include "qapi/error.h"
#include "qapi/qapi-commands-misc.h"
""",
    )
    replace_once(
        "monitor/hmp.c",
        """static bool cmd_available(const HMPCommand *cmd)
{
    if (cmd->arch_bitmask && !qemu_arch_available(cmd->arch_bitmask)) {
        return false;
    }
    return phase_check(PHASE_MACHINE_READY) || cmd_can_preconfig(cmd);
}

""",
        """static bool cmd_architecture_available(const HMPCommand *cmd)
{
    return !cmd->arch_bitmask || qemu_arch_available(cmd->arch_bitmask);
}

static bool cmd_phase_available(const HMPCommand *cmd)
{
    return phase_check(PHASE_MACHINE_READY) || cmd_can_preconfig(cmd);
}

static bool cmd_available(const HMPCommand *cmd)
{
    return cmd_architecture_available(cmd) && cmd_phase_available(cmd);
}

""",
    )
    replace_once(
        "monitor/hmp.c",
        """static void help_cmd_dump_one(Monitor *mon,
""",
        """static char *hmp_command_canonical_component(const char *names)
{
    const char *separator = strchr(names, '|');

    if (separator) {
        return g_strndup(names, separator - names);
    }
    return g_strdup(names);
}

static bool hmp_command_implemented(const HMPCommand *cmd)
{
    return cmd->cmd || cmd->cmd_info_hrt || cmd->sub_table;
}

static HMPCommandInfoList **hmp_command_info_collect(
    const HMPCommand *table, const char *prefix, HMPCommandInfoList **tail)
{
    const HMPCommand *cmd;

    for (cmd = table; cmd->name; cmd++) {
        g_autofree char *component =
            hmp_command_canonical_component(cmd->name);
        g_autofree char *path = prefix
            ? g_strdup_printf("%s %s", prefix, component)
            : g_strdup(component);
        bool architecture_available = cmd_architecture_available(cmd);
        bool phase_available = cmd_phase_available(cmd);
        bool implemented = hmp_command_implemented(cmd);
        HMPCommandInfo *info = g_new0(HMPCommandInfo, 1);
        HMPCommandInfoList *entry = g_new0(HMPCommandInfoList, 1);

        info->path = g_strdup(path);
        info->names = g_strdup(cmd->name);
        info->args_type = g_strdup(cmd->args_type ?: "");
        info->parameters = g_strdup(cmd->params ?: "");
        info->help = g_strdup(cmd->help ?: "");
        info->available = architecture_available && phase_available &&
                          implemented;
        info->implemented = implemented;
        info->architecture_available = architecture_available;
        info->phase_available = phase_available;
        info->preconfig = cmd_can_preconfig(cmd);
        info->coroutine = cmd->coroutine;
        info->subcommands = cmd->sub_table != NULL;
        info->arch_mask = cmd->arch_bitmask;

        entry->value = info;
        *tail = entry;
        tail = &entry->next;

        if (cmd->sub_table) {
            tail = hmp_command_info_collect(cmd->sub_table, path, tail);
        }
    }

    return tail;
}

HMPCommandInfoList *qmp_query_hmp_commands(Error **errp)
{
    HMPCommandInfoList *list = NULL;

    hmp_command_info_collect(hmp_cmds_for_target(false), NULL, &list);
    return list;
}

static void help_cmd_dump_one(Monitor *mon,
""",
        owned_markers=(
            "static char *hmp_command_canonical_component(",
            "static HMPCommandInfoList **hmp_command_info_collect(",
            "HMPCommandInfoList *qmp_query_hmp_commands(",
        ),
    )
    replace_once(
        "docs/devel/index.rst",
        """   codebase
""",
        """   codebase
   wd40-monitor-v2
""",
    )
    write_extensible(
        "docs/devel/wd40-monitor-v2.rst",
        """WD40 monitor-v2 foundations
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
""",
    )


if __name__ == "__main__":
    main()
