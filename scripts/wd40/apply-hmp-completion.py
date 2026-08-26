#!/usr/bin/env python3
"""Add a structured bridge to QEMU's native HMP completion engine."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def insert_before_once(
    path: str,
    anchor: str,
    block: str,
    *,
    owned_markers: tuple[str, ...],
) -> None:
    file_path, text = load(path)
    marker_counts = [text.count(marker) for marker in owned_markers]
    if all(count == 1 for count in marker_counts):
        return
    if any(marker_counts):
        raise RuntimeError(
            f"{path}: partially applied completion block: "
            f"marker counts={marker_counts}"
        )
    count = text.count(anchor)
    if count != 1:
        raise RuntimeError(f"{path}: expected one insertion site, found {count}")
    store(file_path, text.replace(anchor, block + anchor, 1))


def main() -> None:
    insert_before_once(
        "qapi/misc.json",
        """##\n# @LogCategoryInfo:\n""",
        """##\n# @WD40HMPCompletion:\n#\n# Context-sensitive HMP completion candidates.\n#\n# @cursor: byte offset in the command line where completion was\n#     requested\n#\n# @replace-start: byte offset where the active token begins\n#\n# @replace-length: number of bytes from @replace-start to @cursor\n#\n# @candidates: sorted complete replacements for the active token\n#\n# @capacity-reached: whether HMP's fixed completion capacity was\n#     filled, so additional candidates may exist\n#\n# @omitted-invalid-utf8: candidates omitted because QMP strings must\n#     contain valid UTF-8\n#\n# Since: 11.2\n##\n{ 'struct': 'WD40HMPCompletion',\n  'data': { 'cursor': 'uint64', 'replace-start': 'uint64',\n            'replace-length': 'uint64', 'candidates': [ 'str' ],\n            'capacity-reached': 'bool',\n            'omitted-invalid-utf8': 'uint64' } }\n\n##\n# @x-wd40-complete-hmp:\n#\n# Complete an HMP command line with the same registry, argument\n# grammar, dynamic value providers, and availability checks used by\n# interactive HMP.  Frontends should replace the returned byte span\n# with one selected candidate while preserving text after @cursor.\n#\n# @command-line: complete editor buffer\n#\n# @cursor: byte offset where completion is requested; defaults to the\n#     end of @command-line and must lie on a UTF-8 boundary\n#\n# Features:\n#\n# @unstable: This command is an experimental monitor-v2 foundation.\n#\n# Returns: replacement span and completion candidates\n#\n# Since: 11.2\n##\n{ 'command': 'x-wd40-complete-hmp',\n  'data': { 'command-line': 'str', '*cursor': 'uint64' },\n  'returns': 'WD40HMPCompletion',\n  'allow-preconfig': true,\n  'features': [ 'unstable' ] }\n\n""",
        owned_markers=(
            "'struct': 'WD40HMPCompletion'",
            "'command': 'x-wd40-complete-hmp'",
        ),
    )

    insert_before_once(
        "monitor/hmp.c",
        """static void monitor_read(void *opaque, const uint8_t *buf, int size)\n""",
        r'''static void G_GNUC_PRINTF(2, 3)
hmp_completion_discard_printf(void *opaque, const char *fmt, ...)
{
    (void)opaque;
    (void)fmt;
}

static void hmp_completion_discard_flush(void *opaque)
{
    (void)opaque;
}

static int hmp_completion_compare(const void *left, const void *right)
{
    const char *const *left_string = left;
    const char *const *right_string = right;

    return strcmp(*left_string, *right_string);
}

static void hmp_completion_clear(ReadLineState *rs)
{
    int i;

    for (i = 0; i < rs->nb_completions; i++) {
        g_clear_pointer(&rs->completions[i], g_free);
    }
    rs->nb_completions = 0;
}

WD40HMPCompletion *qmp_x_wd40_complete_hmp(const char *command_line,
                                             bool has_cursor,
                                             uint64_t cursor,
                                             Error **errp)
{
    size_t line_length = strlen(command_line);
    g_autofree char *command_prefix = NULL;
    MonitorHMP *hmp;
    ReadLineState *rs;
    WD40HMPCompletion *result;
    strList **tail;
    uint64_t omitted = 0;
    int i;

    if (line_length > READLINE_CMD_BUF_SIZE) {
        error_setg(errp,
                   "command-line exceeds the HMP readline limit of %d bytes",
                   READLINE_CMD_BUF_SIZE);
        return NULL;
    }

    if (!has_cursor) {
        cursor = line_length;
    }
    if (cursor > line_length) {
        error_setg(errp,
                   "cursor at byte %" PRIu64
                   " exceeds command-line length %zu",
                   cursor, line_length);
        return NULL;
    }
    if (!g_utf8_validate(command_line, (gssize)cursor, NULL)) {
        error_setg(errp, "cursor must lie on a UTF-8 boundary");
        return NULL;
    }

    command_prefix = g_strndup(command_line, (gsize)cursor);
    hmp = MONITOR_HMP(object_new(TYPE_MONITOR_HMP));
    hmp->rs = readline_init(hmp_completion_discard_printf,
                            hmp_completion_discard_flush,
                            hmp, monitor_find_completion);
    rs = hmp->rs;
    monitor_find_completion(hmp, command_prefix);

    if (rs->completion_index < 0 ||
        (uint64_t)rs->completion_index > cursor) {
        error_setg(errp, "HMP completion returned an invalid replacement span");
        hmp_completion_clear(rs);
        object_unref(hmp);
        return NULL;
    }

    qsort(rs->completions, rs->nb_completions,
          sizeof(rs->completions[0]), hmp_completion_compare);

    result = g_new0(WD40HMPCompletion, 1);
    result->cursor = cursor;
    result->replace_start = cursor - rs->completion_index;
    result->replace_length = rs->completion_index;
    result->capacity_reached =
        rs->nb_completions == READLINE_MAX_COMPLETIONS;
    tail = &result->candidates;

    for (i = 0; i < rs->nb_completions; i++) {
        strList *entry;

        if (!g_utf8_validate(rs->completions[i], -1, NULL)) {
            omitted++;
            continue;
        }
        entry = g_new0(strList, 1);
        entry->value = g_strdup(rs->completions[i]);
        *tail = entry;
        tail = &entry->next;
    }
    result->omitted_invalid_utf8 = omitted;

    hmp_completion_clear(rs);
    object_unref(hmp);
    return result;
}

''',
        owned_markers=(
            "static void hmp_completion_clear(ReadLineState *rs)",
            "WD40HMPCompletion *qmp_x_wd40_complete_hmp",
        ),
    )

    insert_before_once(
        "docs/devel/wd40-monitor-v2.rst",
        """Structured log-category control\n-------------------------------\n""",
        """Context-sensitive HMP completion\n--------------------------------\n\n``x-wd40-complete-hmp`` exposes the exact completion engine used by interactive\nHMP.  It covers command aliases, nested command tables, filename and block\nbackend arguments, and command-specific dynamic providers such as device,\nchardev, migration, trace-event, and snapshot names.  Availability filtering\ntherefore remains identical to the active target and machine phase.\n\nThe request accepts an optional byte cursor so a frontend can complete text in\nthe middle of an editor buffer.  The response identifies the active token's\nreplacement span and returns sorted complete candidate strings; text after the\ncursor is never inspected or discarded.  Offsets are UTF-8 byte offsets, and\nthe cursor must fall on a character boundary.\n\nThe result also reports when HMP's fixed completion capacity was filled and how\nmany filesystem candidates could not be represented as QMP UTF-8 strings.\nThis lets TTYphoon and other monitor-v2 clients reuse QEMU's live knowledge\nwithout embedding another completion implementation.\n\n""",
        owned_markers=(
            "Context-sensitive HMP completion",
            "x-wd40-complete-hmp",
        ),
    )


if __name__ == "__main__":
    main()
