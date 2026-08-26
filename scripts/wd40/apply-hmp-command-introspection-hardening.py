#!/usr/bin/env python3
"""Make the HMP introspection transform composable with later tranches.

Two retained-anchor patterns need explicit ownership rules:

* the HMP QAPI block preserves the shared ``human-monitor-command`` anchor, so
  later QAPI tranches may legitimately insert between HMP's owned prefix and
  that anchor;
* HMP owns the base ``wd40-monitor-v2.rst`` document, while later monitor-v2
  tranches append sections to it.

The hardened transformer recognizes its unique generated prefix before using a
preserved anchor and accepts only the exact base document or an append-only
extension beginning after a blank line.
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

OLD_WRITER = '''def write_exact(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        if file_path.read_text(encoding="utf-8") != content:
            raise RuntimeError(f"{path}: existing file differs from WD40 content")
        return
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
'''

NEW_WRITER = '''def write_extensible(path: str, content: str) -> None:
    file_path = ROOT / path
    if file_path.exists():
        current = file_path.read_text(encoding="utf-8")
        if current == content or current.startswith(content + "\\n"):
            return
        raise RuntimeError(
            f"{path}: existing file is not the WD40 base or an append-only "
            "extension"
        )
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
'''

OLD_CALL = '''    write_exact(
        "docs/devel/wd40-monitor-v2.rst",
'''
NEW_CALL = '''    write_extensible(
        "docs/devel/wd40-monitor-v2.rst",
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
    path = "scripts/wd40/apply-hmp-command-introspection.py"
    replace_once(path, OLD_REPLACE, NEW_REPLACE)
    replace_once(path, OLD_WRITER, NEW_WRITER)
    replace_once(path, OLD_CALL, NEW_CALL)


if __name__ == "__main__":
    main()
