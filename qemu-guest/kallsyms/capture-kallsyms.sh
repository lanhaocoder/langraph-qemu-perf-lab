#!/usr/bin/env bash
# 在 QEMU Guest 里导出运行时的 /proc/kallsyms（含已加载模块符号）
# 用 nokaslr 固定内核基址，使符号地址可复现、可与 System.map 对照。
set -e

OUT=/workspace/kallsyms-capture
G=/tmp/ksguest
rm -rf "$OUT" "$G"
mkdir -p "$OUT" "$G/bin" "$G/modules" "$G/tmp" "$G/proc" "$G/sys" "$G/dev"

cp /bin/busybox "$G/bin/busybox"
cp /tmp/e1000.ko "$G/modules/e1000.ko"

cat > "$G/init" <<'EOF'
#!/bin/busybox sh
/bin/busybox --install -s /bin 2>/dev/null
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev

# 必须先加载网卡模块，模块符号才会出现在 /proc/kallsyms 里
insmod /modules/e1000.ko
sleep 1
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2 2>/dev/null

# root 下放开地址可见性，保证 kallsyms 导出的是真实地址而非全 0
echo 0 > /proc/sys/kernel/kptr_restrict 2>/dev/null

echo "[ks] kernel: $(uname -r)"
echo "[ks] kptr_restrict=$(cat /proc/sys/kernel/kptr_restrict)"
echo "[ks] kallsyms 行数: $(wc -l < /proc/kallsyms)"
echo "[ks] 已加载模块: $(wc -l < /proc/modules)"

cp /proc/kallsyms /tmp/kallsyms.txt
cp /proc/modules  /tmp/modules.txt
cp /proc/version  /tmp/version.txt
tar cf /tmp/ks.tar -C /tmp kallsyms.txt modules.txt version.txt
echo "[ks] tar=$(wc -c < /tmp/ks.tar) bytes"

cat /tmp/ks.tar | nc 10.0.2.2 9999 && echo "[ks] push OK" || echo "[ks] push FAILED"
sleep 2
poweroff -f
EOF
chmod +x "$G/init"

( cd "$G" && find . | cpio -o -H newc -R root:root 2>/dev/null | gzip -9 > /tmp/ks-initramfs.cpio.gz )
echo "[host] initramfs: $(ls -lh /tmp/ks-initramfs.cpio.gz | awk '{print $5}')"

# 宿主机接收端（timeout 兜底，防止 nc 不自动退出）
timeout 240 nc -l 9999 > "$OUT/ks.tar" 2>/dev/null &
NPID=$!
sleep 1

echo "[qemu] 启动 Guest (TCG, nokaslr)"
timeout 300 qemu-system-x86_64 \
    -m 512 -smp 2 \
    -kernel /tmp/vmlinuz \
    -initrd /tmp/ks-initramfs.cpio.gz \
    -append "console=ttyS0 rdinit=/init panic=-1 nokaslr" \
    -netdev user,id=net0 -device e1000,netdev=net0 \
    -nographic -accel tcg -no-reboot -monitor none </dev/null \
    > "$OUT/qemu.log" 2>&1 || true

wait "$NPID" 2>/dev/null || true
kill "$NPID" 2>/dev/null || true

echo "=== Guest 串口输出 ==="
grep -aE '\[ks\]|GUEST|Kernel panic|error' "$OUT/qemu.log" | tail -15

echo "=== 接收结果 ==="
ls -lh "$OUT/ks.tar"
tar xf "$OUT/ks.tar" -C "$OUT"
wc -l "$OUT/kallsyms.txt" "$OUT/modules.txt"
echo "--- modules ---"; cat "$OUT/modules.txt"
echo "--- version ---"; cat "$OUT/version.txt"
echo "--- kallsyms 样例 ---"; head -3 "$OUT/kallsyms.txt"
echo "--- e1000 模块符号数 ---"; grep -c '\[e1000\]' "$OUT/kallsyms.txt"
echo "--- 关键符号 ---"; grep -E ' (e1000_clean|tcp_rcv_established|tcp_write_xmit|__napi_poll)$' "$OUT/kallsyms.txt" | head -8
