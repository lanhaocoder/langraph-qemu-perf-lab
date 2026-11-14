#!/bin/busybox sh
# Guest /init：采集(perf+ftrace) + 20 线程双向 iperf 流量 + 日志回传
/bin/busybox --install -s /bin 2>/dev/null
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null
mount -t debugfs nodev /sys/kernel/debug 2>/dev/null
[ -d /sys/kernel/debug/tracing ] || { mkdir -p /sys/kernel/debug/tracing 2>/dev/null; mount -t tracefs tracefs /sys/kernel/debug/tracing 2>/dev/null; }
export LD_LIBRARY_PATH=/lib:/lib64:/opt/lib
export PATH=/bin:/usr/bin:/sbin:/usr/sbin

echo "########## GUEST BOOTED ##########"
uname -a

echo "[net] insmod e1000"
insmod /modules/e1000.ko
sleep 2
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2 2>/dev/null
ifconfig eth0 | head -3 | tail -2

# 导出运行时内核符号表：必须在 insmod e1000 之后，模块符号才会出现在 kallsyms 里。
# 用于离线重新解析 perf.data / 地址换算；详见 ../qemu-guest/kallsyms/README.md
echo 0 > /proc/sys/kernel/kptr_restrict 2>/dev/null
mkdir -p /langraph_out
cp /proc/kallsyms /langraph_out/kallsyms.txt
cp /proc/modules  /langraph_out/modules.txt
cp /proc/version  /langraph_out/version.txt
echo "[ks] kallsyms=$(wc -l < /langraph_out/kallsyms.txt) 行, modules=$(wc -l < /langraph_out/modules.txt) 个"

# Guest 侧 iperf3 server，供宿主机经 hostfwd 反向发起双向流量
iperf3 -s -p 5201 -D 2>/dev/null || iperf3 -s -p 5201 >/dev/null 2>&1 &
sleep 2

echo "[capture] get_ftrace.sh -t 35 -p 1  (perf + ftrace 同时抓)"
cd /
sh /get_ftrace.sh -t 35 -p 1 -o /langraph_out -z 0 > /capture.log 2>&1 &
CPID=$!
# 与宿主机发起的测试错开，避免 slirp 上两组 20 流并发把控制连接挤掉
sleep 16

echo "[iperf] guest -> host : iperf3 -c 10.0.2.2 -p 5211 -P 20 --bidir -t 12"
iperf3 -c 10.0.2.2 -p 5211 -P 20 --bidir -t 12 -J > /langraph_out/iperf_guest_to_host.json 2>&1 &
IPID=$!

wait $CPID
echo "[capture] 采集脚本结束"
sleep 10         # perf record 是 sleep(t+5)，等 perf.data 写完
wait $IPID 2>/dev/null

# 脚本的 save_perf 可能早于 perf.data 写完，重新导出一次完整 perf.log
cd /langraph_out
/opt/perf.bin script -F comm,pid,tid,cpu,flags,time,event,ip,sym,dso,trace,symoff,dso,ipc > perf.log 2> /perf_script.err
echo "[perf] perf.log=$(wc -c < perf.log) bytes  perf.data=$(wc -c < perf.data 2>/dev/null) bytes"
cd /
echo "[capture.log tail]"
tail -20 /capture.log
echo "[files]"
ls -lh /langraph_out/

cp /capture.log /langraph_out/capture.log
cp /perf_script.err /langraph_out/perf_script.err
cd /langraph_out
tar cf /tmp/result.tar perf.log ftrace.log ftrace_format.log iperf_guest_to_host.json capture.log perf_script.err kallsyms.txt modules.txt version.txt 2>/dev/null
cp /tmp/result.tar /langraph_out/result.tar
echo "[export] tar=$(wc -c < /tmp/result.tar) bytes"

# 主通道：主动推给宿主机 nc :9999
cat /tmp/result.tar | nc 10.0.2.2 9999 && echo "[export] nc push OK" || echo "[export] nc push FAILED"
# 兜底通道：起 httpd，宿主机经 hostfwd 18080 来取
cd /langraph_out
httpd -p 8080 -h /langraph_out 2>/dev/null &
echo "[export] httpd fallback on :8080"
sleep 8
echo "########## GUEST DONE ##########"
poweroff -f
