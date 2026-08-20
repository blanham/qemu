#!/usr/bin/env bash
# Build the freestanding AArch64 /init and deterministic initramfs.
# SPDX-License-Identifier: GPL-2.0-or-later

set -euo pipefail

root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
out=${1:-"$root/build/vc4-linux-initramfs"}
cross=${CROSS_COMPILE:-aarch64-linux-gnu-}

mkdir -p "$out"

"${cross}gcc" \
    -mcpu=cortex-a53 \
    -O2 \
    -g \
    -std=c11 \
    -ffreestanding \
    -fno-builtin \
    -fno-common \
    -fno-pic \
    -fno-pie \
    -fno-stack-protector \
    -fno-asynchronous-unwind-tables \
    -fno-unwind-tables \
    -mgeneral-regs-only \
    -Wall \
    -Wextra \
    -Werror \
    -nostdlib \
    -static \
    -no-pie \
    -Wl,--build-id=none \
    -Wl,-z,max-page-size=4096 \
    -Wl,-e,_start \
    -Wl,-Ttext=0x00400000 \
    -o "$out/init" \
    "$root/tests/vc4/linux-init.c"

"${cross}readelf" -aW "$out/init" > "$out/init.readelf"
"${cross}objdump" -drwC "$out/init" > "$out/init.dis"

if "${cross}readelf" -lW "$out/init" | grep -q 'INTERP'; then
    echo "init unexpectedly contains a program interpreter" >&2
    exit 1
fi

python3 "$root/scripts/vc4/build-initramfs.py" \
    --init "$out/init" \
    --cpio "$out/initramfs.cpio" \
    --gzip "$out/initramfs.cpio.gz"

printf '%s\n' "$out/initramfs.cpio.gz"
