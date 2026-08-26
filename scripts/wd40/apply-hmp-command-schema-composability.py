#!/usr/bin/env python3
"""Make HMP command-schema generation composable with argument metadata.

The HMP command-introspection transform owns ``HMPCommandInfo`` and
``query-hmp-commands``.  The later argument-introspection tranche extends that
schema in place, so exact generated-block matching is no longer a valid replay
signal.  This repair teaches the base transform and its hardening template to
recognize stable schema ownership markers instead.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OLD_REPLACE = '''def replace_once(path: str, old: str, new: str) -> None:
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

NEW_REPLACE = '''def replace_once(path: str, old: str, new: str,
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
'''

OLD_QAPI_TAIL = '''{ 'command': 'query-hmp-commands',
  'returns': [ 'HMPCommandInfo' ],
  'allow-preconfig': true,
  'features': [ 'unstable' ] }

##
# @human-monitor-command:
""",
    )
    replace_once(
        "monitor/hmp.c",
'''

NEW_QAPI_TAIL = '''{ 'command': 'query-hmp-commands',
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
'''

OLD_HARDENER_REPLACE = "NEW_REPLACE = '''" + OLD_REPLACE + "'''"
NEW_HARDENER_REPLACE = "NEW_REPLACE = '''" + NEW_REPLACE + "'''"


def replace_once(path: str, old: str, new: str) -> None:
    file_path = ROOT / path
    text = file_path.read_text(encoding="utf-8")
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one repair site, found {count}")
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    transform = "scripts/wd40/apply-hmp-command-introspection.py"
    replace_once(transform, OLD_REPLACE, NEW_REPLACE)
    replace_once(transform, OLD_QAPI_TAIL, NEW_QAPI_TAIL)

    hardener = "scripts/wd40/apply-hmp-command-introspection-hardening.py"
    replace_once(hardener, OLD_HARDENER_REPLACE, NEW_HARDENER_REPLACE)


if __name__ == "__main__":
    main()
