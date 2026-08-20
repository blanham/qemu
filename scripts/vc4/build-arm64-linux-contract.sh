#!/usr/bin/env bash
# Build the freestanding AArch64 Linux entry-contract witness.
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

case "${1:-}" in
    /*) out_dir=$1 ;;
    "") out_dir="$PWD/build/vc4-arm64-linux-contract" ;;
    *) out_dir="$PWD/$1" ;;
esac

src_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
assembler=${AARCH64_AS:-aarch64-linux-gnu-as}
linker=${AARCH64_LD:-aarch64-linux-gnu-ld}
objcopy=${AARCH64_OBJCOPY:-aarch64-linux-gnu-objcopy}
objdump=${AARCH64_OBJDUMP:-aarch64-linux-gnu-objdump}

rm -rf "$out_dir"
mkdir -p "$out_dir"

"$assembler" \
    -o "$out_dir/arm64-linux-contract.o" \
    "$src_dir/tests/vc4/arm64-linux-contract.S"
"$linker" \
    -nostdlib \
    -T "$src_dir/tests/vc4/arm64-linux-contract.ld" \
    -Map "$out_dir/arm64-linux-contract.map" \
    -o "$out_dir/arm64-linux-contract.elf" \
    "$out_dir/arm64-linux-contract.o"
"$objcopy" \
    -O binary \
    "$out_dir/arm64-linux-contract.elf" \
    "$out_dir/kernel8.img"
"$objdump" \
    -d -r -s \
    "$out_dir/arm64-linux-contract.elf" \
    >"$out_dir/arm64-linux-contract.dis"

"$objdump" -t "$out_dir/arm64-linux-contract.elf" \
    >"$out_dir/arm64-linux-contract.sym"

test -s "$out_dir/kernel8.img"
if "$objdump" -t "$out_dir/arm64-linux-contract.elf" | \
        grep -Eq '[[:space:]]UND[[:space:]]'; then
    echo "contract witness has undefined symbols" >&2
    exit 1
fi

sha256sum \
    "$out_dir/kernel8.img" \
    "$out_dir/arm64-linux-contract.elf" \
    >"$out_dir/SHA256SUMS"

printf 'Built VC4 ARM64 Linux contract witness at %s\n' "$out_dir"
