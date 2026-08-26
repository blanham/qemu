#!/usr/bin/env python3
"""Make the AMD QoL base-document transform append-composable.

The AMD tranche owns the base ``docs/system/i386/wd40-qol.rst`` document and
later x86 QoL tranches append sections to it.  This transformation replaces the
exact-only creator with a base-or-append-only writer and updates the single
call site.  Unrelated rewrites remain fatal.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OLD_WRITER = '''def create_once(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")
        if existing == content:
            return
        raise RuntimeError(f"{path}: existing content differs from WD40 template")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
'''

NEW_WRITER = '''def create_extensible(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        existing = file_path.read_text(encoding="utf-8")
        if existing == content or existing.startswith(content + "\\n"):
            return
        raise RuntimeError(
            f"{path}: existing file is not the WD40 base or an append-only "
            "extension"
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
'''

OLD_CALL = '''    create_once(
        "docs/system/i386/wd40-qol.rst",
'''
NEW_CALL = '''    create_extensible(
        "docs/system/i386/wd40-qol.rst",
'''


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
    path = "scripts/wd40/apply-amd-1tb-hole-control.py"
    replace_once(path, OLD_WRITER, NEW_WRITER)
    replace_once(path, OLD_CALL, NEW_CALL)


if __name__ == "__main__":
    main()
