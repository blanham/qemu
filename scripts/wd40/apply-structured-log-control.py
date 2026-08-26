#!/usr/bin/env python3
"""Add structured discovery and control for QEMU log categories over QMP.

The transformation is marker-based and idempotent so it can be replayed after
routine upstream rebases.  It reuses the existing logging registry and setter;
no parallel category table or logging path is introduced.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    if new in text:
        return
    if new.endswith(old):
        owned_prefix = new[:-len(old)]
        if owned_prefix and owned_prefix in text:
            return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    store(file_path, text.replace(old, new, 1))


def main() -> None:
    replace_once(
        "qapi/misc.json",
        """##
# @human-monitor-command:
""",
        """##
# @LogCategoryInfo:
#
# Structured metadata for a QEMU log category.
#
# @name: Category name accepted by ``-d`` and the HMP ``log`` command.
#
# @help: Human-readable description.
#
# @enabled: Whether the category is currently enabled.
#
# @sticky: Whether the category's enabled state is fixed at process
#     startup and cannot be changed at run time.
#
# Since: 11.2
##
{ 'struct': 'LogCategoryInfo',
  'data': { 'name': 'str',
            'help': 'str',
            'enabled': 'bool',
            'sticky': 'bool' } }

##
# @LogCategoryAction:
#
# How @set-log-categories changes the active category set.
#
# @replace: Replace the active set with @categories.
#
# @enable: Add @categories to the active set.
#
# @disable: Remove @categories from the active set.
#
# Since: 11.2
##
{ 'enum': 'LogCategoryAction',
  'data': [ 'replace', 'enable', 'disable' ] }

##
# @query-log-categories:
#
# Return the compiled log-category registry and current enabled state.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: All compiled log categories.
#
# Since: 11.2
##
{ 'command': 'query-log-categories',
  'returns': [ 'LogCategoryInfo' ],
  'allow-preconfig': true,
  'features': [ 'unstable' ] }

##
# @set-log-categories:
#
# Change the active log categories and return the resulting registry.
#
# @action: How to apply @categories.
#
# @categories: Category names returned by @query-log-categories.
#
# Features:
#
# @unstable: This command is an experimental monitor-v2 foundation.
#
# Returns: All compiled log categories after applying the change.
#
# Since: 11.2
##
{ 'command': 'set-log-categories',
  'data': { 'action': 'LogCategoryAction',
            'categories': [ 'str' ] },
  'returns': [ 'LogCategoryInfo' ],
  'allow-preconfig': true,
  'features': [ 'unstable' ] }

##
# @human-monitor-command:
""",
    )
    replace_once(
        "include/qemu/log.h",
        """bool qemu_set_log(int log_flags, Error **errp);
""",
        """bool qemu_set_log(int log_flags, Error **errp);
unsigned qemu_get_log_mask(void);
""",
    )
    replace_once(
        "util/log.c",
        """bool qemu_set_log(int log_flags, Error **errp)
{
    return qemu_set_log_internal(NULL, false, log_flags, errp);
}

""",
        """bool qemu_set_log(int log_flags, Error **errp)
{
    return qemu_set_log_internal(NULL, false, log_flags, errp);
}

unsigned qemu_get_log_mask(void)
{
    unsigned mask = qemu_loglevel;

    if (log_per_thread) {
        mask |= LOG_PER_THREAD;
    }
    return mask;
}

""",
    )
    replace_once(
        "monitor/qmp-cmds.c",
        """#include "qemu/sockets.h"
""",
        """#include "qemu/sockets.h"
#include "qemu/log.h"
""",
    )
    replace_once(
        "monitor/qmp-cmds.c",
        """NameInfo *qmp_query_name(Error **errp)
""",
        """static LogCategoryInfoList *qmp_log_category_info_list(void)
{
    const QEMULogItem *item;
    LogCategoryInfoList *list = NULL;
    LogCategoryInfoList **tail = &list;
    unsigned mask = qemu_get_log_mask();

    for (item = qemu_log_items; item->mask != 0; item++) {
        LogCategoryInfo *info = g_new0(LogCategoryInfo, 1);
        LogCategoryInfoList *entry = g_new0(LogCategoryInfoList, 1);

        info->name = g_strdup(item->name);
        info->help = g_strdup(item->help);
        info->enabled = (mask & item->mask) == item->mask;
        info->sticky = item->mask == LOG_PER_THREAD;
        entry->value = info;
        *tail = entry;
        tail = &entry->next;
    }

    return list;
}

static bool qmp_log_category_mask(strList *categories, unsigned *mask,
                                  Error **errp)
{
    strList *category;
    unsigned result = 0;

    for (category = categories; category; category = category->next) {
        const QEMULogItem *item;

        for (item = qemu_log_items; item->mask != 0; item++) {
            if (g_str_equal(category->value, item->name)) {
                result |= item->mask;
                break;
            }
        }
        if (item->mask == 0) {
            error_setg(errp, "Unknown log category '%s'", category->value);
            return false;
        }
    }

    *mask = result;
    return true;
}

LogCategoryInfoList *qmp_query_log_categories(Error **errp)
{
    return qmp_log_category_info_list();
}

LogCategoryInfoList *qmp_set_log_categories(LogCategoryAction action,
                                             strList *categories,
                                             Error **errp)
{
    unsigned current = qemu_get_log_mask();
    unsigned selected;
    unsigned target;

    if (!qmp_log_category_mask(categories, &selected, errp)) {
        return NULL;
    }

    switch (action) {
    case LOG_CATEGORY_ACTION_REPLACE:
        target = selected;
        break;
    case LOG_CATEGORY_ACTION_ENABLE:
        target = current | selected;
        break;
    case LOG_CATEGORY_ACTION_DISABLE:
        target = current & ~selected;
        break;
    default:
        g_assert_not_reached();
    }

    if ((current ^ target) & LOG_PER_THREAD) {
        if (current & LOG_PER_THREAD) {
            error_setg(errp,
                       "The 'tid' log category cannot be disabled once set");
        } else {
            error_setg(errp,
                       "The 'tid' log category can only be selected at "
                       "process startup with a '%%d' logfile template");
        }
        return NULL;
    }
    if (!qemu_set_log(target, errp)) {
        return NULL;
    }

    return qmp_log_category_info_list();
}

NameInfo *qmp_query_name(Error **errp)
""",
    )
    replace_once(
        "docs/devel/wd40-monitor-v2.rst",
        """front ends such as TTYphoon and a future monitor v2.
""",
        """front ends such as TTYphoon and a future monitor v2.

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
""",
    )


if __name__ == "__main__":
    main()
