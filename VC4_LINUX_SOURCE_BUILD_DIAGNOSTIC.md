# VC4 Linux initramfs source-build diagnostic

- Source commit: `1cfb585dd60dc164392f889740f725f12fbe199b`
- Workflow run: `32321352775`
- Build return code: `1`

## Toolchain
```text
aarch64-linux-gnu-gcc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0
Copyright (C) 2023 Free Software Foundation, Inc.
This is free software; see the source for copying conditions.  There is NO
warranty; not even for MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.

/
/usr/lib/gcc-cross/aarch64-linux-gnu/13/../../../../aarch64-linux-gnu/lib/../lib/libc.a
```

## Build log
```text
+ set -euo pipefail
+++ dirname scripts/vc4/build-linux-initramfs.sh
++ cd scripts/vc4/../..
++ pwd
+ root_dir=/home/runner/work/qemu/qemu
+ out_dir=/tmp/vc4-linux-initramfs.cpio
+ cc=aarch64-linux-gnu-gcc
+ rm -rf /tmp/vc4-linux-initramfs.cpio
+ mkdir -p /tmp/vc4-linux-initramfs.cpio/root/dev /tmp/vc4-linux-initramfs.cpio/root/proc /tmp/vc4-linux-initramfs.cpio/root/sys
+ aarch64-linux-gnu-gcc -static -O2 -g0 -Wall -Wextra -Werror -fno-ident -Wl,--build-id=none -o /tmp/vc4-linux-initramfs.cpio/root/init /home/runner/work/qemu/qemu/tests/vc4/linux-init.c
+ chmod 0755 /tmp/vc4-linux-initramfs.cpio/root/init
+ cd /tmp/vc4-linux-initramfs.cpio/root
+ find . -print0
+ cpio --null --quiet -o --format=newc --owner=0:0
+ LC_ALL=C
+ sort -z
+ gzip -n -9 -c /tmp/vc4-linux-initramfs.cpio/initramfs.cpio
+ test -x /tmp/vc4-linux-initramfs.cpio/root/init
+ test -s /tmp/vc4-linux-initramfs.cpio/initramfs.cpio
+ test -s /tmp/vc4-linux-initramfs.cpio/initramfs.cpio.gz
+ gzip -t /tmp/vc4-linux-initramfs.cpio/initramfs.cpio.gz
+ cpio --quiet -it
+ grep -qx ./init
```

## Archive
```text
<missing>
<missing>
```
