#!/usr/bin/env python3
"""Wire the validated address-translation service into WD40 integration CI."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INTEGRATION = ROOT / ".github/workflows/wd40-qol-integration.yml"
FEATURE = ROOT / ".github/workflows/wd40-address-translation-service.yml"


def replace_exact(text: str, old: str, new: str, *, count: int = 1) -> str:
    actual = text.count(old)
    if actual != count:
        raise RuntimeError(
            f"expected {count} copies of integration anchor, found {actual}: "
            f"{old!r}"
        )
    return text.replace(old, new)


def main() -> None:
    text = INTEGRATION.read_text(encoding="utf-8")

    text = replace_exact(
        text,
        """      - wd40/qol-memory-read-integration
  workflow_dispatch:
""",
        """      - wd40/qol-memory-read-integration
      - wd40/qol-address-translation-integration
  workflow_dispatch:
""",
    )
    text = replace_exact(
        text,
        """            python3 scripts/wd40/apply-memory-read-service.py
          }
""",
        """            python3 scripts/wd40/apply-memory-read-service.py
            python3 scripts/wd40/apply-address-translation-service.py
          }
""",
    )
    text = replace_exact(
        text,
        """          python3 scripts/ci/check-wd40-memory-read-service.py
          git diff --check
""",
        """          python3 scripts/ci/check-wd40-memory-read-service.py
          python3 scripts/ci/check-wd40-address-translation-service.py
          git diff --check
""",
        count=2,
    )
    text = replace_exact(
        text,
        """      - name: Publish the validated integration history
""",
        """      - name: Exercise x86-64 address translation
        run: |
          set -Eeuo pipefail
          python3 scripts/ci/check-wd40-address-translation-service.py \\
            build-qol-integration x86_64

      - name: Exercise AArch64 address translation
        run: |
          set -Eeuo pipefail
          python3 scripts/ci/check-wd40-address-translation-service.py \\
            build-qol-integration aarch64

      - name: Exercise m68k address translation
        run: |
          set -Eeuo pipefail
          python3 scripts/ci/check-wd40-address-translation-service.py \\
            build-qol-integration m68k

      - name: Exercise PowerPC address translation
        run: |
          set -Eeuo pipefail
          python3 scripts/ci/check-wd40-address-translation-service.py \\
            build-qol-integration ppc

      - name: Publish the validated integration history
""",
    )
    INTEGRATION.write_text(text, encoding="utf-8")

    feature = FEATURE.read_text(encoding="utf-8")
    feature = replace_exact(
        feature,
        """            python3 scripts/wd40/apply-address-translation-ppc-reset-mapping.py
""",
        "",
    )
    FEATURE.write_text(feature, encoding="utf-8")


if __name__ == "__main__":
    main()
