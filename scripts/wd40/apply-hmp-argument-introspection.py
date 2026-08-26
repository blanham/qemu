#!/usr/bin/env python3
"""Expose structured HMP argument metadata through query-hmp-commands.

This transformation is additive and replay-safe. It decodes the existing HMP
``args_type`` mini-language for clients without changing parsing or execution.
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
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    store(file_path, text.replace(old, new, 1))


def main() -> None:
    replace_once(
        "qapi/misc.json",
        """##\n# @HMPCommandInfo:\n""",
        """##\n# @HMPArgumentKind:\n#\n# Semantic kind of an HMP command argument.\n#\n# Since: 11.2\n##\n{ 'enum': 'HMPArgumentKind',\n  'data': [ 'filename', 'block-device', 'string', 'rest-of-line',\n            'options', 'format', 'integer', 'size', 'megabytes', 'time',\n            'boolean', 'option', 'unknown' ] }\n\n##\n# @HMPArgumentInfo:\n#\n# Structured description of one HMP parser argument.\n#\n# @name: QDict key populated by the HMP parser\n# @kind: semantic argument kind\n# @optional: whether the argument may be omitted\n# @option: short option spelling, including ``-``\n# @takes-value: whether an option consumes a following string\n# @raw-type: original parser fragment\n#\n# Since: 11.2\n##\n{ 'struct': 'HMPArgumentInfo',\n  'data': { 'name': 'str', 'kind': 'HMPArgumentKind',\n            'optional': 'bool', '*option': 'str',\n            '*takes-value': 'bool', 'raw-type': 'str' } }\n\n##\n# @HMPCommandInfo:\n""",
    )
    replace_once(
        "qapi/misc.json",
        """# @args-type: Internal HMP argument-parser grammar.\n#\n# @parameters: Human-readable parameter syntax.\n""",
        """# @args-type: Internal HMP argument-parser grammar.\n#\n# @arguments: Parsed argument descriptors corresponding to @args-type.\n#\n# @parameters: Human-readable parameter syntax.\n""",
    )
    replace_once(
        "qapi/misc.json",
        """      'args-type': 'str',\n      'parameters': 'str',\n""",
        """      'args-type': 'str',\n      'arguments': [ 'HMPArgumentInfo' ],\n      'parameters': 'str',\n""",
    )
    replace_once(
        "monitor/hmp.c",
        """static bool hmp_command_implemented(const HMPCommand *cmd)\n{\n    return cmd->cmd || cmd->cmd_info_hrt || cmd->sub_table;\n}\n\n""",
        """static bool hmp_command_implemented(const HMPCommand *cmd)\n{\n    return cmd->cmd || cmd->cmd_info_hrt || cmd->sub_table;\n}\n\nstatic HMPArgumentKind hmp_argument_kind(char type)\n{\n    switch (type) {\n    case 'F': return HMP_ARGUMENT_KIND_FILENAME;\n    case 'B': return HMP_ARGUMENT_KIND_BLOCK_DEVICE;\n    case 's': return HMP_ARGUMENT_KIND_STRING;\n    case 'S': return HMP_ARGUMENT_KIND_REST_OF_LINE;\n    case 'O': return HMP_ARGUMENT_KIND_OPTIONS;\n    case '/': return HMP_ARGUMENT_KIND_FORMAT;\n    case 'i': case 'l': return HMP_ARGUMENT_KIND_INTEGER;\n    case 'o': return HMP_ARGUMENT_KIND_SIZE;\n    case 'M': return HMP_ARGUMENT_KIND_MEGABYTES;\n    case 'T': return HMP_ARGUMENT_KIND_TIME;\n    case 'b': return HMP_ARGUMENT_KIND_BOOLEAN;\n    case '-': return HMP_ARGUMENT_KIND_OPTION;\n    default: return HMP_ARGUMENT_KIND_UNKNOWN;\n    }\n}\n\nstatic HMPArgumentInfoList *hmp_argument_info_collect(const char *types)\n{\n    HMPArgumentInfoList *head = NULL;\n    HMPArgumentInfoList **tail = &head;\n\n    while (types && *types) {\n        const char *start = types;\n        const char *colon = strchr(types, ':');\n        const char *comma;\n        HMPArgumentInfo *info;\n        HMPArgumentInfoList *entry;\n        char type;\n\n        if (!colon) {\n            break;\n        }\n        comma = strchr(colon + 1, ',');\n        if (!comma) {\n            comma = colon + strlen(colon);\n        }\n        type = colon[1];\n        info = g_new0(HMPArgumentInfo, 1);\n        entry = g_new0(HMPArgumentInfoList, 1);\n        info->name = g_strndup(start, colon - start);\n        info->kind = hmp_argument_kind(type);\n        info->optional = memchr(colon + 2, '?', comma - (colon + 2)) != NULL;\n        info->raw_type = g_strndup(colon + 1, comma - (colon + 1));\n        if (type == '-' && colon + 2 < comma) {\n            info->has_option = true;\n            info->option = g_strdup_printf("-%c", colon[2]);\n            info->has_takes_value = true;\n            info->takes_value = colon + 3 < comma && colon[3] == 's';\n        }\n        entry->value = info;\n        *tail = entry;\n        tail = &entry->next;\n        types = *comma ? comma + 1 : comma;\n    }\n    return head;\n}\n\n""",
    )
    replace_once(
        "monitor/hmp.c",
        """        info->args_type = g_strdup(cmd->args_type ?: \"\");\n        info->parameters = g_strdup(cmd->params ?: \"\");\n""",
        """        info->args_type = g_strdup(cmd->args_type ?: \"\");\n        info->arguments = hmp_argument_info_collect(cmd->args_type ?: \"\");\n        info->parameters = g_strdup(cmd->params ?: \"\");\n""",
    )
    replace_once(
        "docs/devel/wd40-monitor-v2.rst",
        """This API is deliberately read-only: command execution continues through QMP or\nHMP exactly as before.  It is an experimental foundation for capability-driven\nfront ends such as TTYphoon and a future monitor v2.\n""",
        """This API is deliberately read-only: command execution continues through QMP or\nHMP exactly as before.  It is an experimental foundation for capability-driven\nfront ends such as TTYphoon and a future monitor v2.\n\nEach command also exposes an ``arguments`` array that decodes the internal\n``args-type`` mini-language into argument names, semantic kinds, optionality,\nand short-option metadata.  Clients can therefore construct command UIs\nwithout carrying a second copy of QEMU's HMP parser grammar.\n""",
    )


if __name__ == "__main__":
    main()
