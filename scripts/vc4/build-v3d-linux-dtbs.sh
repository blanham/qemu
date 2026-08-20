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

for tool in cp dtc fdtoverlay fdtget fdtput grep sha256sum; do
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

symbol_path()
{
    local label=$1
    local path

    path=$(fdtget "$base_dtb" /__symbols__ "$label" 2>/dev/null) || {
        printf 'pinned base DTB has no required symbol: %s\n' "$label" >&2
        return 1
    }
    case "$path" in
        /*) printf '%s\n' "$path" ;;
        *)
            printf 'pinned base DTB symbol %s has invalid path: %s\n' \
                "$label" "$path" >&2
            return 1
            ;;
    esac
}

set_status()
{
    local dtb=$1
    local label=$2
    local status=$3
    local path

    path=$(symbol_path "$label")
    fdtput -t s "$dtb" "$path" status "$status"
}

apply_kms_fallback()
{
    local dtb=$1

    set_status "$dtb" fb disabled
    for label in \
        i2c2 pixelvalve0 pixelvalve1 pixelvalve2 hvs hdmi v3d vc4 txp; do
        set_status "$dtb" "$label" okay
    done
}

apply_fkms_fallback()
{
    local dtb=$1

    set_status "$dtb" fb disabled
    for label in firmwarekms v3d vc4; do
        set_status "$dtb" "$label" okay
    done
}

materialize_variant()
{
    local name=$1
    local overlay=$2
    local fallback=$3
    local output="$out_dir/$name.dtb"
    local log="$out_dir/$name.overlay.log"
    local method

    if fdtoverlay -i "$base_dtb" -o "$output" "$overlay" \
        >"$log" 2>&1; then
        method=official-overlay
    else
        printf '%s overlay could not be applied by generic fdtoverlay; ' \
            "$name" >&2
        printf 'using the exact pinned-DTB status-property equivalent\n' >&2
        cp "$base_dtb" "$output"
        "$fallback" "$output"
        method=pinned-dtb-fallback
    fi
    printf '%s\n' "$method" > "$out_dir/$name.method"
}

cp "$base_dtb" "$out_dir/base.dtb"
printf '%s\n' unmodified > "$out_dir/base.method"
: > "$out_dir/base.overlay.log"
materialize_variant kms "$kms_dtbo" apply_kms_fallback
materialize_variant fkms "$fkms_dtbo" apply_fkms_fallback

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
    method=$(cat "$out_dir/$variant.method")
    printf '%s method=%s node=%s status=%s compatible=%s\n' \
        "$variant" "$method" "$node" "${status:-<absent>}" \
        "${compatible:-<absent>}" \
        | tee "$out_dir/$variant.v3d.txt"
    case " $compatible " in
        *" brcm,vc4-v3d "*|*" brcm,bcm2835-v3d "*) ;;
        *)
            printf '%s DTB V3D compatibility is unexpected: %s\n' \
                "$variant" "$compatible" >&2
            exit 1
            ;;
    esac
    if [ "$variant" != base ] && [ "$status" != okay ]; then
        printf '%s configuration did not enable V3D (status=%s)\n' \
            "$variant" "${status:-<absent>}" >&2
        exit 1
    fi
    dtc -q -I dtb -O dts -o "$out_dir/$variant.dts" "$dtb"
done

sha256sum \
    "$out_dir"/*.dtb \
    "$out_dir"/*.dts \
    "$out_dir"/*.method \
    "$out_dir"/*.overlay.log \
    "$out_dir"/*.v3d.txt \
    > "$out_dir/SHA256SUMS"
cat "$out_dir/SHA256SUMS"
