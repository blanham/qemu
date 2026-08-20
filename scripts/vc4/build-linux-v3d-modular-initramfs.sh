#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-v3d-modular-initramfs"}
module_bundle=${2:-"$root_dir/build/vc4-linux-v3d-modules"}
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}
libdrm_include=${LIBDRM_INCLUDE:-/usr/include/libdrm}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi
if [[ "$module_bundle" != /* ]]; then
    module_bundle="$PWD/$module_bundle"
fi

for tool in "$cc" cpio file gzip sha256sum strings; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required Linux V3D modular initramfs tool not found: %s\n' \
            "$tool" >&2
        exit 1
    fi
done
if [ ! -f "$libdrm_include/drm.h" ] || \
   [ ! -f "$libdrm_include/vc4_drm.h" ]; then
    printf 'libdrm UAPI headers not found below %s\n' "$libdrm_include" >&2
    exit 1
fi
if [ ! -s "$module_bundle/MANIFEST" ]; then
    printf 'VC4 module bundle has no MANIFEST: %s\n' "$module_bundle" >&2
    exit 1
fi

rm -rf "$out_dir"
mkdir -p \
    "$out_dir/root/dev" \
    "$out_dir/root/etc" \
    "$out_dir/root/lib/vc4-modules" \
    "$out_dir/root/proc" \
    "$out_dir/root/sys"

"$cc" \
    -static \
    -O2 \
    -g0 \
    -Wall \
    -Wextra \
    -Werror \
    -fno-ident \
    -I"$libdrm_include" \
    -Wl,--build-id=none \
    -o "$out_dir/root/init" \
    "$root_dir/tests/vc4/linux-v3d-modular-init.c"
chmod 0755 "$out_dir/root/init"

: > "$out_dir/root/etc/vc4-modules.manifest"
while IFS= read -r module; do
    if [ -z "$module" ]; then
        continue
    fi
    case "$module" in
        */*|.*)
            printf 'unsafe module bundle entry: %s\n' "$module" >&2
            exit 1
            ;;
    esac
    test -s "$module_bundle/$module"
    file "$module_bundle/$module" | grep -q 'ELF 64-bit LSB relocatable, ARM aarch64'
    cp "$module_bundle/$module" \
       "$out_dir/root/lib/vc4-modules/$module"
    printf '/lib/vc4-modules/%s\n' "$module" \
        >> "$out_dir/root/etc/vc4-modules.manifest"
done < "$module_bundle/MANIFEST"

test -s "$out_dir/root/etc/vc4-modules.manifest"
cp "$module_bundle/MANIFEST" "$out_dir/MODULE_MANIFEST"
cp "$module_bundle/PROVENANCE" "$out_dir/MODULE_PROVENANCE"

for marker in \
    VC4_LINUX_V3D_MODULAR_START \
    VC4_LINUX_MODULE_LOAD_START \
    VC4_LINUX_MODULE_LOAD_OK \
    VC4_LINUX_MODULE_LOAD_DONE \
    VC4_LINUX_MODULE_CLOSURE_OK \
    VC4_LINUX_DRM_UAPI_OK \
    VC4_LINUX_DRM_SUBMIT_OK \
    VC4_LINUX_V3D_MODULAR_DONE \
    VC4_LINUX_V3D_MODULAR_OK; do
    strings "$out_dir/root/init" | grep -Fq "$marker"
done

(
    cd "$out_dir/root"
    find . -print0 | LC_ALL=C sort -z | \
        cpio --null --quiet -o --format=newc --owner=0:0 \
        > "$out_dir/initramfs.cpio"
)
gzip -n -9 -c "$out_dir/initramfs.cpio" \
    > "$out_dir/initramfs.cpio.gz"

test -x "$out_dir/root/init"
test -s "$out_dir/initramfs.cpio"
test -s "$out_dir/initramfs.cpio.gz"
gzip -t "$out_dir/initramfs.cpio.gz"
cpio --quiet -it < "$out_dir/initramfs.cpio" > "$out_dir/CONTENTS"
grep -Eq '^(\./)?init$' "$out_dir/CONTENTS"
grep -Eq '^(\./)?etc/vc4-modules.manifest$' "$out_dir/CONTENTS"
grep -Eq '^(\./)?lib/vc4-modules/' "$out_dir/CONTENTS"

sha256sum \
    "$out_dir/root/init" \
    "$out_dir/root/etc/vc4-modules.manifest" \
    "$out_dir/initramfs.cpio" \
    "$out_dir/initramfs.cpio.gz" \
    > "$out_dir/SHA256SUMS"

printf 'Built VC4 Linux modular DRM initramfs at %s\n' "$out_dir"
cat "$out_dir/SHA256SUMS"
