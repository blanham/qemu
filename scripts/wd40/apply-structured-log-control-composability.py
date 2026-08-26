#!/usr/bin/env python3
"""Make the structured-log transformer composable with later WD40 docs.

The original hardened ``replace_once()`` recognized later insertions only when
its generated block ended with the shared anchor.  The structured-log docs
block starts with that anchor, so HMP argument documentation inserted between
the anchor and the existing log section caused a duplicate section on replay.

This transformation makes owned-prefix and owned-suffix recognition symmetric,
updates the hardening template, and wires the repair into every integration
workflow replay/check path before the structured-log transformer runs.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CURRENT_REPLACE = '''def replace_once(path: str, old: str, new: str) -> None:
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

COMPOSABLE_REPLACE = '''def replace_once(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    new_count = text.count(new)
    if new_count == 1:
        return
    if new_count > 1:
        raise RuntimeError(f"{path}: generated block appears {new_count} times")
    if new.endswith(old):
        owned_prefix = new[:-len(old)]
        prefix_count = text.count(owned_prefix) if owned_prefix else 0
        if prefix_count == 1:
            return
        if prefix_count > 1:
            raise RuntimeError(
                f"{path}: generated prefix appears {prefix_count} times"
            )
    if new.startswith(old):
        owned_suffix = new[len(old):]
        suffix_count = text.count(owned_suffix) if owned_suffix else 0
        if suffix_count == 1:
            return
        if suffix_count > 1:
            raise RuntimeError(
                f"{path}: generated suffix appears {suffix_count} times"
            )
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one replacement site, found {count}")
    store(file_path, text.replace(old, new, 1))
'''


def load(path: str) -> tuple[Path, str]:
    file_path = ROOT / path
    return file_path, file_path.read_text(encoding="utf-8")


def store(file_path: Path, text: str) -> None:
    file_path.write_text(text, encoding="utf-8")


def replace_exact(path: str, old: str, new: str) -> None:
    file_path, text = load(path)
    if new in text:
        return
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one repair site, found {count}")
    store(file_path, text.replace(old, new, 1))


def insert_before_each(path: str, anchor: str, insertion: str) -> None:
    """Insert one line before every matching workflow line, atomically.

    A fully applied file is a no-op.  A partially applied or duplicated file is
    rejected rather than silently normalised, so replay errors remain visible.
    """

    file_path, text = load(path)
    anchor_count = text.count(anchor)
    insertion_count = text.count(insertion)
    pair = insertion + anchor
    pair_count = text.count(pair)

    if anchor_count == 0:
        raise RuntimeError(f"{path}: workflow anchor is absent: {anchor!r}")
    if pair_count == anchor_count and insertion_count == anchor_count:
        return
    if pair_count != 0 or insertion_count != 0:
        raise RuntimeError(
            f"{path}: partial workflow repair: anchors={anchor_count} "
            f"insertions={insertion_count} adjacent={pair_count}"
        )

    store(file_path, text.replace(anchor, pair))


def main() -> None:
    replace_exact(
        "scripts/wd40/apply-structured-log-control.py",
        CURRENT_REPLACE,
        COMPOSABLE_REPLACE,
    )

    replace_exact(
        "scripts/wd40/apply-structured-log-control-hardening.py",
        "NEW_REPLACE = '''" + CURRENT_REPLACE + "'''",
        "NEW_REPLACE = '''" + COMPOSABLE_REPLACE + "'''",
    )

    insert_before_each(
        ".github/workflows/wd40-qol-integration.yml",
        "            python3 scripts/wd40/apply-structured-log-control.py\n",
        "            python3 scripts/wd40/apply-structured-log-control-composability.py\n",
    )
    insert_before_each(
        ".github/workflows/wd40-qol-integration.yml",
        "          python3 scripts/ci/check-wd40-structured-log-control.py\n",
        "          python3 scripts/ci/check-wd40-transform-composability.py\n",
    )


if __name__ == "__main__":
    main()
