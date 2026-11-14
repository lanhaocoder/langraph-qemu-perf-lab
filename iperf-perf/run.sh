#!/bin/bash
# 宿主机编排：iperf3 server + 反向 20 线程双向流量 + 接收 Guest 回传的采集日志
set -u
OUT=/workspace/iperf-perf-out
mkdir -p "$OUT/guest"
rm -f "$OUT/result.tar" "$OUT/result.tar.wget"

pkill -f 'iperf3 -s -p 5211' 2>/dev/null || true
pkill -f 'nc -l 9999' 2>/dev/null || true
sleep 1

echo "[host] 启动 iperf3 server :5211 (daemon 常驻)"
iperf3 -s -p 5211 -D > "$OUT/host_server.log" 2>&1
sleep 1
echo "[host] 启动 nc 接收端 :9999"
nc -l 9999 > "$OUT/result.tar" 2>/dev/null &
NPID=$!

echo "[qemu] 启动 Guest (TCG, 4 vCPU / 2G, e1000 + hostfwd)"
qemu-system-x86_64 \
    -m 2048 -smp 4 \
    -kernel /tmp/vmlinuz \
    -initrd /tmp/initramfs_iperf.cpio.gz \
    -append "console=ttyS0 rdinit=/init panic=-1" \
    -netdev user,id=net0,hostfwd=tcp:127.0.0.1:15201-:5201,hostfwd=tcp:127.0.0.1:18080-:8080 \
    -device e1000,netdev=net0 \
    -nographic -accel tcg -no-reboot -monitor none \
    > "$OUT/qemu.log" 2>&1 &
QPID=$!

echo "[host] 等 Guest 侧 iperf3 server (127.0.0.1:15201)"
OK=0
for i in $(seq 1 180); do
    if nc -z 127.0.0.1 15201 2>/dev/null; then OK=1; break; fi
    kill -0 "$QPID" 2>/dev/null || break
    sleep 1
done
echo "[host] Guest server 可达: $OK"

if [ "$OK" = 1 ]; then
    echo "[host] 宿主机 -> Guest: iperf3 -P 20 --bidir -t 12"
    iperf3 -c 127.0.0.1 -p 15201 -P 20 --bidir -t 12 -J > "$OUT/host_to_guest.json" 2>&1
    echo "[host] 反向测试结束 (rc=$?)"
fi

echo "[host] 等待日志回传"
for i in $(seq 1 200); do
    [ -s "$OUT/result.tar" ] && break
    wget -q -O "$OUT/result.tar.wget" "http://127.0.0.1:18080/result.tar" 2>/dev/null
    if [ -s "$OUT/result.tar.wget" ]; then mv "$OUT/result.tar.wget" "$OUT/result.tar"; echo "[host] 经 httpd 取回"; break; fi
    kill -0 "$QPID" 2>/dev/null || { sleep 3; break; }
    sleep 2
done

wait "$QPID" 2>/dev/null
echo "[qemu] 已退出"
kill "$NPID" 2>/dev/null || true
pkill -f 'iperf3 -s -p 5211' 2>/dev/null || true

if [ -s "$OUT/result.tar" ]; then
    tar xf "$OUT/result.tar" -C "$OUT/guest" 2>/dev/null
    echo "[result] 解包完成:"
    ls -lh "$OUT/guest"
else
    echo "[result] 未收到回传文件"
fi
