#!/usr/bin/env python3
"""Repair the one malformed empty-string literal in scalar-fpu-fixup.py."""

from pathlib import Path

path = Path(__file__).with_name("scalar-fpu-fixup.py")
text = path.read_text(encoding="utf-8")
bad = '''    """,
    "obsolete floating-point rejection",
'''
good = '''    "",
    "obsolete floating-point rejection",
'''
if bad in text:
    path.write_text(text.replace(bad, good, 1), encoding="utf-8")
elif good not in text:
    raise SystemExit("could not locate scalar-FPU empty replacement literal")
