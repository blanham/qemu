#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-v3d-boundary-initramfs"}
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi

for tool in "$cc" cpio gzip sha256sum strings; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required Linux V3D boundary initramfs tool not found: %s\n' \
            "$tool" >&2
        exit 1
    fi
done

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
    -Wl,--build-id=none \
    -o "$out_dir/root/init" \
    "$root_dir/tests/vc4/linux-v3d-boundary-init.c"
chmod 0755 "$out_dir/root/init"

# Do not pipe strings(1) into grep -q under pipefail: grep exits as soon as it
# finds a marker and strings then reports SIGPIPE, turning a successful marker
# check into status 141.  Materialize the finite output once and inspect it.
strings "$out_dir/root/init" > "$out_dir/init.strings"
for marker in \
    VC4_LINUX_V3D_BOUNDARY_START \
    VC4_LINUX_BIND_EVIDENCE_BEGIN \
    VC4_LINUX_V3D_BIND_WRITE_OK \
    VC4_LINUX_DRM_UAPI_START \
    VC4_LINUX_DRM_UAPI_OK \
    VC4_LINUX_DRM_WAIT_BO_START \
    VC4_LINUX_DRM_WAIT_BO_OK \
    VC4_LINUX_V3D_BOUNDARY_DONE \
    VC4_LINUX_V3D_BOUNDARY_OK; do
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

printf 'Built VC4 Linux V3D boundary initramfs at %s\n' "$out_dir"
cat "$out_dir/SHA256SUMS"
