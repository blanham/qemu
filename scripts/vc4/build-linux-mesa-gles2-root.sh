#!/usr/bin/env bash
set -euo pipefail

root_dir=$(cd "$(dirname "$0")/../.." && pwd)
out_dir=${1:-"$root_dir/build/vc4-linux-mesa-gles2-root"}
mesa_version=24.0.2
mesa_archive="mesa-${mesa_version}.tar.xz"
mesa_sha256=94e28a8edad06d8ed2b83eb53f253b9eb5aa62c3080f939702e1b3039b56c9e8
cc=${AARCH64_CC:-aarch64-linux-gnu-gcc}
cxx=${AARCH64_CXX:-aarch64-linux-gnu-g++}
readelf=${AARCH64_READELF:-aarch64-linux-gnu-readelf}
strip=${AARCH64_STRIP:-aarch64-linux-gnu-strip}

if [[ "$out_dir" != /* ]]; then
    out_dir="$PWD/$out_dir"
fi

for tool in \
    "$cc" "$cxx" "$readelf" "$strip" \
    aarch64-linux-gnu-ar curl file meson ninja pkg-config \
    python3 sha256sum tar xz; do
    if ! command -v "$tool" >/dev/null 2>&1; then
        printf 'required Mesa VC4 root tool not found: %s\n' "$tool" >&2
        exit 1
    fi
done

rm -rf "$out_dir"
mkdir -p "$out_dir/download" "$out_dir/root" "$out_dir/work"
archive_path="$out_dir/download/$mesa_archive"

downloaded=false
for url in \
    "https://archive.mesa3d.org/$mesa_archive" \
    "https://mesa.freedesktop.org/archive/$mesa_archive"; do
    if curl --fail --location --retry 5 \
            --output "$archive_path" "$url"; then
        downloaded=true
        break
    fi
done
if [ "$downloaded" != true ]; then
    printf 'could not download %s from pinned Mesa archives\n' \
        "$mesa_archive" >&2
    exit 1
fi
printf '%s  %s\n' "$mesa_sha256" "$archive_path" | sha256sum -c -

tar -C "$out_dir/work" -xf "$archive_path"
mesa_source="$out_dir/work/mesa-$mesa_version"
mesa_build="$out_dir/work/mesa-build"
cross_file="$out_dir/work/aarch64-linux.cross"
pkg_config_wrapper="$out_dir/work/aarch64-pkg-config"

cat > "$pkg_config_wrapper" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
export PKG_CONFIG_PATH=
export PKG_CONFIG_SYSROOT_DIR=/
export PKG_CONFIG_LIBDIR=/usr/lib/aarch64-linux-gnu/pkgconfig:/usr/share/pkgconfig
exec pkg-config "$@"
EOF
chmod 0755 "$pkg_config_wrapper"

cat > "$cross_file" <<EOF
[binaries]
c = '$cc'
cpp = '$cxx'
ar = 'aarch64-linux-gnu-ar'
strip = '$strip'
pkgconfig = '$pkg_config_wrapper'

[host_machine]
system = 'linux'
cpu_family = 'aarch64'
cpu = 'aarch64'
endian = 'little'

[properties]
needs_exe_wrapper = true

[built-in options]
c_args = ['-I/usr/include/aarch64-linux-gnu']
cpp_args = ['-I/usr/include/aarch64-linux-gnu']
c_link_args = ['-L/usr/lib/aarch64-linux-gnu', '-Wl,-rpath-link,/usr/lib/aarch64-linux-gnu', '-Wl,-rpath-link,/lib/aarch64-linux-gnu']
cpp_link_args = ['-L/usr/lib/aarch64-linux-gnu', '-Wl,-rpath-link,/usr/lib/aarch64-linux-gnu', '-Wl,-rpath-link,/lib/aarch64-linux-gnu']
EOF

meson setup "$mesa_build" "$mesa_source" \
    --cross-file "$cross_file" \
    --prefix=/usr \
    --libdir=lib/aarch64-linux-gnu \
    --buildtype=release \
    -Db_ndebug=true \
    -Dstrip=true \
    -Dplatforms= \
    -Degl-native-platform=surfaceless \
    -Dgallium-drivers=vc4 \
    -Dvulkan-drivers= \
    -Degl=enabled \
    -Dgbm=enabled \
    -Dgles1=disabled \
    -Dgles2=enabled \
    -Dopengl=false \
    -Dglx=disabled \
    -Dglvnd=false \
    -Dshared-glapi=enabled \
    -Dllvm=disabled \
    -Dshared-llvm=disabled \
    -Ddraw-use-llvm=false \
    -Dexpat=disabled \
    -Dxmlconfig=disabled \
    -Dzlib=disabled \
    -Dzstd=disabled \
    -Dshader-cache=disabled \
    -Dvalgrind=disabled \
    -Dlibunwind=disabled \
    -Dlmsensors=disabled \
    -Dgallium-vdpau=disabled \
    -Dgallium-va=disabled \
    -Dgallium-xa=disabled \
    -Dgallium-nine=false \
    -Dgallium-omx=disabled \
    -Dgallium-opencl=disabled \
    -Dgallium-rusticl=false \
    -Dopencl-spirv=false \
    -Dbuild-tests=false \
    -Denable-glcpp-tests=false \
    -Dtools= \
    -Dosmesa=false \
    -Dselinux=false \
    -Dperfetto=false \
    -Dvideo-codecs= \
    2>&1 | tee "$out_dir/mesa-configure.log"

meson compile -C "$mesa_build" \
    2>&1 | tee "$out_dir/mesa-build.log"
DESTDIR="$out_dir/root" meson install -C "$mesa_build" \
    2>&1 | tee "$out_dir/mesa-install.log"

mkdir -p \
    "$out_dir/root/usr/bin" \
    "$out_dir/root/etc" \
    "$out_dir/root/tmp" \
    "$out_dir/root/var/tmp"
chmod 01777 "$out_dir/root/tmp" "$out_dir/root/var/tmp"

mesa_lib="$out_dir/root/usr/lib/aarch64-linux-gnu"
test -e "$mesa_lib/libEGL.so"
test -e "$mesa_lib/libGLESv2.so"
test -e "$mesa_lib/dri/vc4_dri.so"

"$cc" \
    -O2 \
    -g0 \
    -Wall \
    -Wextra \
    -Werror \
    -fno-ident \
    -I"$out_dir/root/usr/include" \
    -L"$mesa_lib" \
    -Wl,-rpath,/usr/lib/aarch64-linux-gnu \
    -Wl,-rpath-link,"$mesa_lib" \
    -Wl,-rpath-link,/usr/lib/aarch64-linux-gnu \
    -Wl,--build-id=none \
    -o "$out_dir/root/usr/bin/vc4-mesa-gles2-probe" \
    "$root_dir/tests/vc4/linux-mesa-gles2-probe.c" \
    -lEGL -lGLESv2 -ldl -lm -pthread
"$strip" "$out_dir/root/usr/bin/vc4-mesa-gles2-probe"

closure_args=(
    --root "$out_dir/root"
    --readelf "$readelf"
    --manifest "$out_dir/ELF_CLOSURE"
    --search /usr/aarch64-linux-gnu/lib
    --search /usr/lib/aarch64-linux-gnu
    --search /lib/aarch64-linux-gnu
    --seed usr/bin/vc4-mesa-gles2-probe
    --seed usr/lib/aarch64-linux-gnu/libEGL.so.1
    --seed usr/lib/aarch64-linux-gnu/libEGL_mesa.so.0
    --seed usr/lib/aarch64-linux-gnu/libGLESv2.so.2
    --seed usr/lib/aarch64-linux-gnu/libglapi.so.0
    --seed usr/lib/aarch64-linux-gnu/dri/vc4_dri.so
)
python3 "$root_dir/scripts/vc4/copy-aarch64-elf-closure.py" \
    "${closure_args[@]}"

file "$out_dir/root/usr/bin/vc4-mesa-gles2-probe" \
    > "$out_dir/probe.file"
grep -Fq 'ELF 64-bit LSB pie executable, ARM aarch64' \
    "$out_dir/probe.file"
"$readelf" -l "$out_dir/root/usr/bin/vc4-mesa-gles2-probe" \
    > "$out_dir/probe.program-headers"
grep -Fq '/lib/ld-linux-aarch64.so.1' \
    "$out_dir/probe.program-headers"

if [ -n "$(find "$mesa_lib/dri" -maxdepth 1 \
        -name 'swrast_dri.so' -print -quit)" ]; then
    printf 'software rasterizer unexpectedly present in VC4-only root\n' >&2
    exit 1
fi
if [ -n "$(find "$out_dir/root" -type f \
        -name '*LLVM*' -print -quit)" ]; then
    printf 'LLVM unexpectedly present in minimal VC4 root\n' >&2
    exit 1
fi

cat > "$out_dir/root/etc/vc4-mesa-gles2-provenance" <<EOF
mesa_version=$mesa_version
mesa_archive=$mesa_archive
mesa_sha256=$mesa_sha256
gallium_driver=vc4
egl_platform=surfaceless
gles_version=2
EOF
cp "$out_dir/root/etc/vc4-mesa-gles2-provenance" \
    "$out_dir/PROVENANCE"

(
    cd "$out_dir/root"
    find . -type f -print0 | LC_ALL=C sort -z | \
        xargs -0 sha256sum
) > "$out_dir/ROOT_SHA256SUMS"

printf 'Built pinned Mesa VC4 GLES2 root at %s\n' "$out_dir"
cat "$out_dir/PROVENANCE"
du -sh "$out_dir/root"
