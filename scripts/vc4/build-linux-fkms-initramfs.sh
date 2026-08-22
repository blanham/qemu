#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-fkms-initramfs"}
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
        printf 'required Linux FKMS initramfs tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done
for header in drm.h drm_mode.h drm_fourcc.h vc4_drm.h; do
    if [ ! -f "$libdrm_include/$header" ]; then
        printf 'libdrm UAPI header not found: %s/%s\n' \
            "$libdrm_include" "$header" >&2
        exit 1
    fi
done
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
    "$root_dir/tests/vc4/linux-fkms-init.c"
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
    file "$module_bundle/$module" > "$out_dir/module.file"
    grep -Fq 'ELF 64-bit LSB relocatable, ARM aarch64' \
        "$out_dir/module.file"
    cp "$module_bundle/$module" \
       "$out_dir/root/lib/vc4-modules/$module"
    printf '/lib/vc4-modules/%s\n' "$module" \
        >> "$out_dir/root/etc/vc4-modules.manifest"
done < "$module_bundle/MANIFEST"
rm -f "$out_dir/module.file"

test -s "$out_dir/root/etc/vc4-modules.manifest"
cp "$module_bundle/MANIFEST" "$out_dir/MODULE_MANIFEST"
cp "$module_bundle/PROVENANCE" "$out_dir/MODULE_PROVENANCE"

strings "$out_dir/root/init" > "$out_dir/init.strings"
for marker in \
    VC4_LINUX_FKMS_START \
    VC4_LINUX_MODULE_CLOSURE_OK \
    VC4_LINUX_FKMS_RESOURCES_OK \
    VC4_LINUX_FKMS_CONNECTOR_OK \
    VC4_LINUX_FKMS_CREATE_DUMB_OK \
    VC4_LINUX_FKMS_MMAP_OK \
    VC4_LINUX_FKMS_ADDFB_OK \
    VC4_LINUX_FKMS_MODESET_OK \
    VC4_LINUX_FKMS_SCANOUT_OK \
    VC4_LINUX_FKMS_OK \
    VC4_LINUX_FB_OK; do
    grep -Fq "$marker" "$out_dir/init.strings"
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

printf 'Built VC4 Linux firmware-KMS initramfs at %s\n' "$out_dir"
cat "$out_dir/SHA256SUMS"
