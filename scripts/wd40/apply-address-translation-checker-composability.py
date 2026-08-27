#!/usr/bin/env python3
"""Keep the address-translation checker scoped as later services compose."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

OLD = (
    'def implementation_block() -> str:\n'
    '    text = source("system/physmem-qmp-cmds.c")\n'
    '    start_marker = "WD40AddressTranslation *\\n"\n'
    '    end_marker = "void qmp_memsave(uint64_t addr"\n'
    '    start = text.find(start_marker)\n'
    '    end = text.find(end_marker, start + len(start_marker))\n'
    '    if start < 0 or end < 0:\n'
    '        raise SystemExit(\n'
    '            "system/physmem-qmp-cmds.c: could not isolate "\n'
    '            "address-translation block"\n'
    '        )\n'
    '    return text[start:end]\n'
)
NEW = (
    'def implementation_block() -> str:\n'
    '    text = source("system/physmem-qmp-cmds.c")\n'
    '    start_marker = "WD40AddressTranslation *\\n"\n'
    '    start = text.find(start_marker)\n'
    '    if start < 0:\n'
    '        raise SystemExit(\n'
    '            "system/physmem-qmp-cmds.c: could not isolate "\n'
    '            "address-translation block"\n'
    '        )\n'
    '\n'
    '    end_markers = (\n'
    '        "#define WD40_MEMORY_WRITE_MAX",\n'
    '        "void qmp_memsave(uint64_t addr",\n'
    '    )\n'
    '    search_from = start + len(start_marker)\n'
    '    end_candidates = [\n'
    '        text.find(marker, search_from)\n'
    '        for marker in end_markers\n'
    '    ]\n'
    '    end_candidates = [end for end in end_candidates if end >= 0]\n'
    '    if not end_candidates:\n'
    '        raise SystemExit(\n'
    '            "system/physmem-qmp-cmds.c: could not isolate "\n'
    '            "address-translation block"\n'
    '        )\n'
    '    return text[start:min(end_candidates)]\n'
)


def main() -> None:
    path = ROOT / "scripts/ci/check-wd40-address-translation-service.py"
    text = path.read_text(encoding="utf-8")
    old_count = text.count(OLD)
    new_count = text.count(NEW)
    if old_count == 0 and new_count == 1:
        return
    if old_count != 1 or new_count != 0:
        raise RuntimeError(
            f"{path}: ambiguous translation-checker scope: "
            f"old={old_count}, new={new_count}"
        )
    path.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")


if __name__ == "__main__":
    main()
