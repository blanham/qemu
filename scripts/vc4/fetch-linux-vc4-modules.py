#!/usr/bin/env python3
"""Fetch a dependency-closed VC4 module bundle from pinned Pi firmware.

The Raspberry Pi firmware repository stores the module tree matching each
published kernel image.  Resolve vc4.ko through modules.dep, download only the
transitive module dependencies, decompress them without altering module
signatures, and emit a deterministic dependency-first manifest for the guest
initramfs loader.
"""

from __future__ import annotations

import argparse
import lzma
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "qemu-vc4-module-fixture/1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def join_url(base: str, path: str) -> str:
    return base.rstrip("/") + "/" + urllib.parse.quote(path, safe="/")


def parse_modules_dep(text: str) -> dict[str, list[str]]:
    dependencies: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        module, raw_dependencies = line.split(":", 1)
        module = module.strip()
        if not module:
            continue
        dependencies[module] = raw_dependencies.split()
    return dependencies


def module_stem(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in (".xz", ".zst", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def find_target(dependencies: dict[str, list[str]], target: str) -> str:
    matches = [
        path for path in dependencies
        if module_stem(path) == target or module_stem(path) == target + ".ko"
    ]
    if len(matches) != 1:
        raise RuntimeError(
            f"expected exactly one {target} module, found {matches!r}"
        )
    return matches[0]


def dependency_order(dependencies: dict[str, list[str]], target: str) -> list[str]:
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def visit(path: str) -> None:
        if path in visited:
            return
        if path in visiting:
            raise RuntimeError(f"module dependency cycle at {path}")
        if path not in dependencies:
            raise RuntimeError(f"module dependency is absent from modules.dep: {path}")
        visiting.add(path)
        for dependency in dependencies[path]:
            visit(dependency)
        visiting.remove(path)
        visited.add(path)
        ordered.append(path)

    visit(target)
    return ordered


def decompress_zstd(data: bytes) -> bytes:
    if shutil.which("zstd") is None:
        raise RuntimeError("zstd is required for .ko.zst module files")
    with tempfile.TemporaryDirectory(prefix="vc4-module-zstd-") as temp_s:
        temp = Path(temp_s)
        source = temp / "module.ko.zst"
        output = temp / "module.ko"
        source.write_bytes(data)
        subprocess.run(
            ["zstd", "--quiet", "--decompress", "--force",
             str(source), "-o", str(output)],
            check=True,
        )
        return output.read_bytes()


def decompress(path: str, data: bytes) -> bytes:
    if path.endswith(".xz"):
        return lzma.decompress(data)
    if path.endswith(".zst"):
        return decompress_zstd(data)
    if path.endswith(".gz"):
        import gzip
        return gzip.decompress(data)
    return data


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--target", default="vc4")
    args = parser.parse_args()

    modules_dep_url = join_url(args.base_url, "modules.dep")
    modules_dep_data = fetch(modules_dep_url)
    dependencies = parse_modules_dep(
        modules_dep_data.decode("utf-8", errors="strict")
    )
    target_path = find_target(dependencies, args.target)
    ordered = dependency_order(dependencies, target_path)

    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "modules.dep.source").write_bytes(modules_dep_data)

    manifest: list[str] = []
    provenance: list[str] = []
    for index, path in enumerate(ordered):
        raw = fetch(join_url(args.base_url, path))
        module = decompress(path, raw)
        if not module.startswith(b"\x7fELF"):
            raise RuntimeError(f"decompressed module is not ELF: {path}")
        destination_name = f"{index:03d}-{module_stem(path)}"
        destination = out_dir / destination_name
        destination.write_bytes(module)
        manifest.append(destination_name)
        provenance.append(
            f"{destination_name}\t{len(raw)}\t{len(module)}\t{path}"
        )

    (out_dir / "MANIFEST").write_text(
        "\n".join(manifest) + "\n", encoding="utf-8"
    )
    (out_dir / "PROVENANCE").write_text(
        "destination\tcompressed_bytes\tmodule_bytes\tsource\n"
        + "\n".join(provenance) + "\n",
        encoding="utf-8",
    )
    print(f"Fetched {len(ordered)} modules for {target_path}")
    for line in provenance:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
