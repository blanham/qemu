#!/usr/bin/env bash
# Build the freestanding stock-firmware framebuffer witness.
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
out=${1:-"$root/build/vc4-stock-framebuffer"}
cross=${CROSS_COMPILE:-aarch64-linux-gnu-}

mkdir -p "$out"

common_cflags=(
    -mcpu=cortex-a53
    -O2
    -g
    -ffreestanding
    -fno-builtin
    -fno-common
    -fno-pic
    -fno-pie
    -fno-stack-protector
    -mgeneral-regs-only
    -Wall
    -Wextra
    -Werror
)

"${cross}gcc" "${common_cflags[@]}" \
    -c "$root/tests/vc4/stock-framebuffer-start.S" \
    -o "$out/stock-framebuffer-start.o"
"${cross}gcc" "${common_cflags[@]}" \
    -std=c11 \
    -c "$root/tests/vc4/stock-framebuffer.c" \
    -o "$out/stock-framebuffer.o"

"${cross}ld" \
    -nostdlib \
    --build-id=none \
    -T "$root/tests/vc4/stock-framebuffer.ld" \
    -Map "$out/stock-framebuffer.map" \
    -o "$out/stock-framebuffer.elf" \
    "$out/stock-framebuffer-start.o" \
    "$out/stock-framebuffer.o"

"${cross}objcopy" -O binary \
    "$out/stock-framebuffer.elf" \
    "$out/kernel8.img"
"${cross}objdump" -drwC \
    "$out/stock-framebuffer.elf" \
    > "$out/stock-framebuffer.dis"
"${cross}readelf" -aW \
    "$out/stock-framebuffer.elf" \
    > "$out/stock-framebuffer.readelf"

test -s "$out/kernel8.img"
printf '%s\n' "$out/kernel8.img"
