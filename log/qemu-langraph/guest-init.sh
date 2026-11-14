#!/bin/busybox sh
/bin/busybox --install -s /bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null
mount -t debugfs nodev /sys/kernel/debug 2>/dev/null && echo "  [init] debugfs mounted"
ls -ld /sys/kernel/debug/tracing 2>&1 | sed 's/^/  [init] /'
[ -d /sys/kernel/debug/tracing ] || { mkdir -p /sys/kernel/debug/tracing 2>/dev/null; mount -t tracefs tracefs /sys/kernel/debug/tracing 2>/dev/null; }
export LD_LIBRARY_PATH=/lib:/lib64
echo ""
echo "########## GUEST BOOTED: $(uname -r) ##########"
echo ""
echo "[1] 在 Guest 里执行 LanGraph (langraph trace.dat)"
timeout 30 /langraph /trace.dat 2>&1 | head -8
echo "    -> langraph 执行完毕"
echo ""
echo "[2] 在 Guest 里跑采集脚本 get_ftrace.sh -t 2"
cd /
timeout 40 sh /get_ftrace.sh -t 2 -o langraph_out -z 0 2>&1 | tail -12
echo "    -> 输出文件:"
ls -lh /langraph_out/ 2>/dev/null | head -10 | sed 's/^/    /'
echo "    -> ftrace.log 前 6 行:"
head -6 /langraph_out/ftrace.log 2>/dev/null | sed 's/^/    /'
echo ""
echo "[3] Guest 内 perf 软件事件"
/perftest
echo "########## GUEST DONE ##########"
poweroff -f
