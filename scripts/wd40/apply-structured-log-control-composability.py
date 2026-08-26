#!/usr/bin/env python3
"""Make the structured-log transformer composable with later WD40 docs.

The original hardened ``replace_once()`` recognized later insertions only when
its generated block ended with the shared anchor.  The structured-log docs
block starts with that anchor, so HMP argument documentation inserted between
the anchor and the existing log section caused a duplicate section on replay.

This transformation makes owned-prefix and owned-suffix recognition symmetric,
updates the hardening template, and wires the repair into the integration
contract before the structured-log transformer runs.
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

    replace_exact(
        ".github/workflows/wd40-qol-integration.yml",
        "            python3 scripts/wd40/apply-structured-log-control.py\n",
        "            python3 scripts/wd40/apply-structured-log-control-composability.py\n"
        "            python3 scripts/wd40/apply-structured-log-control.py\n",
    )
    replace_exact(
        ".github/workflows/wd40-qol-integration.yml",
        "          python3 scripts/ci/check-wd40-structured-log-control.py\n",
        "          python3 scripts/ci/check-wd40-transform-composability.py\n"
        "          python3 scripts/ci/check-wd40-structured-log-control.py\n",
    )


if __name__ == "__main__":
    main()
