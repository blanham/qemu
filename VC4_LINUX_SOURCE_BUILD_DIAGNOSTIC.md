# VC4 Linux initramfs source-build diagnostic

- Source commit: `dd88d335ab6558af34833599de3ccd8cf3a978aa`
- Workflow run: `32321504049`
- Build return code: `0`

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
+ LC_ALL=C
+ sort -z
+ cpio --null --quiet -o --format=newc --owner=0:0
+ gzip -n -9 -c /tmp/vc4-linux-initramfs.cpio/initramfs.cpio
+ test -x /tmp/vc4-linux-initramfs.cpio/root/init
+ test -s /tmp/vc4-linux-initramfs.cpio/initramfs.cpio
+ test -s /tmp/vc4-linux-initramfs.cpio/initramfs.cpio.gz
+ gzip -t /tmp/vc4-linux-initramfs.cpio/initramfs.cpio.gz
+ cpio --quiet -it
+ grep -Eq '^(\./)?init$' /tmp/vc4-linux-initramfs.cpio/CONTENTS
+ sha256sum /tmp/vc4-linux-initramfs.cpio/root/init /tmp/vc4-linux-initramfs.cpio/initramfs.cpio /tmp/vc4-linux-initramfs.cpio/initramfs.cpio.gz
+ printf 'Built VC4 Linux initramfs at %s\n' /tmp/vc4-linux-initramfs.cpio
Built VC4 Linux initramfs at /tmp/vc4-linux-initramfs.cpio
+ cat /tmp/vc4-linux-initramfs.cpio/SHA256SUMS
bf7b59776d9a377abff11e2367d347d014310127145134963cffad809ec71039  /tmp/vc4-linux-initramfs.cpio/root/init
31ee6a16dbd70703fb7f60f7fadaabf32cb9440cbae0d43f7cc0f1e535bd0531  /tmp/vc4-linux-initramfs.cpio/initramfs.cpio
87a3cb234008e655971ddf8e3390783cb8e12d2e24cafdd52a3df5b54b05807b  /tmp/vc4-linux-initramfs.cpio/initramfs.cpio.gz
```

## Archive
```text
<missing>
<missing>
```
