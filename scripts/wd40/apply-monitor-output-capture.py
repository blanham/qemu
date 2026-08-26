#!/usr/bin/env python3
"""Add replay-safe WD40 monitor output capture.

The service captures legacy HMP output once, can return it through structured
QMP, and can write the exact bytes to a host file.  The HMP command is only a
thin frontend over the QMP service so future WD40 frontends can reuse it.
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
        """##\n# @human-monitor-command:\n""",
        """##\n# @WD40TextCapture:\n#\n# Captured human-monitor output.\n#\n# @text: captured text, omitted when ``return-text`` was false\n#\n# @bytes: number of bytes captured and, when requested, written\n#\n# @path: host path written by the command\n#\n# @append: whether output was appended instead of replacing @path\n#\n# Since: 11.2\n##\n{ 'struct': 'WD40TextCapture',\n  'data': { '*text': 'str', 'bytes': 'uint64', '*path': 'str',\n            'append': 'bool' } }\n\n##\n# @x-wd40-capture-hmp:\n#\n# Execute a legacy HMP command, capture its exact text output, and optionally\n# write that output to a host file.  This is an experimental bridge for WD40\n# frontends while commands are migrated to typed QMP services.\n#\n# @command-line: complete HMP command line to execute\n#\n# @cpu-index: CPU selected for commands that use the monitor's implicit CPU\n#\n# @path: host file to write; replacing it unless @append is true\n#\n# @append: append to @path instead of replacing it (default: false)\n#\n# @return-text: include captured text in the reply (default: true)\n#\n# Features:\n#\n# @unstable: This command is an experimental WD40 monitor foundation.\n#\n# Returns: capture metadata and, by default, the captured text\n#\n# Since: 11.2\n##\n{ 'command': 'x-wd40-capture-hmp',\n  'data': { 'command-line': 'str', '*cpu-index': 'int', '*path': 'str',\n            '*append': 'bool', '*return-text': 'bool' },\n  'returns': 'WD40TextCapture',\n  'features': [ 'unstable' ] }\n\n##\n# @human-monitor-command:\n""",
    )

    replace_once(
        "monitor/qmp-cmds.c",
        """out:\n    object_unref(hmp);\n    return output;\n}\n\nstatic void __attribute__((__constructor__)) monitor_init_qmp_commands(void)\n""",
        """out:\n    object_unref(hmp);\n    return output;\n}\n\nstatic bool wd40_capture_in_progress;\n\nstatic bool wd40_write_capture_file(const char *path, bool append,\n                                      const char *text, size_t length,\n                                      Error **errp)\n{\n    GError *gerr = NULL;\n    int fd;\n    ssize_t written;\n    int saved_errno;\n\n    if (length > G_MAXSSIZE) {\n        error_setg(errp, \"Captured output is too large to write\");\n        return false;\n    }\n\n    if (!append) {\n        /* g_file_set_contents() uses a consistent whole-file replacement. */\n        if (!g_file_set_contents(path, text, (gssize)length, &gerr)) {\n            error_setg(errp, \"Could not write '%s': %s\",\n                       path, gerr->message);\n            g_error_free(gerr);\n            return false;\n        }\n        return true;\n    }\n\n    fd = qemu_create(path, O_WRONLY | O_BINARY | O_APPEND, 0666, errp);\n    if (fd < 0) {\n        return false;\n    }\n\n    written = qemu_write_full(fd, text, length);\n    if (written < 0 || (size_t)written != length) {\n        saved_errno = written < 0 ? errno : EIO;\n        qemu_close(fd);\n        error_setg_errno(errp, saved_errno, \"Could not append to '%s'\", path);\n        return false;\n    }\n\n    if (qemu_close(fd) < 0) {\n        error_setg_errno(errp, errno, \"Could not close '%s'\", path);\n        return false;\n    }\n    return true;\n}\n\nWD40TextCapture *qmp_x_wd40_capture_hmp(const char *command_line,\n                                         bool has_cpu_index,\n                                         int64_t cpu_index,\n                                         const char *path,\n                                         bool has_append, bool append,\n                                         bool has_return_text,\n                                         bool return_text,\n                                         Error **errp)\n{\n    g_autofree char *output = NULL;\n    WD40TextCapture *result;\n    bool keep_text = !has_return_text || return_text;\n    size_t length;\n\n    append = has_append && append;\n    if (!command_line[0]) {\n        error_setg(errp, \"command-line must not be empty\");\n        return NULL;\n    }\n    if (append && !path) {\n        error_setg(errp, \"append requires path\");\n        return NULL;\n    }\n    if (!path && !keep_text) {\n        error_setg(errp,\n                   \"at least one output destination must be selected\");\n        return NULL;\n    }\n    if (wd40_capture_in_progress) {\n        error_setg(errp, \"nested WD40 output capture is not supported\");\n        return NULL;\n    }\n\n    wd40_capture_in_progress = true;\n    output = qmp_human_monitor_command(command_line, has_cpu_index,\n                                       cpu_index, errp);\n    wd40_capture_in_progress = false;\n    if (!output) {\n        return NULL;\n    }\n    length = strlen(output);\n\n    if (path && !wd40_write_capture_file(path, append, output, length, errp)) {\n        return NULL;\n    }\n\n    result = g_new0(WD40TextCapture, 1);\n    result->bytes = length;\n    result->append = path && append;\n    result->path = g_strdup(path);\n    if (keep_text) {\n        result->text = g_steal_pointer(&output);\n    }\n    return result;\n}\n\nstatic void __attribute__((__constructor__)) monitor_init_qmp_commands(void)\n""",
    )

    replace_once(
        "include/monitor/hmp.h",
        """void hmp_logfile(Monitor *mon, const QDict *qdict);\n""",
        """void hmp_capture_output(Monitor *mon, const QDict *qdict);\nvoid hmp_logfile(Monitor *mon, const QDict *qdict);\n""",
    )

    replace_once(
        "monitor/hmp-cmds.c",
        """void hmp_logfile(Monitor *mon, const QDict *qdict)\n""",
        """void hmp_capture_output(Monitor *mon, const QDict *qdict)\n{\n    const char *filename = qdict_get_str(qdict, \"filename\");\n    const char *command_line = qdict_get_str(qdict, \"command\");\n    bool append = qdict_get_try_bool(qdict, \"append\", false);\n    bool quiet = qdict_get_try_bool(qdict, \"quiet\", false);\n    int cpu_index = monitor_get_cpu_index(mon);\n    WD40TextCapture *capture;\n    Error *err = NULL;\n\n    capture = qmp_x_wd40_capture_hmp(command_line, cpu_index >= 0, cpu_index,\n                                     filename, true, append, true, !quiet,\n                                     &err);\n    if (hmp_handle_error(mon, err)) {\n        return;\n    }\n\n    if (quiet) {\n        monitor_printf(mon, \"captured %\" PRIu64 \" bytes to '%s'%s\\n\",\n                       capture->bytes, filename,\n                       capture->append ? \" (append)\" : \"\");\n    } else {\n        monitor_puts(mon, capture->text);\n    }\n    qapi_free_WD40TextCapture(capture);\n}\n\nvoid hmp_logfile(Monitor *mon, const QDict *qdict)\n""",
    )

    replace_once(
        "hmp-commands.hx",
        """    {\n        .name       = \"logfile\",\n""",
        """    {\n        .name       = \"capture-output|save-output\",\n        .args_type  = \"append:-a,quiet:-q,filename:F,command:S\",\n        .params     = \"[-a] [-q] filename command...\",\n        .help       = \"capture a monitor command to a host text file\",\n        .cmd        = hmp_capture_output,\n        .flags      = \"p\",\n    },\n\nSRST\n``capture-output`` or ``save-output`` [-a] [-q] *filename* *command*...\n  Execute *command* through the legacy human monitor and write its exact text\n  output to *filename*.  By default the file is replaced consistently and the\n  output is also displayed.  ``-a`` appends to an existing file; ``-q``\n  suppresses the captured text and prints only a byte-count confirmation.  The\n  selected CPU is inherited for commands such as ``info registers``.  Nested\n  capture commands are rejected.\n\n  For example::\n\n    (qemu) capture-output page-tables.txt info mem\n    (qemu) capture-output -q devices.txt info qom-tree\n    (qemu) capture-output -a trace-notes.txt info mtree\nERST\n\n    {\n        .name       = \"logfile\",\n""",
    )

    replace_once(
        "docs/devel/wd40-monitor-v2.rst",
        """without carrying a second copy of QEMU's HMP parser grammar.\n""",
        """without carrying a second copy of QEMU's HMP parser grammar.\n\nText output capture\n-------------------\n\n``x-wd40-capture-hmp`` is a structured bridge for commands that still produce\nlegacy monitor text.  It returns byte-counted output through QMP and can write\nthe exact same bytes to a host file in consistent-replace or append mode.\nSetting ``return-text`` to false avoids returning a second copy after writing a\nlarge dump.  The HMP ``capture-output`` command is a thin frontend over this\nservice; new typed WD40 commands should return structured QAPI objects instead.\nNested capture is rejected at the shared service boundary.\n""",
    )


if __name__ == "__main__":
    main()
