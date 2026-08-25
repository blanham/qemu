#!/usr/bin/env python3
"""Add left-to-right additive and subtractive ``-d`` log-mask parsing.

The transformation is marker-based and idempotent so it can be rerun after
routine upstream rebases. Existing unprefixed masks retain their behavior;
``+item`` explicitly adds a category and ``-item`` removes one from the mask
built so far.
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
    count = text.count(old)
    if count == 1:
        store(file_path, text.replace(old, new, 1))
        return
    if count == 0 and new in text:
        return
    raise RuntimeError(f"{path}: expected one replacement site, found {count}")


def main() -> None:
    replace_once(
        "util/log.c",
        """/* takes a comma separated list of log masks. Return 0 if error. */
int qemu_str_to_log_mask(const char *str)
{
    const QEMULogItem *item;
    int mask = 0;
    char **parts = g_strsplit(str, ",", 0);
    char **tmp;

    for (tmp = parts; tmp && *tmp; tmp++) {
        if (g_str_equal(*tmp, "all")) {
            for (item = qemu_log_items; item->mask != 0; item++) {
                mask |= item->mask;
            }
#ifdef CONFIG_TRACE_LOG
        } else if (g_str_has_prefix(*tmp, "trace:") && (*tmp)[6] != '\\0') {
            trace_enable_events((*tmp) + 6);
            mask |= LOG_TRACE;
#endif
        } else {
            for (item = qemu_log_items; item->mask != 0; item++) {
                if (g_str_equal(*tmp, item->name)) {
                    goto found;
                }
            }
            goto error;
        found:
            mask |= item->mask;
        }
    }

    g_strfreev(parts);
    return mask;

 error:
    g_strfreev(parts);
    return 0;
}
""",
        """/*
 * Take a comma-separated list of log masks. Items are processed from left to
 * right. A leading '-' removes an item and a leading '+' adds one. Return 0
 * on error.
 */
