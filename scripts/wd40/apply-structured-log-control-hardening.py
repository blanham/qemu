#!/usr/bin/env python3
"""Harden structured QMP log control and its source transformer.

The original tranche had two composability/state issues:

* generated QAPI blocks retain shared insertion anchors, so later monitor-v2
  blocks may appear between this transform's owned prefix and that anchor;
* QEMU's per-thread logger mode is fixed at process startup.  A runtime enable
  after opening an ordinary global ``-D`` logfile could be silently stripped
  by the logger, while disabling an active mode was already rejected.

The hardened transformation recognizes owned prefixes at preserved anchors,
rejects every real runtime transition of ``tid`` in either direction, and
updates the embedded source templates so future replays preserve the contract.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OLD_REPLACE = '''def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    store(file_path, text.replace(old, new, 1))
'''

NEW_REPLACE = '''def replace_once(path: str, old: str, new: str) -> None:
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

OLD_STICKY = """# @sticky: Whether the category cannot be disabled after being
#     enabled.
"""
NEW_STICKY = """# @sticky: Whether the category's enabled state is fixed at process
#     startup and cannot be changed at run time.
"""

OLD_GUARD = """    if ((current & LOG_PER_THREAD) && !(target & LOG_PER_THREAD)) {
        error_setg(errp, "The 'tid' log category cannot be disabled once set");
        return NULL;
    }
"""
NEW_GUARD = """    if ((current ^ target) & LOG_PER_THREAD) {
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
"""

OLD_DOCS = """The commands are available during preconfiguration.  Unknown category names
are rejected atomically: no logging state changes unless every supplied name
is valid.  The ``tid`` category is reported as sticky because QEMU cannot
return from per-thread log files to a single global output after enabling it;
an attempted transition that would disable it is rejected instead of being
silently misreported.
"""
NEW_DOCS = """The commands are available during preconfiguration.  Unknown category names
are rejected atomically: no logging state changes unless every supplied name
is valid.  The ``tid`` category is reported as sticky because its state is
fixed at process startup.  It must be enabled with both a ``-D`` ``%d``
filename template and ``-d tid``; QMP rejects attempts either to enable it
later or to disable it after startup, rather than silently misreporting a
transition the logger cannot perform.
"""


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    new_count = text.count(new)
    if new_count == 1:
        return
    if new_count > 1:
        raise RuntimeError(f"{path}: hardened block appears {new_count} times")
    old_count = text.count(old)
    if old_count != 1:
        raise RuntimeError(f"{path}: expected one hardening site, found {old_count}")
    store(file_path, text.replace(old, new, 1))


def main() -> None:
    transform = "scripts/wd40/apply-structured-log-control.py"
    replace_once(transform, OLD_REPLACE, NEW_REPLACE)

    replace_once("qapi/misc.json", OLD_STICKY, NEW_STICKY)
    replace_once(transform, OLD_STICKY, NEW_STICKY)

    replace_once("monitor/qmp-cmds.c", OLD_GUARD, NEW_GUARD)
    replace_once(transform, OLD_GUARD, NEW_GUARD)

    replace_once("docs/devel/wd40-monitor-v2.rst", OLD_DOCS, NEW_DOCS)
    replace_once(transform, OLD_DOCS, NEW_DOCS)


if __name__ == "__main__":
    main()
