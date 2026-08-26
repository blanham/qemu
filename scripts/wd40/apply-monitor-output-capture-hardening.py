#!/usr/bin/env python3
"""Harden WD40 output capture and its replay contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

BASE_REPLACE_OLD = '''def replace_once(path: str, old: str, new: str) -> None:
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
'''

BASE_REPLACE_NEW = '''def replace_once(path: str, old: str, new: str,
                 owned_markers: tuple[str, ...] = ()) -> None:
    file_path, text = load(path)
    if new in text:
        return
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
        if owned_prefix and owned_prefix in text:
            return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    store(file_path, text.replace(old, new, 1))
'''

BASE_QMP_TAIL_OLD = '''static void __attribute__((__constructor__)) monitor_init_qmp_commands(void)\\n""",
    )

    replace_once(
        "include/monitor/hmp.h",
'''

BASE_QMP_TAIL_NEW = '''static void __attribute__((__constructor__)) monitor_init_qmp_commands(void)\\n""",
        owned_markers=(
            "static bool wd40_capture_in_progress;",
            "static bool wd40_write_capture_file(",
            "WD40TextCapture *qmp_x_wd40_capture_hmp(",
        ),
    )

    replace_once(
        "include/monitor/hmp.h",
'''

TEMPLATE_DOC_INTRO_OLD = (
    "# Execute a legacy HMP command, capture its exact text output, and optionally\\n"
    "# write that output to a host file.  This is an experimental bridge for WD40\\n"
    "# frontends while commands are migrated to typed QMP services.\\n"
)
TEMPLATE_DOC_INTRO_NEW = (
    "# Execute a legacy HMP command and capture its exact text output.\\n"
    "# Output may also be written to a host file.  This command is an\\n"
    "# experimental bridge for WD40 frontends while commands migrate to\\n"
    "# typed QMP services.\\n"
)
TEMPLATE_DOC_CPU_OLD = (
    "# @cpu-index: CPU selected for commands that use the monitor's implicit CPU\\n"
)
TEMPLATE_DOC_CPU_NEW = (
    "# @cpu-index: CPU selected for commands that use the monitor's\\n"
    "#     implicit CPU\\n"
)

GENERATED_DOC_INTRO_OLD = """# Execute a legacy HMP command, capture its exact text output, and optionally
# write that output to a host file.  This is an experimental bridge for WD40
# frontends while commands are migrated to typed QMP services.
"""
GENERATED_DOC_INTRO_NEW = """# Execute a legacy HMP command and capture its exact text output.
# Output may also be written to a host file.  This command is an
# experimental bridge for WD40 frontends while commands migrate to
# typed QMP services.
"""
GENERATED_DOC_CPU_OLD = (
    "# @cpu-index: CPU selected for commands that use the monitor's implicit CPU\n"
)
GENERATED_DOC_CPU_NEW = (
    "# @cpu-index: CPU selected for commands that use the monitor's\n"
    "#     implicit CPU\n"
)


def replace_once(path: str, old: str, new: str) -> None:
    file_path = ROOT / path
    text = file_path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    # The security hardening below intentionally edits the generated service.
    # Teach the base transformer to recognize that hardened service by stable,
    # unique ownership markers before the workflow performs its second replay.
    replace_once(
        "scripts/wd40/apply-monitor-output-capture.py",
        BASE_REPLACE_OLD,
        BASE_REPLACE_NEW,
    )
    replace_once(
        "scripts/wd40/apply-monitor-output-capture.py",
        BASE_QMP_TAIL_OLD,
        BASE_QMP_TAIL_NEW,
    )

    # Keep both the replay template and the already-generated QAPI document
    # inside QAPI's 70-column documentation contract during the same pass.
    replace_once(
        "scripts/wd40/apply-monitor-output-capture.py",
        TEMPLATE_DOC_INTRO_OLD,
        TEMPLATE_DOC_INTRO_NEW,
    )
    replace_once(
        "scripts/wd40/apply-monitor-output-capture.py",
        TEMPLATE_DOC_CPU_OLD,
        TEMPLATE_DOC_CPU_NEW,
    )
    replace_once(
        "qapi/misc.json",
        GENERATED_DOC_INTRO_OLD,
        GENERATED_DOC_INTRO_NEW,
    )
    replace_once(
        "qapi/misc.json",
        GENERATED_DOC_CPU_OLD,
        GENERATED_DOC_CPU_NEW,
    )

    replace_once(
        "monitor/qmp-cmds.c",
        """#include "qemu/sockets.h"\n""",
        """#include "qemu/ctype.h"\n#include "qemu/sockets.h"\n""",
    )
    replace_once(
        "monitor/qmp-cmds.c",
        """static bool wd40_capture_in_progress;\n\n""",
        """static bool wd40_capture_in_progress;\n\nstatic bool wd40_capture_command_is_recursive(const char *command_line)\n{\n    const char *start = command_line;\n    const char *end;\n    g_autofree char *name = NULL;\n\n    while (qemu_isspace(*start)) {\n        start++;\n    }\n    end = start;\n    while (*end && !qemu_isspace(*end) && *end != '/') {\n        end++;\n    }\n    if (end == start) {\n        return false;\n    }\n\n    name = g_strndup(start, end - start);\n    return hmp_compare_cmd(name, "capture-output|save-output");\n}\n\n""",
    )
    replace_once(
        "monitor/qmp-cmds.c",
        """    if (wd40_capture_in_progress) {\n        error_setg(errp, "nested WD40 output capture is not supported");\n""",
        """    if (wd40_capture_command_is_recursive(command_line) ||\n        wd40_capture_in_progress) {\n        error_setg(errp, "nested WD40 output capture is not supported");\n""",
    )


if __name__ == "__main__":
    main()