int qemu_str_to_log_mask(const char *str)
{
    const QEMULogItem *item;
    int mask = 0;
    char **parts = g_strsplit(str, ",", 0);
    char **tmp;

    for (tmp = parts; tmp && *tmp; tmp++) {
        const char *part = *tmp;
        bool subtract = false;
        int item_mask = 0;

        if (*part == '+' || *part == '-') {
            subtract = *part == '-';
            part++;
        }
        if (*part == '\\0') {
            goto error;
        }

        if (g_str_equal(part, "all")) {
            for (item = qemu_log_items; item->mask != 0; item++) {
                item_mask |= item->mask;
            }
#ifdef CONFIG_TRACE_LOG
        } else if (!subtract && g_str_has_prefix(part, "trace:") &&
                   part[6] != '\\0') {
            trace_enable_events(part + 6);
            item_mask = LOG_TRACE;
#endif
        } else {
            for (item = qemu_log_items; item->mask != 0; item++) {
                if (g_str_equal(part, item->name)) {
                    item_mask = item->mask;
                    break;
                }
            }
            if (item_mask == 0) {
                goto error;
            }
        }

        if (subtract) {
            mask &= ~item_mask;
        } else {
            mask |= item_mask;
        }
    }

    g_strfreev(parts);
    return mask;

 error:
    g_strfreev(parts);
    return 0;
}
""",
    )
    replace_once(
        "util/log.c",
        """void qemu_print_log_usage(FILE *f)
{
    const QEMULogItem *item;
    fprintf(f, "Log items (comma separated):\\n");
    for (item = qemu_log_items; item->mask != 0; item++) {
""",
        """void qemu_print_log_usage(FILE *f)
{
    const QEMULogItem *item;
    fprintf(f, "Log items (comma separated):\\n");
    fprintf(f, "Items are processed left-to-right; prefix '+' to add and "
               "'-' to remove.\\n");
    for (item = qemu_log_items; item->mask != 0; item++) {
""",
    )
    replace_once(
        "qemu-options.hx",
        """DEF("d", HAS_ARG, QEMU_OPTION_d, \\
    "-d item1,...    enable logging of specified items (use '-d help' for a list of log items)\\n",
    QEMU_ARCH_ALL)
SRST
``-d item1[,...]``
    Enable logging of specified items. Use '-d help' for a list of log
    items.
ERST
""",
        """DEF("d", HAS_ARG, QEMU_OPTION_d, \\
    "-d item1,...    enable logging; prefix items with '-' to exclude them\\n"
    "                (use '-d help' for a list of log items)\\n",
    QEMU_ARCH_ALL)
SRST
``-d item1[,...]``
    Enable logging of specified items. Use ``-d help`` for a list of log
    items.

    Items are processed from left to right. Prefix an item with ``-`` to
    remove it from the current mask, or with ``+`` to add it explicitly.

    For example, enable every ordinary log category except per-thread files,
    interrupt logging, and the two highest-volume execution streams::

        -d all,-tid,-int,-exec,-cpu
ERST
""",
    )
    replace_once(
        "hmp-commands.hx",
        """    {
        .name       = "log",
        .args_type  = "items:s",
        .params     = "item1[,...]",
        .help       = "activate logging of the specified items",
        .cmd        = hmp_log,
    },

SRST
``log`` *item1*\\ [,...]
  Activate logging of the specified items.
ERST
""",
        """    {
        .name       = "log",
        .args_type  = "items:s",
        .params     = "item1[,...]",
        .help       = "set logging items; prefix '-' to exclude",
        .cmd        = hmp_log,
    },

SRST
``log`` *item1*\\ [,...]
  Replace the active log mask with the specified items. Items are processed
  from left to right; prefix an item with ``-`` to remove it from the mask
  built so far, or with ``+`` to add it explicitly.

  For example, enable every ordinary category except per-thread files,
  interrupt logging, and the two highest-volume execution streams::

    (qemu) log all,-tid,-int,-exec,-cpu

  Use ``log none`` to disable logging.
ERST
""",
    )
    replace_once(
        "linux-user/main.c",
        """    {"d",          "QEMU_LOG",         true,  handle_arg_log,
     "item[,...]", "enable logging of specified items "
     "(use '-d help' for a list of items)"},
""",
        """    {"d",          "QEMU_LOG",         true,  handle_arg_log,
     "item[,...]", "enable logging; prefix '-' to exclude an item "
     "(use '-d help' for a list of items)"},
""",
    )
    replace_once(
        "bsd-user/main.c",
        """           "-d item1[,...]    enable logging of specified items\\n"
           "                  (use '-d help' for a list of log items)\\n"
""",
        """           "-d item1[,...]    enable logging; prefix '-' to exclude an item\\n"
           "                  (use '-d help' for a list of log items)\\n"
""",
    )
    replace_once(
        "tests/unit/test-logging.c",
        """static void set_log_path_tmp(char const *dir, char const *tpl, Error **errp)
""",
        """static int all_log_items_mask(void)
{
    const QEMULogItem *item;
    int mask = 0;

    for (item = qemu_log_items; item->mask != 0; item++) {
        mask |= item->mask;
    }
    return mask;
}

static void test_parse_log_mask(void)
{
    int all = all_log_items_mask();

    g_assert_cmpint(qemu_str_to_log_mask("int"), ==, CPU_LOG_INT);
    g_assert_cmpint(qemu_str_to_log_mask("+int,guest_errors"), ==,
                    CPU_LOG_INT | LOG_GUEST_ERROR);
    g_assert_cmpint(qemu_str_to_log_mask("all,-int"), ==,
                    all & ~CPU_LOG_INT);
    g_assert_cmpint(qemu_str_to_log_mask("all,-int,+int"), ==, all);
    g_assert_cmpint(qemu_str_to_log_mask("-all,guest_errors"), ==,
                    LOG_GUEST_ERROR);
    g_assert_cmpint(qemu_str_to_log_mask("int,-int,guest_errors"), ==,
                    LOG_GUEST_ERROR);
    g_assert_cmpint(
        qemu_str_to_log_mask("all,-definitely-not-a-log-item"), ==, 0);
    g_assert_cmpint(qemu_str_to_log_mask("all,-"), ==, 0);
}

static void set_log_path_tmp(char const *dir, char const *tpl, Error **errp)
""",
    )
    replace_once(
        "tests/unit/test-logging.c",
        """    g_test_add_func("/logging/parse_range", test_parse_range);
    g_test_add_data_func("/logging/parse_path", tmp_path, test_parse_path);
""",
        """    g_test_add_func("/logging/parse_range", test_parse_range);
    g_test_add_func("/logging/parse_mask", test_parse_log_mask);
    g_test_add_data_func("/logging/parse_path", tmp_path, test_parse_path);
""",
    )


if __name__ == "__main__":
    main()
