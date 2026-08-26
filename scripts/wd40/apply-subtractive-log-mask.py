#!/usr/bin/env python3
"""Add left-to-right additive and subtractive ``-d`` log-mask parsing.

The transformation is marker-based, self-repairing for the duplicated test
block produced by the original transformer, and safe to replay after later
logging extensions. Existing unprefixed masks retain their behavior;
``+item`` explicitly adds a category and ``-item`` removes one from the mask
built so far.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


LEGACY_PARSE_MASK_TEST_BLOCK = """static int all_log_items_mask(void)
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

"""

PARSE_MASK_TEST_BLOCK = """static int all_log_items_mask(void)
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
    int int_mask = qemu_str_to_log_mask("int");

    g_assert_cmpint(int_mask, !=, 0);
    g_assert_cmpint(qemu_str_to_log_mask("+int,guest_errors"), ==,
                    int_mask | LOG_GUEST_ERROR);
    g_assert_cmpint(qemu_str_to_log_mask("all,-int"), ==,
                    all & ~int_mask);
    g_assert_cmpint(qemu_str_to_log_mask("all,-int,+int"), ==, all);
    g_assert_cmpint(qemu_str_to_log_mask("-all,guest_errors"), ==,
                    LOG_GUEST_ERROR);
    g_assert_cmpint(qemu_str_to_log_mask("int,-int,guest_errors"), ==,
                    LOG_GUEST_ERROR);
    g_assert_cmpint(
        qemu_str_to_log_mask("all,-definitely-not-a-log-item"), ==, 0);
    g_assert_cmpint(qemu_str_to_log_mask("all,-"), ==, 0);
    g_assert_cmpint(qemu_str_to_log_mask("-"), ==, 0);
}

"""


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    """Replace one pristine marker or accept exactly one transformed block."""
    file_path, text = load(path)
    new_count = text.count(new)
    if new_count == 1:
        return
    if new_count > 1:
        raise RuntimeError(
            f"{path}: transformed replacement appears {new_count} times"
        )
    if new.endswith(old):
        owned_prefix = new[:-len(old)]
        if owned_prefix and owned_prefix in text:
            return

    old_count = text.count(old)
    if old_count != 1:
        raise RuntimeError(
            f"{path}: expected one replacement site, found {old_count}"
        )
    store(file_path, text.replace(old, new, 1))


def ensure_parse_mask_tests() -> None:
    """Install one test block and collapse damage from old replay behavior.

    A later transformer may extend the unique test function. Such an extension
    is deliberately preserved. Only the exact legacy/canonical repeated blocks
    are normalized when duplicates exist.
    """
    path = "tests/unit/test-logging.c"
    file_path, text = load(path)
    start = "static int all_log_items_mask(void)\n"
    test_start = "static void test_parse_log_mask(void)\n"
    anchor = "static void set_log_path_tmp(char const *dir, char const *tpl, Error **errp)\n"

    start_count = text.count(start)
    test_count = text.count(test_start)
    anchor_count = text.count(anchor)
    if anchor_count != 1:
        raise RuntimeError(f"{path}: expected one test insertion anchor, found {anchor_count}")

    anchor_pos = text.index(anchor)
    if start_count == 0 and test_count == 0:
        store(file_path, text[:anchor_pos] + PARSE_MASK_TEST_BLOCK + text[anchor_pos:])
        return
    if start_count != test_count or start_count == 0:
        raise RuntimeError(
            f"{path}: inconsistent parse-mask test functions: "
            f"helper={start_count} test={test_count}"
        )

    first = text.index(start)
    if first > anchor_pos:
        raise RuntimeError(f"{path}: parse-mask tests occur after their anchor")
    region = text[first:anchor_pos]

    if start_count == 1:
        if region == LEGACY_PARSE_MASK_TEST_BLOCK:
            store(file_path, text[:first] + PARSE_MASK_TEST_BLOCK + text[anchor_pos:])
        # Preserve the canonical block and any one-copy downstream extension.
        return

    residue = region
    for block in (LEGACY_PARSE_MASK_TEST_BLOCK, PARSE_MASK_TEST_BLOCK):
        residue = residue.replace(block, "")
    if residue.strip():
        raise RuntimeError(
            f"{path}: refusing to discard noncanonical content while repairing "
            f"{start_count} duplicate test blocks"
        )
    store(file_path, text[:first] + PARSE_MASK_TEST_BLOCK + text[anchor_pos:])


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

    ensure_parse_mask_tests()

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
