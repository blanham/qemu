#!/usr/bin/env bash
# Build direct-entry and Linux-jump AArch64 witnesses.
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

OUT_DIR=$(realpath -m -- "${1:-build/vc4-direct-arm-entry}")
CROSS_COMPILE=${CROSS_COMPILE:-aarch64-linux-gnu-}
AS=${AS:-${CROSS_COMPILE}as}
LD=${LD:-${CROSS_COMPILE}ld}
OBJCOPY=${OBJCOPY:-${CROSS_COMPILE}objcopy}
OBJDUMP=${OBJDUMP:-${CROSS_COMPILE}objdump}

rm -rf "$OUT_DIR"
mkdir -p "$OUT_DIR"

build_one()
{
    local name=$1
    local source=$2

    "$AS" -o "$OUT_DIR/$name.o" "$source"
    "$LD" -nostdlib --entry=_start -Ttext=0x80000 \
        -o "$OUT_DIR/$name.elf" "$OUT_DIR/$name.o"
    "$OBJCOPY" -O binary \
        "$OUT_DIR/$name.elf" "$OUT_DIR/$name.bin"
    "$OBJDUMP" -d "$OUT_DIR/$name.elf" >"$OUT_DIR/$name.dis"
    test -s "$OUT_DIR/$name.bin"
}

build_one direct-arm-entry tests/vc4/direct-arm-entry.S
build_one linux-entry-trampoline tests/vc4/linux-entry-trampoline.S

(
    cd "$OUT_DIR"
    sha256sum \
        direct-arm-entry.bin \
        linux-entry-trampoline.bin \
        >SHA256SUMS
)

printf 'Built VC4 direct ARM entry witnesses at %s\n' "$OUT_DIR"
