#!/usr/bin/env python3
"""Copy the dynamic-library closure of AArch64 ELF files into an initramfs."""

from __future__ import annotations

import argparse
from collections import deque
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable


NEEDED_RE = re.compile(r"\(NEEDED\).*Shared library: \[([^\]]+)\]")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--seed", action="append", required=True)
    parser.add_argument("--search", action="append", default=[])
    parser.add_argument(
        "--readelf", default="aarch64-linux-gnu-readelf"
    )
    parser.add_argument(
        "--manifest", required=True, type=Path
    )
    return parser.parse_args()


def dynamic_dependencies(readelf: str, path: Path) -> list[str]:
    result = subprocess.run(
        [readelf, "-d", str(path)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return NEEDED_RE.findall(result.stdout)


def existing_candidates(root: Path, name: str) -> Iterable[Path]:
    preferred = (
        root / "usr/lib/aarch64-linux-gnu" / name,
        root / "lib/aarch64-linux-gnu" / name,
        root / "lib" / name,
        root / "usr/lib" / name,
    )
    for candidate in preferred:
        if candidate.exists():
            yield candidate
    yield from root.rglob(name)


def destination_for(root: Path, source: Path, soname: str) -> Path:
    text = str(source)
    if text.startswith("/lib/aarch64-linux-gnu/"):
        return root / "lib/aarch64-linux-gnu" / soname
    if text.startswith("/usr/lib/aarch64-linux-gnu/"):
        return root / "usr/lib/aarch64-linux-gnu" / soname
    if text.startswith("/usr/aarch64-linux-gnu/lib/"):
        return root / "lib/aarch64-linux-gnu" / soname
    return root / "usr/lib/aarch64-linux-gnu" / soname


def locate_library(root: Path, searches: list[Path],
                   name: str) -> tuple[Path, bool]:
    for candidate in existing_candidates(root, name):
        if candidate.exists():
            return candidate, True
    for directory in searches:
        candidate = directory / name
        if candidate.exists():
            return candidate, False
    raise FileNotFoundError(f"could not resolve AArch64 dependency {name}")


def copy_external(root: Path, source: Path, soname: str) -> Path:
    destination = destination_for(root, source, soname)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copy2(source.resolve(), destination)
    return destination


def resolve_seed(root: Path, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        raise FileNotFoundError(f"ELF closure seed does not exist: {path}")
    return path


def copy_interpreter(root: Path, searches: list[Path]) -> Path:
    names = ("ld-linux-aarch64.so.1",)
    candidates = [
        Path("/usr/aarch64-linux-gnu/lib"),
        Path("/lib/aarch64-linux-gnu"),
        *searches,
    ]
    for directory in candidates:
        for name in names:
            source = directory / name
            if source.exists():
                destination = root / "lib" / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source.resolve(), destination)
                return destination
    raise FileNotFoundError("could not locate ld-linux-aarch64.so.1")


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    searches = [Path(value).resolve() for value in args.search]
    queue: deque[Path] = deque(
        resolve_seed(root, value) for value in args.seed
    )
    seen_files: set[Path] = set()
    records: list[str] = []

    interpreter = copy_interpreter(root, searches)
    queue.append(interpreter)

    while queue:
        path = queue.popleft()
        real_path = path.resolve()
        if real_path in seen_files:
            continue
        seen_files.add(real_path)
        records.append(str(path.relative_to(root)))

        for soname in dynamic_dependencies(args.readelf, path):
            source, in_root = locate_library(root, searches, soname)
            dependency = source if in_root else copy_external(
                root, source, soname
            )
            queue.append(dependency)

    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        "\n".join(sorted(set(records))) + "\n",
        encoding="utf-8",
    )
    print(
        f"Copied {len(set(records))} AArch64 ELF objects below {root}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
