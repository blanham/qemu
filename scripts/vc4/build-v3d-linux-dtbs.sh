#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 4 ]; then
    printf 'usage: %s BASE_DTB KMS_DTBO FKMS_DTBO OUT_DIR\n' "$0" >&2
    exit 2
fi

base_dtb=$1
kms_dtbo=$2
fkms_dtbo=$3
out_dir=$4

for tool in cp dtc fdtoverlay fdtget grep sha256sum; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required DTB tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done
for file in "$base_dtb" "$kms_dtbo" "$fkms_dtbo"; do
    if [ ! -s "$file" ]; then
        printf 'DTB input is missing or empty: %s\n' "$file" >&2
        exit 1
    fi
done

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi
rm -rf "$out_dir"
mkdir -p "$out_dir"

cp "$base_dtb" "$out_dir/base.dtb"
fdtoverlay -i "$base_dtb" -o "$out_dir/kms.dtb" "$kms_dtbo"
fdtoverlay -i "$base_dtb" -o "$out_dir/fkms.dtb" "$fkms_dtbo"

find_v3d_node()
{
    local dtb=$1
    local child

    while IFS= read -r child; do
        case "$child" in
            v3d@*)
                printf '/soc/%s\n' "$child"
                return 0
                ;;
        esac
    done < <(fdtget -l "$dtb" /soc)
    return 1
}

for variant in base kms fkms; do
    dtb="$out_dir/$variant.dtb"
    node=$(find_v3d_node "$dtb") || {
        printf '%s DTB has no V3D node\n' "$variant" >&2
        exit 1
    }
    status=$(fdtget "$dtb" "$node" status 2>/dev/null || true)
    compatible=$(fdtget "$dtb" "$node" compatible 2>/dev/null || true)
    printf '%s node=%s status=%s compatible=%s\n' \
        "$variant" "$node" "${status:-<absent>}" "${compatible:-<absent>}" \
        | tee "$out_dir/$variant.v3d.txt"
    case " $compatible " in
        *" brcm,bcm2835-v3d "*) ;;
        *)
            printf '%s DTB V3D compatibility is unexpected: %s\n' \
                "$variant" "$compatible" >&2
            exit 1
            ;;
    esac
    if [ "$variant" != base ] && [ "$status" != okay ]; then
        printf '%s overlay did not enable V3D (status=%s)\n' \
            "$variant" "${status:-<absent>}" >&2
        exit 1
    fi
    dtc -q -I dtb -O dts -o "$out_dir/$variant.dts" "$dtb"
done

sha256sum "$out_dir"/*.dtb "$out_dir"/*.dts "$out_dir"/*.v3d.txt \
    > "$out_dir/SHA256SUMS"
cat "$out_dir/SHA256SUMS"
