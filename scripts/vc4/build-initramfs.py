#!/usr/bin/env python3
"""Build a deterministic newc initramfs around the VC4 Linux /init binary."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import gzip
from io import BytesIO
from pathlib import Path
import stat


@dataclass(frozen=True)
class Entry:
    name: str
    mode: int
    data: bytes = b""
    rdev_major: int = 0
    rdev_minor: int = 0


def pad4(output: bytearray) -> None:
    output.extend(b"\0" * (-len(output) & 3))


def append_newc(output: bytearray, entry: Entry, inode: int) -> None:
    name = entry.name.encode("utf-8") + b"\0"
    fields = (
        inode,
        entry.mode,
        0,                          # uid
        0,                          # gid
        2 if stat.S_ISDIR(entry.mode) else 1,
        0,                          # mtime
        len(entry.data),
        0,                          # dev major
        0,                          # dev minor
        entry.rdev_major,
        entry.rdev_minor,
        len(name),
        0,                          # checksum for newc
    )
    header = b"070701" + b"".join(f"{value:08x}".encode("ascii")
                                    for value in fields)
    if len(header) != 110:
        raise AssertionError(f"newc header has {len(header)} bytes")
    output.extend(header)
    output.extend(name)
    pad4(output)
    output.extend(entry.data)
    pad4(output)


def build_archive(init: bytes) -> bytes:
    entries = (
        Entry("dev", stat.S_IFDIR | 0o755),
        Entry("dev/console", stat.S_IFCHR | 0o600,
              rdev_major=5, rdev_minor=1),
        Entry("dev/null", stat.S_IFCHR | 0o666,
              rdev_major=1, rdev_minor=3),
        Entry("dev/fb0", stat.S_IFCHR | 0o600,
              rdev_major=29, rdev_minor=0),
        Entry("init", stat.S_IFREG | 0o755, init),
    )
    output = bytearray()
    for inode, entry in enumerate(entries, 1):
        append_newc(output, entry, inode)
    append_newc(output, Entry("TRAILER!!!", 0), len(entries) + 1)
    output.extend(b"\0" * (-len(output) & 511))
    return bytes(output)


def compress_deterministically(data: bytes) -> bytes:
    output = BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output,
                       mtime=0, compresslevel=9) as stream:
        stream.write(data)
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--init", required=True, type=Path)
    parser.add_argument("--cpio", required=True, type=Path)
    parser.add_argument("--gzip", required=True, type=Path)
    args = parser.parse_args()

    if not args.init.is_file():
        parser.error(f"not a file: {args.init}")
    archive = build_archive(args.init.read_bytes())
    args.cpio.parent.mkdir(parents=True, exist_ok=True)
    args.gzip.parent.mkdir(parents=True, exist_ok=True)
    args.cpio.write_bytes(archive)
    args.gzip.write_bytes(compress_deterministically(archive))
    print(f"wrote {args.cpio} ({len(archive)} bytes)")
    print(f"wrote {args.gzip} ({args.gzip.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
