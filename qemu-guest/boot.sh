#!/bin/bash
# 启动最小 Guest：内部自动执行 lanGraph + get_ftrace.sh + perf 探测，完成后自动关机
# 有 KVM 的机器（例如你的 WSL2）把 -accel tcg 换成 -enable-kvm 可大幅提速
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
exec qemu-system-x86_64 \
    -m 1024 -smp 2 \
    -kernel "$HERE/vmlinuz" \
    -initrd "$HERE/initramfs.cpio.gz" \
    -append "console=ttyS0 rdinit=/init panic=-1" \
    -nographic -accel tcg -no-reboot -monitor none
