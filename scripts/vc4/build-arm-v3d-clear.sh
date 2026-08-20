#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-arm-v3d-clear"}
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}
ld=${AARCH64_LD:-aarch64-linux-gnu-ld}
objcopy=${AARCH64_OBJCOPY:-aarch64-linux-gnu-objcopy}
objdump=${AARCH64_OBJDUMP:-aarch64-linux-gnu-objdump}

for tool in "$cc" "$ld" "$objcopy" "$objdump" sha256sum grep; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required V3D witness build tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done

rm -rf "$out_dir"
mkdir -p "$out_dir"

"$cc" \
    -c \
    -mcpu=cortex-a53 \
    -ffreestanding \
    -fno-pie \
    -fno-pic \
    -fno-stack-protector \
    -fno-unwind-tables \
    -fno-asynchronous-unwind-tables \
    -Wall \
    -Wextra \
    -Werror \
    -o "$out_dir/start.o" \
    "$root_dir/tests/vc4/arm-framebuffer-start.S"

"$cc" \
    -c \
    -mcpu=cortex-a53 \
    -O2 \
    -ffreestanding \
    -fno-builtin \
    -fno-pie \
    -fno-pic \
    -fno-stack-protector \
    -fno-unwind-tables \
    -fno-asynchronous-unwind-tables \
    -include stdbool.h \
    -Wall \
    -Wextra \
    -Werror \
    -o "$out_dir/v3d-clear.o" \
    "$root_dir/tests/vc4/arm-v3d-clear.c"

"$ld" \
    -nostdlib \
    -static \
    -T "$root_dir/tests/vc4/arm-framebuffer.ld" \
    -o "$out_dir/arm-v3d-clear.elf" \
    "$out_dir/start.o" \
    "$out_dir/v3d-clear.o"

"$objcopy" -O binary \
    "$out_dir/arm-v3d-clear.elf" \
    "$out_dir/kernel8.img"
"$objdump" -d "$out_dir/arm-v3d-clear.elf" \
    > "$out_dir/arm-v3d-clear.dis"

if "$objdump" -t "$out_dir/arm-v3d-clear.elf" |
        grep -Eq '(^|[[:space:]])U([[:space:]]|$)'; then
    echo "bare-metal V3D witness contains unresolved symbols" >&2
    exit 1
fi

sha256sum \
    "$out_dir/arm-v3d-clear.elf" \
    "$out_dir/kernel8.img" \
    > "$out_dir/SHA256SUMS"

printf 'Built VC4 bare-metal V3D clear kernel at %s\n' "$out_dir"
