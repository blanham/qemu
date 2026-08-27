#!/usr/bin/env python3
"""Harden WD40 memory-write semantics and PowerPC virtual coverage."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: str, old: str, new: str) -> None:
    file_path = ROOT / path
    text = file_path.read_text(encoding="utf-8")
    old_count = text.count(old)
    new_count = text.count(new)
    if old_count == 0 and new_count == 1:
        return
    if old_count == 1 and new_count == 0:
        file_path.write_text(text.replace(old, new, 1), encoding="utf-8")
        return
    raise RuntimeError(
        f"{path}: ambiguous memory-write hardening: "
        f"old={old_count}, new={new_count}"
    )


def main() -> None:
    replace_once(
        "scripts/ci/check-wd40-memory-write-service.py",
        """        target="ppc",
        address=0x10000,
        virtual=False,
""",
        """        target="ppc",
        address=0x10000,
        virtual=True,
""",
    )

    replace_once(
        "scripts/ci/check-wd40-memory-write-service.py",
        """        "virtual debug writes can modify ROM",
        "read-modify-write",
    )
""",
        """        "virtual debug writes can modify ROM",
        "read-modify-write",
        "Writes are not atomic",
        "Neither virtual nor physical writes are atomic",
        "does not roll it back",
    )
""",
    )

    old_qapi = """# Write between 1 byte and 1 MiB to guest virtual or physical
# memory from an exact hexadecimal byte string.
#
# @space: address space used for the write
"""
    new_qapi = """# Write between 1 byte and 1 MiB to guest virtual or physical
# memory from an exact hexadecimal byte string.
#
# Writes are not atomic.  A failure may leave an earlier portion of
# the requested range modified.
#
# @space: address space used for the write
"""
    replace_once("qapi/machine.json", old_qapi, new_qapi)
    replace_once("scripts/wd40/apply-memory-write-service.py", old_qapi, new_qapi)

    old_docs = """Writes are debugger operations rather than side-effect-free RAM edits.  They
can invoke MMIO callbacks, and virtual debug writes can modify ROM through
QEMU's debugger path.  The command synchronizes accelerator state but does not
pause a running guest; clients should issue ``stop`` before read-modify-write
work that must be coherent.
"""
    new_docs = """Writes are debugger operations rather than side-effect-free RAM edits.  They
can invoke MMIO callbacks, and virtual debug writes can modify ROM through
QEMU's debugger path.  Neither virtual nor physical writes are atomic.  If a
multi-byte request fails, an earlier portion of the range may already have
been modified; QEMU does not roll it back.  Clients that need all-or-nothing
behavior must arrange their own validation and rollback.

The command synchronizes accelerator state but does not pause a running guest;
clients should issue ``stop`` before read-modify-write work that must be
coherent.
"""
    replace_once("docs/devel/wd40-monitor-v2.rst", old_docs, new_docs)
    replace_once("scripts/wd40/apply-memory-write-service.py", old_docs, new_docs)


if __name__ == "__main__":
    main()
