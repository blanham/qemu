#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-v3d-modular-initramfs"}
module_bundle=${2:-"$root_dir/build/vc4-linux-v3d-modules"}
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}
libdrm_include=${LIBDRM_INCLUDE:-/usr/include/libdrm}
modeset_probe=${VC4_MODESET_PROBE:-0}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi
if [[ "$module_bundle" != /* ]]; then
    module_bundle="$PWD/$module_bundle"
fi

for tool in "$cc" cpio file gzip python3 sha256sum strings; do
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
if [ "$modeset_probe" != 0 ] && [ "$modeset_probe" != 1 ]; then
    printf 'VC4_MODESET_PROBE must be 0 or 1, got: %s\n' \
        "$modeset_probe" >&2
    exit 1
fi

rm -rf "$out_dir"
mkdir -p \
    "$out_dir/root/dev" \
    "$out_dir/root/etc" \
    "$out_dir/root/lib/vc4-modules" \
    "$out_dir/root/proc" \
    "$out_dir/root/sys"

init_source="$root_dir/tests/vc4/linux-v3d-modular-init.c"
if [ "$modeset_probe" = 1 ]; then
    init_source="$out_dir/linux-v3d-modular-modeset-init.c"
    python3 - \
        "$root_dir/tests/vc4/linux-v3d-modular-init.c" \
        "$init_source" <<'PY'
from pathlib import Path
import sys

source_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
source = source_path.read_text()

include_anchor = '#include <sys/syscall.h>\n'
include_replacement = (
    include_anchor + '\n#include "linux-kms-modeset-probe.inc.c"\n'
)
if source.count(include_anchor) != 1:
    raise SystemExit("modeset include anchor changed")
source = source.replace(include_anchor, include_replacement, 1)

call_anchor = '''    if (card.fd >= 0 && card.vc4) {
        kms_result = probe_kms_topology(&card);
    } else {
        marker("VC4_LINUX_KMS_FAILED stage=no-vc4-card\\n");
    }
'''
call_replacement = call_anchor + '''    if (kms_result == 0) {
        vc4_kms_modeset_supervise();
    }
'''
if source.count(call_anchor) != 1:
    raise SystemExit("modeset call anchor changed")
source = source.replace(call_anchor, call_replacement, 1)
out_path.write_text(source)
PY
fi

"$cc" \
    -static \
    -O2 \
    -g0 \
    -Wall \
    -Wextra \
    -Werror \
    -fno-ident \
    -I"$libdrm_include" \
    -I"$root_dir/tests/vc4" \
    -Wl,--build-id=none \
    -o "$out_dir/root/init" \
    "$init_source"
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

# Avoid strings|grep -q under pipefail: a successful early grep exit can
# deliver SIGPIPE to strings and turn marker validation into status 141.
strings "$out_dir/root/init" > "$out_dir/init.strings"
test -s "$out_dir/init.strings"
markers=(
    VC4_LINUX_V3D_MODULAR_START
    VC4_LINUX_MODULE_LOAD_START
    VC4_LINUX_MODULE_LOAD_OK
    VC4_LINUX_MODULE_LOAD_DONE
    VC4_LINUX_MODULE_CLOSURE_OK
    VC4_LINUX_DRM_UAPI_OK
    VC4_LINUX_DRM_SUBMIT_OK
    VC4_LINUX_V3D_MODULAR_DONE
    VC4_LINUX_V3D_MODULAR_OK
)
if [ "$modeset_probe" = 1 ]; then
    markers+=(
        VC4_LINUX_KMS_MODESET_SUPERVISOR_START
        VC4_LINUX_KMS_MODESET_CONNECTOR_OK
        VC4_LINUX_KMS_MODESET_DUMB_OK
        VC4_LINUX_KMS_MODESET_MAP_OK
        VC4_LINUX_KMS_MODESET_FB_OK
        VC4_LINUX_KMS_MODESET_SETCRTC_OK
        VC4_LINUX_KMS_MODESET_OK
    )
fi
for marker in "${markers[@]}"; do
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

printf 'Built VC4 Linux modular DRM initramfs at %s\n' "$out_dir"
cat "$out_dir/SHA256SUMS"
