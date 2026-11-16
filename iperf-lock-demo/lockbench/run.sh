#!/bin/bash
# 宿主机编排：重抓「带完整硬中断因果链」的 iperf 生命周期
#
# 背景：上一轮 -P 20 持续大流量下，e1000 的 NAPI 长期停在 polling 状态并
#       写 IMC 屏蔽设备中断，稳定打流 10s 内 eth0 硬中断 = 0，因果链只能
#       从 NET_RX 软中断起点，看不到「硬中断 → 软中断 → 用户态」完整链。
#
# 本轮手段：把流量改成 -P 2 / -b 1M / -l 256 的低速小包，包间隔 ~10ms，
#       远大于 NAPI poll 的百 µs 级耗时，使每轮 poll 后 RX 队列都能排空、
#       napi_complete() 写 IMS 重开中断，下一个包即可触发新的 IRQ。
#
# 其余时序与 iperf-lifecycle/run.sh 完全一致（READY :9998 / 回传 :9999）。
set -u
# 产物目录 = 本脚本所在目录（在仓库里 clone 下来即可直接复现）
OUT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HTL="$OUT/host_timeline.txt"
mkdir -p "$OUT"
: > "$HTL"
hstamp(){ printf '%s  %s\n' "$(date +%s.%N)" "$*" >> "$HTL"; }

rm -f "$OUT/result.tar" "$OUT/result.tar.wget" "$OUT/ready.flag" \
      "$OUT/langraph.tar.gz" "$OUT/qemu.log" "$OUT/host_client.json"

# 清理上一轮遗留（[x] 技巧避免 pkill 匹配到自身命令行）
pkill -f "[i]perf3 -s -p 5211" 2>/dev/null || true
pkill -f "[n]c -l 9998" 2>/dev/null || true
pkill -f "[n]c -l 9999" 2>/dev/null || true
pkill -f "[i]nitramfs_hardirq" 2>/dev/null || true
sleep 1

echo "[host] READY 信号接收端 :9998"
timeout 600 nc -l 9998 > "$OUT/ready.flag" 2>/dev/null &
RPID=$!
echo "[host] 采集产物接收端 :9999"
timeout 600 nc -l 9999 > "$OUT/result.tar" 2>/dev/null &
NPID=$!
sleep 0.5
hstamp "LISTENERS_UP"

echo "[host] 构建 initramfs"
cp "$OUT/guest-init.sh" /tmp/guest/init
chmod +x /tmp/guest/init
( cd /tmp/guest && find . | cpio -o -H newc -R root:root 2>/dev/null | gzip -9 > /tmp/initramfs_lockdemo.cpio.gz )
echo "       initramfs: $(ls -lh /tmp/initramfs_lockdemo.cpio.gz | awk '{print $5}')"

echo "[qemu] 启动 Guest (TCG, 4 vCPU / 3G, nokaslr, e1000 + SLIRP)"
hstamp "QEMU_LAUNCH"
timeout 600 qemu-system-x86_64 \
    -m 3072 -smp 4 \
    -kernel /tmp/vmlinuz \
    -initrd /tmp/initramfs_lockdemo.cpio.gz \
    -append "console=ttyS0 rdinit=/init panic=-1 nokaslr" \
    -netdev user,id=net0,hostfwd=tcp:127.0.0.1:15201-:5201,hostfwd=tcp:127.0.0.1:18080-:8080 \
    -device e1000,netdev=net0 \
    -nographic -accel tcg -no-reboot -monitor none \
    > "$OUT/qemu.log" 2>&1 &
QPID=$!

echo "[host] 等待 Guest READY（ftrace 已开 + guest server 已起）"
OK=0
for i in $(seq 1 300); do
    [ -s "$OUT/ready.flag" ] && { OK=1; break; }
    kill -0 "$QPID" 2>/dev/null || break
    sleep 0.5
done
echo "[host] READY=$OK  msg='$(head -c 120 "$OUT/ready.flag" 2>/dev/null)'"
hstamp "GUEST_READY=$OK"

if [ "$OK" = 1 ]; then
    # ② 沙盒侧收包进程：one-off，服务完 guest client 会话后自然退出
    echo "[host] 起 host iperf3 server :5211 -1 (one-off)"
    iperf3 -s -p 5211 -1 </dev/null > "$OUT/host_server.log" 2>&1 &
    HSID=$!
    hstamp "HOST_SERVER_START pid=$HSID"
    sleep 0.8

    # ③ 本轮关键：低速小包打向 Guest server (:5201 via hostfwd 15201)
    #    Guest 收包侧是「硬中断 → NET_RX → 唤醒 iperf3」链的来源。
    echo "[host] 起 host iperf3 client -P 2 -t 12 -b 1M -l 256 -> 127.0.0.1:15201"
    hstamp "HOST_CLIENT_START lowrate P2 b1M l256"
    iperf3 -c 127.0.0.1 -p 15201 -P 2 -t 12 -b 1M -l 256 -J </dev/null \
        > "$OUT/host_client.json" 2> "$OUT/host_client.err"
    HCRC=$?
    hstamp "HOST_CLIENT_EXIT rc=$HCRC"
    echo "[host] host client rc=$HCRC"
fi

echo "[host] 等待 langraph/ 回传"
for i in $(seq 1 300); do
    [ -s "$OUT/result.tar" ] && break
    wget -q -O "$OUT/result.tar.wget" "http://127.0.0.1:18080/result.tar" 2>/dev/null
    if [ -s "$OUT/result.tar.wget" ]; then
        mv "$OUT/result.tar.wget" "$OUT/result.tar"
        echo "[host] 经 httpd 取回"; break
    fi
    kill -0 "$QPID" 2>/dev/null || { sleep 3; break; }
    sleep 2
done

wait "$QPID" 2>/dev/null
echo "[qemu] 已退出"
kill "$NPID" "$RPID" 2>/dev/null || true
if [ -n "${HSID:-}" ]; then wait "$HSID" 2>/dev/null; echo "[host] host server wait rc=$?"; fi
echo "[host] host server 残留: $(pgrep -f '[i]perf3 -s -p 5211' | wc -l)"
hstamp "DONE"

if [ ! -s "$OUT/result.tar" ]; then
    echo "[ERROR] 未收到回传文件，qemu.log 尾部："
    tail -40 "$OUT/qemu.log"
    exit 1
fi

echo "[host] 解包 langraph/"
rm -rf "$OUT/langraph"
tar xf "$OUT/result.tar" -C "$OUT"
ls -lh "$OUT/langraph" | sed 's/^/    /'

echo "[host] 压缩归档 (pigz -9)"
cd "$OUT"
tar cf - langraph | pigz -9 > iperf-lockdemo.tar.gz
echo "[host] iperf-lockdemo.tar.gz: $(ls -lh iperf-lockdemo.tar.gz | awk '{print $5}')"
echo "[host] 归档内容:"
tar tzf iperf-lockdemo.tar.gz | sed 's/^/    /'
