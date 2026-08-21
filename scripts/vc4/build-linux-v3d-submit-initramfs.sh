#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-v3d-submit-initramfs"}
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}
libdrm_include=${LIBDRM_INCLUDE:-/usr/include/libdrm}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi

for tool in "$cc" cpio gzip sha256sum strings; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required Linux V3D submit initramfs tool not found: %s\n' \
            "$tool" >&2
        exit 1
    fi
done
if [ ! -f "$libdrm_include/drm.h" ] || \
   [ ! -f "$libdrm_include/vc4_drm.h" ]; then
    printf 'libdrm UAPI headers not found below %s\n' "$libdrm_include" >&2
    exit 1
fi

rm -rf "$out_dir"
mkdir -p \
    "$out_dir/root/dev" \
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
    "$root_dir/tests/vc4/linux-v3d-submit-init.c"
chmod 0755 "$out_dir/root/init"

# Materialize strings once.  A strings|grep -q pipeline is racy under
# pipefail because a successful early grep exit delivers SIGPIPE to strings.
strings "$out_dir/root/init" > "$out_dir/init.strings"
for marker in \
    VC4_LINUX_DRM_SUBMIT_PROBE_START \
    VC4_LINUX_DRM_UAPI_OK \
    VC4_LINUX_DRM_SUBMIT_START \
    VC4_LINUX_DRM_SUBMIT_CL_OK \
    VC4_LINUX_DRM_SUBMIT_WAIT_OK \
    VC4_LINUX_DRM_SUBMIT_PIXELS_OK \
    VC4_LINUX_DRM_SUBMIT_OK \
    VC4_LINUX_DRM_SUBMIT_PROBE_DONE \
    VC4_LINUX_V3D_SUBMIT_DRIVER_OK; do
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

sha256sum \
    "$out_dir/root/init" \
    "$out_dir/initramfs.cpio" \
    "$out_dir/initramfs.cpio.gz" \
    > "$out_dir/SHA256SUMS"

printf 'Built VC4 Linux DRM submit initramfs at %s\n' "$out_dir"
cat "$out_dir/SHA256SUMS"
