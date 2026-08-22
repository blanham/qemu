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

required_nodes=(
    /soc/txp@7e004000
    /soc/pixelvalve@7e206000
    /soc/pixelvalve@7e207000
    /soc/hvs@7e400000
    /soc/pixelvalve@7e807000
    /soc/hdmi@7e902000
    /soc/v3d@7ec00000
    /soc/gpu
)

for node in "${required_nodes[@]}"; do
    status=$(fdtget -t s "$output_dtb" "$node" status)
    if [ "$status" != okay ]; then
        printf 'KMS overlay left %s status=%s\n' "$node" "$status" >&2
        exit 1
    fi
    printf '  enabled: %s\n' "$node"
done

v3d_compatible=$(fdtget -t s "$output_dtb" /soc/v3d@7ec00000 compatible)
case " $v3d_compatible " in
    *" brcm,bcm2835-v3d "*|*" brcm,vc4-v3d "*) ;;
    *)
        printf 'unexpected V3D compatible string: %s\n' \
            "$v3d_compatible" >&2
        exit 1
        ;;
esac

hdmi_compatible=$(fdtget -t s "$output_dtb" /soc/hdmi@7e902000 compatible)
case " $hdmi_compatible " in
    *" brcm,bcm2835-hdmi "*) ;;
    *)
        printf 'unexpected HDMI compatible string: %s\n' \
            "$hdmi_compatible" >&2
        exit 1
        ;;
esac

printf 'Built native VC4 KMS Linux DTB: %s\n' "$output_dtb"
printf '  V3D compatible: %s\n' "$v3d_compatible"
printf '  HDMI compatible: %s\n' "$hdmi_compatible"
