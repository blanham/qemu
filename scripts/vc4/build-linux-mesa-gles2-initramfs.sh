#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-mesa-gles2-initramfs"}
module_bundle=${2:-"$root_dir/build/vc4-linux-v3d-modules"}
mesa_root=${3:-"$root_dir/build/vc4-linux-mesa-gles2-root"}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi
if [[ "$module_bundle" != /* ]]; then
    module_bundle="$PWD/$module_bundle"
fi
if [[ "$mesa_root" != /* ]]; then
    mesa_root="$PWD/$mesa_root"
fi

test -s "$mesa_root/root/usr/bin/vc4-mesa-gles2-probe"
test -e "$mesa_root/root/usr/lib/aarch64-linux-gnu/dri/vc4_dri.so"
test -e "$mesa_root/root/lib/ld-linux-aarch64.so.1"

temp_dir=$(mktemp -d -t vc4-mesa-gles2-init.XXXXXX)
trap 'rm -rf "$temp_dir"' EXIT
init_source="$temp_dir/linux-v3d-modular-mesa-init.c"
cp "$root_dir/tests/vc4/linux-v3d-modular-init.c" "$init_source"
python3 "$root_dir/scripts/vc4/enable-linux-mesa-gles2-probe.py" \
    "$init_source"

required_markers=(
    VC4_LINUX_MESA_GLES2_SUPERVISOR_START
    VC4_LINUX_MESA_GLES2_SUPERVISOR_OK
    VC4_LINUX_MESA_GLES2_SUPERVISOR_FAILED
    VC4_LINUX_MESA_GLES2_SUPERVISOR_TIMEOUT
)
VC4_INIT_SOURCE="$init_source" \
VC4_INITRAMFS_OVERLAY="$mesa_root/root" \
VC4_INITRAMFS_REQUIRED_MARKERS="${required_markers[*]}" \
    bash "$root_dir/scripts/vc4/build-linux-v3d-modular-initramfs.sh" \
        "$out_dir" "$module_bundle"

for path in \
    usr/bin/vc4-mesa-gles2-probe \
    usr/lib/aarch64-linux-gnu/libEGL.so.1 \
    usr/lib/aarch64-linux-gnu/libGLESv2.so.2 \
    usr/lib/aarch64-linux-gnu/libglapi.so.0 \
    usr/lib/aarch64-linux-gnu/dri/vc4_dri.so \
    lib/ld-linux-aarch64.so.1; do
    grep -Eq "^(\./)?${path}$" "$out_dir/CONTENTS"
done

cp "$mesa_root/PROVENANCE" "$out_dir/MESA_PROVENANCE"
cp "$mesa_root/ELF_CLOSURE" "$out_dir/MESA_ELF_CLOSURE"
cp "$init_source" "$out_dir/INIT_SOURCE_POSTIMAGE"

printf 'Built Linux Mesa VC4 GLES2 initramfs at %s\n' "$out_dir"
