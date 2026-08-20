#!/usr/bin/env bash
set -euo pipefail

# VC4_ABSOLUTE_ARGV1
case "${1:-}" in
    /*) ;;
    "") set -- "$PWD/build/vc4-linux-initramfs" ;;
    *) set -- "$PWD/$1" ;;
esac

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-initramfs"}
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}

# The archive is assembled from inside $out_dir/root.  Canonicalize a caller-
# relative output path before changing directories so redirections continue to
# name the intended build directory rather than a path below the staging root.
if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi

rm -rf "$out_dir"
mkdir -p "$out_dir/root/dev" "$out_dir/root/proc" "$out_dir/root/sys"

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
    "$root_dir/tests/vc4/linux-init.c"
chmod 0755 "$out_dir/root/init"

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

printf 'Built VC4 Linux initramfs at %s\n' "$out_dir"
cat "$out_dir/SHA256SUMS"
