#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 3 ]; then
    printf 'usage: %s BASE_DTB OVERLAY_DTBO OUTPUT_DTB\n' "$0" >&2
    exit 2
fi

base_dtb=$1
overlay_dtbo=$2
output_dtb=$3

for tool in fdtoverlay fdtget dtc; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required device-tree tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done

test -s "$base_dtb"
test -s "$overlay_dtbo"
mkdir -p "$(dirname "$output_dtb")"
fdtoverlay -i "$base_dtb" -o "$output_dtb" "$overlay_dtbo"
test -s "$output_dtb"

dts_dump=${output_dtb}.dts
dtc -q -I dtb -O dts -o "$dts_dump" "$output_dtb"

v3d_node=$(fdtget -l "$output_dtb" /soc | grep -E '^v3d@' | head -n 1)
if [ -z "$v3d_node" ]; then
    printf 'overlay result has no /soc/v3d@* node\n' >&2
    exit 1
fi
v3d_path=/soc/$v3d_node
status=$(fdtget -t s "$output_dtb" "$v3d_path" status)
compatible=$(fdtget -t s "$output_dtb" "$v3d_path" compatible)

if [ "$status" != okay ]; then
    printf 'overlay left %s status=%s\n' "$v3d_path" "$status" >&2
    exit 1
fi
case " $compatible " in
    *" brcm,bcm2835-v3d "*|*" brcm,vc4-v3d "*) ;;
    *)
        printf 'unexpected V3D compatible string: %s\n' "$compatible" >&2
        exit 1
        ;;
esac

printf 'Built V3D Linux DTB: %s\n' "$output_dtb"
printf '  node: %s\n' "$v3d_path"
printf '  status: %s\n' "$status"
printf '  compatible: %s\n' "$compatible"
