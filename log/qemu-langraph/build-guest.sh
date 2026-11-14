#!/bin/bash
# 重新组装 initramfs（改完 guest-init.sh 或换了 LanGraph 代码后跑这个）
# 用法: ./build-guest.sh [LanGraph源码目录，默认 /workspace/LanGraph]
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
LANGRAPH=${1:-/workspace/LanGraph}
ROOT=$(mktemp -d)
trap 'rm -rf "$ROOT"' EXIT

echo "[1/5] 检查依赖"
for c in gcc cpio busybox qemu-system-x86_64 pkg-config; do
    command -v "$c" >/dev/null || { echo "缺少 $c，请先安装"; exit 1; }
done
[ -f /bin/busybox ] || { echo "需要 busybox-static"; exit 1; }

echo "[2/5] 编译 LanGraph"
make -C "$LANGRAPH" clean >/dev/null
make -C "$LANGRAPH" >/dev/null 2>&1
[ -x "$LANGRAPH/langraph" ] || { echo "langraph 编译失败"; exit 1; }

echo "[3/5] 搭 rootfs"
mkdir -p "$ROOT"/{bin,proc,sys,dev,tmp,mnt,lib,lib64}
cp /bin/busybox "$ROOT/bin/busybox"
gcc -static -O2 -o "$ROOT/perftest" "$HERE/perftest.c"
cp "$LANGRAPH/langraph" "$LANGRAPH/trace.dat" "$LANGRAPH/script/get_ftrace.sh" "$ROOT/"
for lib in $(ldd "$LANGRAPH/langraph" | awk '{print $3}' | grep '^/'); do cp "$lib" "$ROOT/lib/"; done
cp -L /lib64/ld-linux-x86-64.so.2 "$ROOT/lib64/" 2>/dev/null \
    || cp -L /lib/x86_64-linux-gnu/ld-linux-x86-64.so.2 "$ROOT/lib64/"
cp "$HERE/guest-init.sh" "$ROOT/init"
chmod +x "$ROOT/init"

echo "[4/5] 打包 initramfs"
(cd "$ROOT" && find . | cpio -o -H newc -R root:root 2>/dev/null | gzip -9) > "$HERE/initramfs.cpio.gz"

echo "[5/5] 完成: $HERE/initramfs.cpio.gz ($(du -h "$HERE/initramfs.cpio.gz" | cut -f1))"
echo "下一步: ./boot.sh"
