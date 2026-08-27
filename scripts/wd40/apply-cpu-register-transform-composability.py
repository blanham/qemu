#!/usr/bin/env python3
'''Make WD40 CPU-register transforms composable and style-clean.'''

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
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            f"{path}: unexpected CPU-register transform state: "
            f"old={old_count}, new={new_count}"
        )
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


def main() -> None:
    replace_once(
        "scripts/wd40/apply-cpu-register-snapshots.py",
        '''        owned_markers=(
            "Cross-architecture CPU register snapshots",
            "x-wd40-query-cpu-registers",
        ),
''',
        '''        owned_markers=(
            "Cross-architecture CPU register snapshots",
            "without scraping ``info registers`` output",
        ),
''',
    )
    replace_once(
        "scripts/wd40/apply-cpu-register-write-service.py",
        '''        owned_markers=(
            "static int wd40_register_hex_digit",
            "wd40_register_descriptor_for_number",
            "qmp_x_wd40_write_cpu_register",
        ),
''',
        '''        owned_markers=(
            "static int wd40_register_hex_digit",
            "static bool wd40_register_descriptor_for_number(\\n",
            "qmp_x_wd40_write_cpu_register",
        ),
''',
    )
    replace_once(
        "scripts/wd40/apply-cpu-register-write-service.py",
        '''# The hexadecimal value must encode exactly the current register width.
''',
        '''# The hexadecimal value must encode exactly the current register
# width.
''',
    )
    replace_once(
        "scripts/wd40/apply-cpu-register-write-service.py",
        '''        owned_markers=(
            "Typed CPU register writes",
            "x-wd40-write-cpu-register",
            "fresh read-back",
        ),
''',
        '''        owned_markers=(
            "Typed CPU register writes",
            "fresh read-back",
            "target masking, normalization",
        ),
''',
    )


if __name__ == "__main__":
    main()
