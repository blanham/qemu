#!/usr/bin/env python3
"""Fetch a dependency-closed VC4 module bundle from pinned Pi firmware.

The Raspberry Pi firmware repository stores the module tree matching each
published kernel image. Resolve vc4.ko through modules.dep and modules.softdep,
download only the transitive hard and soft dependencies, decompress them
without altering module signatures, and emit a deterministic dependency-first
manifest for the guest initramfs loader.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from dataclasses import dataclass, field
import lzma
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request


@dataclass
class ModuleSoftDependencies:
    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)


def fetch(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "qemu-vc4-module-fixture/1"},
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return response.read()


def fetch_optional(url: str) -> bytes:
    try:
        return fetch(url)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            return b""
        raise


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


def normalize_module_name(name: str) -> str:
    basename = PurePosixPath(name).name
    for suffix in (".xz", ".zst", ".gz"):
        if basename.endswith(suffix):
            basename = basename[: -len(suffix)]
            break
    if basename.endswith(".ko"):
        basename = basename[:-3]
    return basename.replace("-", "_")


def parse_modules_softdep(text: str) -> dict[str, ModuleSoftDependencies]:
    dependencies: dict[str, ModuleSoftDependencies] = defaultdict(
        ModuleSoftDependencies
    )
    for line_number, raw_line in enumerate(text.splitlines(), 1):
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        words = line.split()
        if len(words) < 2 or words[0] != "softdep":
            continue

        module = normalize_module_name(words[1])
        section = "pre"
        for word in words[2:]:
            if word == "pre:":
                section = "pre"
                continue
            if word == "post:":
                section = "post"
                continue
            if word.endswith(":"):
                raise RuntimeError(
                    f"unsupported modules.softdep section {word!r} "
                    f"on line {line_number}"
                )
            target = normalize_module_name(word)
            target_list = getattr(dependencies[module], section)
            if target not in target_list:
                target_list.append(target)
    return dict(dependencies)


def module_stem(path: str) -> str:
    name = PurePosixPath(path).name
    for suffix in (".xz", ".zst", ".gz"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    return name


def module_path_index(
    dependencies: dict[str, list[str]],
) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in dependencies:
        name = normalize_module_name(path)
        previous = index.get(name)
        if previous is not None and previous != path:
            raise RuntimeError(
                f"ambiguous module name {name!r}: {previous!r}, {path!r}"
            )
        index[name] = path
    return index


def find_target(dependencies: dict[str, list[str]], target: str) -> str:
    index = module_path_index(dependencies)
    normalized = normalize_module_name(target)
    try:
        return index[normalized]
    except KeyError as error:
        raise RuntimeError(
            f"expected exactly one {target} module, found none"
        ) from error


def dependency_order(
    dependencies: dict[str, list[str]],
    target: str,
    soft_dependencies: dict[str, ModuleSoftDependencies] | None = None,
) -> list[str]:
    soft_dependencies = soft_dependencies or {}
    path_by_name = module_path_index(dependencies)
    visiting: set[str] = set()
    visited: set[str] = set()
    ordered: list[str] = []

    def resolve_soft_dependency(module: str, dependency: str) -> str:
        path = path_by_name.get(dependency)
        if path is None:
            raise RuntimeError(
                f"soft dependency {dependency!r} of {module!r} "
                "is absent from modules.dep"
            )
        return path

    def visit(path: str) -> None:
        if path in visited:
            return
        if path in visiting:
            raise RuntimeError(f"module dependency cycle at {path}")
        if path not in dependencies:
            raise RuntimeError(
                f"module dependency is absent from modules.dep: {path}"
            )

        visiting.add(path)
        module = normalize_module_name(path)

        # Preserve the existing hard-dependency order, then insert soft
        # predependencies immediately before the module that requested them.
        for dependency in dependencies[path]:
            visit(dependency)
        for dependency in soft_dependencies.get(
            module, ModuleSoftDependencies()
        ).pre:
            visit(resolve_soft_dependency(module, dependency))

        visiting.remove(path)
        visited.add(path)
        ordered.append(path)

        # Postdependencies are loaded after their requesting module.
        for dependency in soft_dependencies.get(
            module, ModuleSoftDependencies()
        ).post:
            visit(resolve_soft_dependency(module, dependency))

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
            [
                "zstd",
                "--quiet",
                "--decompress",
                "--force",
                str(source),
                "-o",
                str(output),
            ],
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
    modules_softdep_url = join_url(args.base_url, "modules.softdep")
    modules_dep_data = fetch(modules_dep_url)
    modules_softdep_data = fetch_optional(modules_softdep_url)
    dependencies = parse_modules_dep(
        modules_dep_data.decode("utf-8", errors="strict")
    )
    soft_dependencies = parse_modules_softdep(
        modules_softdep_data.decode("utf-8", errors="strict")
    )
    target_path = find_target(dependencies, args.target)
    ordered = dependency_order(
        dependencies, target_path, soft_dependencies
    )

    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    (out_dir / "modules.dep.source").write_bytes(modules_dep_data)
    (out_dir / "modules.softdep.source").write_bytes(modules_softdep_data)

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
        + "\n".join(provenance)
        + "\n",
        encoding="utf-8",
    )
    print(f"Fetched {len(ordered)} modules for {target_path}")
    for line in provenance:
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
