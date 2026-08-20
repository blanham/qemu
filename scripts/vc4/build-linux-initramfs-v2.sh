#!/usr/bin/env bash
# Build the diagnostic VC4 Linux initramfs reproducibly.
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

OUT_DIR=$(realpath -m -- "${1:-build/vc4-linux-initramfs-v2}")
ROOT_DIR="$OUT_DIR/root"
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-0}
CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
CC=${CC:-${CROSS_COMPILE}gcc}

rm -rf "$OUT_DIR"
mkdir -p \
  "$ROOT_DIR/dev" \
  "$ROOT_DIR/proc" \
  "$ROOT_DIR/sys" \
  "$ROOT_DIR/tmp"
chmod 1777 "$ROOT_DIR/tmp"

"$CC" \
  -static \
  -std=gnu11 \
  -Os \
  -ffunction-sections \
  -fdata-sections \
  -Wall \
  -Wextra \
  -Werror \
  -Wl,--gc-sections \
  -Wl,-z,noexecstack \
  tests/vc4/linux-init-v2.c \
  -o "$ROOT_DIR/init"

"${CROSS_COMPILE}strip" --strip-all "$ROOT_DIR/init"
file "$ROOT_DIR/init" | grep -q 'ARM aarch64'
file "$ROOT_DIR/init" | grep -q 'statically linked'

# cpio --reproducible fixes inode metadata; fixed mtimes make the archive
# byte-identical across hosts and both relative/absolute output paths.
find "$ROOT_DIR" -print0 | xargs -0 touch -h -d "@${SOURCE_DATE_EPOCH}"
(
  cd "$ROOT_DIR"
  find . -mindepth 1 -print0 \
    | LC_ALL=C sort -z \
    | cpio --null --create --format=newc --reproducible --owner=0:0
) >"$OUT_DIR/initramfs.cpio"

test -s "$OUT_DIR/initramfs.cpio"
(
  cd "$OUT_DIR"
  sha256sum initramfs.cpio root/init >SHA256SUMS
)

printf 'Built diagnostic VC4 Linux initramfs at %s\n' "$OUT_DIR"
