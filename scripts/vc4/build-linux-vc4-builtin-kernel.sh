#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 2 ]; then
    printf 'usage: %s LINUX_SOURCE OUTPUT_DIR\n' "$0" >&2
    exit 2
fi

linux_source=$(cd "$1" && pwd)
out_dir=$2
arch=${ARCH:-arm64}
cross_compile=${CROSS_COMPILE:-aarch64-linux-gnu-}
jobs=${JOBS:-$(nproc)}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi

for tool in "${cross_compile}gcc" make sha256sum; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required built-in VC4 kernel tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done
if [ ! -x "$linux_source/scripts/config" ]; then
    printf 'Linux scripts/config is missing: %s\n' "$linux_source" >&2
    exit 1
fi

mkdir -p "$out_dir"
if [ ! -s "$linux_source/.config" ]; then
    make -C "$linux_source" \
        ARCH="$arch" CROSS_COMPILE="$cross_compile" \
        bcm2711_defconfig
fi

config="$linux_source/scripts/config"
"$config" --file "$linux_source/.config" \
    --enable ARCH_BCM2835 \
    --enable BLK_DEV_INITRD \
    --enable DEVTMPFS \
    --enable DEVTMPFS_MOUNT \
    --enable DRM \
    --enable DRM_VC4 \
    --enable DRM_FBDEV_EMULATION \
    --enable FB \
    --enable FRAMEBUFFER_CONSOLE \
    --enable IKCONFIG \
    --enable IKCONFIG_PROC \
    --enable RD_GZIP \
    --enable SERIAL_AMBA_PL011 \
    --enable SERIAL_AMBA_PL011_CONSOLE \
    --disable LOCALVERSION_AUTO \
    --set-str LOCALVERSION "-vc4-ci"

# Keep the purpose-built fixture small and deterministic without changing any
# functional platform options inherited from the pinned production config.
"$config" --file "$linux_source/.config" \
    --disable DEBUG_INFO \
    --disable DEBUG_INFO_BTF \
    --disable DEBUG_INFO_DWARF4 \
    --disable DEBUG_INFO_DWARF5 \
    --disable WERROR || true

make -C "$linux_source" \
    ARCH="$arch" CROSS_COMPILE="$cross_compile" \
    olddefconfig

grep -q '^CONFIG_DRM=y$' "$linux_source/.config"
grep -q '^CONFIG_DRM_VC4=y$' "$linux_source/.config"
grep -q '^CONFIG_DEVTMPFS=y$' "$linux_source/.config"
grep -q '^CONFIG_BLK_DEV_INITRD=y$' "$linux_source/.config"

make -C "$linux_source" \
    ARCH="$arch" CROSS_COMPILE="$cross_compile" \
    -j"$jobs" Image

test -s "$linux_source/arch/arm64/boot/Image"
cp "$linux_source/arch/arm64/boot/Image" "$out_dir/kernel8-vc4-builtin.img"
cp "$linux_source/.config" "$out_dir/kernel.config"
make -s -C "$linux_source" \
    ARCH="$arch" CROSS_COMPILE="$cross_compile" \
    kernelrelease > "$out_dir/KERNELRELEASE"

sha256sum \
    "$out_dir/kernel8-vc4-builtin.img" \
    "$out_dir/kernel.config" \
    "$out_dir/KERNELRELEASE" \
    > "$out_dir/SHA256SUMS"

printf 'Built firmware-matched kernel with built-in VC4 at %s\n' "$out_dir"
cat "$out_dir/KERNELRELEASE"
cat "$out_dir/SHA256SUMS"
