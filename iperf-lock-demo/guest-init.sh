#!/bin/busybox sh
# Guest /init —— 同步\锁事件关联 demo 重抓（sched+irq+lock+rcu+workqueue+timer+signal，去 napi）
#
# 与 iperf-lifecycle/guest-init.sh 的差别（时序完全一致）：
#   ① get_ftrace.sh 增加 -e "sched|irq|lock|rcu|workqueue|timer|signal"，额外抓 lock:contention_begin/end、rcu、workqueue、timer、signal 事件，
#      用于同步\锁事件关联 demo（去 napi）。
#   ② 双向 iperf 从 -P 20 -t 10（无限速）改为 -P 2 -t 12 -b 1M -l 256，
#      低速小包保证调度/中断/锁竞争事件都有样本，便于 demo 展示。
#   ③ 额外回传 /proc/interrupts、/proc/net/dev 作为硬中断/包量的独立佐证。
#
# 严格时序（全部以 Guest 内 ftrace 开启为 t=0）：
#   ① 先起 get_ftrace.sh -p 1        perf + ftrace 同时抓，ftrace tracing_on=1
#   ② 起 Guest iperf3 server :5201 -1   (one-off，fork/exec 在窗口内)
#      -> 向 Host :9998 推 READY 信号（不用端口探测，避免消耗 one-off 会话）
#   ③ Host 收到 READY 后起 host server :5211 与低速 host client
#      Guest 错开 3s 起低速 guest client -> 10.0.2.2:5211，构成双向
#   ④ 所有 iperf 进程「自然结束」(client -t12 跑完、server one-off 会话后退出)，不 kill
#   ⑤ get_ftrace.sh 仍在进行中；确认无残留后等采集自然结束
#   ⑥ 等 perf record(sleep T+5) 写完，重导 perf.log，并入 kallsyms/时间轴，回传
/bin/busybox --install -s /bin 2>/dev/null
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mount -t tracefs tracefs /sys/kernel/tracing 2>/dev/null
mkdir -p /sys/kernel/debug
mount -t debugfs nodev /sys/kernel/debug 2>/dev/null
[ -d /sys/kernel/debug/tracing ] || { mkdir -p /sys/kernel/debug/tracing; mount -t tracefs tracefs /sys/kernel/debug/tracing 2>/dev/null; }
export LD_LIBRARY_PATH=/lib:/lib64:/opt/lib
export PATH=/bin:/usr/bin:/sbin:/usr/sbin

# /ks 独立于 output：get_ftrace.sh 会把 output 挂成 tmpfs，提前放入会被隐藏
mkdir -p /ks
TS=/ks/timeline.txt
: > "$TS"
stamp(){ printf '%s  %s\n' "$(awk '{print $1}' /proc/uptime)" "$*" >> "$TS"; }

echo "########## GUEST BOOTED: $(uname -r) ##########"
stamp "BOOT"

echo "[net] insmod e1000"
stamp "NET_START"
insmod /modules/e1000.ko
sleep 2
ifconfig eth0 10.0.2.15 netmask 255.255.255.0 up
route add default gw 10.0.2.2 2>/dev/null
stamp "NET_UP"
# 起始中断计数（供回传后对比）
cat /proc/interrupts > /ks/interrupts.before.txt 2>/dev/null
cat /proc/net/dev    > /ks/netdev.before.txt 2>/dev/null

echo "[ks] 导出运行时符号表 (nokaslr)"
echo 0 > /proc/sys/kernel/kptr_restrict 2>/dev/null
cp /proc/kallsyms /ks/kallsyms.txt
cp /proc/modules  /ks/modules.txt
cp /proc/version  /ks/version.txt
stamp "KALLSYMS lines=$(wc -l < /ks/kallsyms.txt)"

mkdir -p /work
cd /work

########## ① 先起采集：perf + ftrace 同时 ##########
# -t 24 : ftrace 采集窗口，必须晚于所有 iperf 自然结束（实测 ~19s）
# -e "sched|irq|lock|rcu|workqueue|timer|signal" : 额外开 napi/napi_poll，佐证 NAPI 收包与队列排空
# -p 1  : perf 与 ftrace 同时抓
# -m 131072 : 128MB/CPU ring buffer
# -z 0  : 不压缩（宿主机侧统一 pigz）
# -o langraph : 按脚本原生语义生成 langraph/ 目录
echo "[1] get_ftrace.sh -t 24 -p 1 -m 131072 -e 'sched|irq|lock|rcu|workqueue|timer|signal' -o langraph"
stamp "CAPTURE_LAUNCH t=24 p=1 m=131072 events=sched,irq,lock,rcu,workqueue,timer,signal"
sh /get_ftrace.sh -t 24 -p 1 -m 131072 -z 0 -e "sched|irq|lock|rcu|workqueue|timer|signal" -o langraph > /ks/capture.log 2>&1 &
CPID=$!
TF=/sys/kernel/debug/tracing/tracing_on
# 坑：内核启动后 tracing_on 默认就是 1，但 tracer=nop、buffer 里没有事件。
# get_ftrace.sh 的 init_ftrace 会先置 0、清空 buffer，最后才置 1 真正开始记录。
# 必须等完整的 1 -> 0 -> 1 转换，之后才能起 iperf。
for i in $(seq 1 200); do
    [ "$(cat $TF 2>/dev/null)" = "0" ] && break
    sleep 0.1
done
stamp "FTRACE_INIT_SEEN (tracing_on went 0)"
for i in $(seq 1 250); do
    [ "$(cat $TF 2>/dev/null)" = "1" ] && break
    sleep 0.1
done
TON=$(cat $TF 2>/dev/null)
stamp "FTRACE_ON tracing_on=$TON"
echo "[1] ftrace tracing_on=$TON  CPID=$CPID"
sleep 2          # 保底：确保主进程的 fork+exec 稳稳落在窗口内
stamp "PRE_IPERF_GUARD done"

########## ② Guest 侧收包进程（one-off，自然退出） ##########
echo "[2] guest iperf3 server :5201 -1 (one-off)"
iperf3 -s -p 5201 -1 </dev/null > /ks/guest_server.log 2>&1 &
GSID=$!
stamp "GUEST_SERVER_START pid=$GSID"
sleep 2
stamp "GUEST_SERVER_LISTEN"
# READY 信号：告知 Host「ftrace 已开 + guest server 已就绪」，Host 才开始起自己的 iperf
printf 'READY ftrace_on=%s server_pid=%s uptime=%s\n' "$TON" "$GSID" "$(awk '{print $1}' /proc/uptime)" \
    | nc 10.0.2.2 9998 2>/dev/null
stamp "READY_SENT"

########## ③ Guest 侧发包进程（与 Host client 错开 3s，避免 slirp 控制连接冲突） ##########
sleep 3
echo "[3] guest iperf3 client -c 10.0.2.2:5211 -P 2 -t 12 -b 1M -l 256"
iperf3 -c 10.0.2.2 -p 5211 -P 2 -t 12 -b 1M -l 256 -J </dev/null > /ks/guest_client.json 2> /ks/guest_client.err &
GCID=$!
stamp "GUEST_CLIENT_START pid=$GCID lowrate"

########## ④ 等所有 iperf 自然结束（全程不 kill） ##########
wait $GCID; GCRC=$?
stamp "GUEST_CLIENT_EXIT rc=$GCRC pid=$GCID"
# guest server 是 one-off：host client 会话结束后自动退出
wait $GSID; GSRC=$?
stamp "GUEST_SERVER_EXIT rc=$GSRC pid=$GSID"
RES=$(ps 2>/dev/null | grep -c '[i]perf3')
stamp "IPERF_ALL_EXITED residual=$RES"
echo "[4] 所有 Guest iperf 进程已自然结束 (client rc=$GCRC, server rc=$GSRC, 残留=$RES)"

########## ⑤ 等采集自然结束（必须晚于 ④） ##########
wait $CPID; CRC=$?
stamp "CAPTURE_EXIT rc=$CRC"
echo "[5] get_ftrace.sh 结束 rc=$CRC"
sleep 8   # perf record 是 sleep(t+5)，等 perf.data 写完

# 结束中断计数：对比 before 可知本轮 eth0 硬中断真实发生次数
cat /proc/interrupts > /ks/interrupts.after.txt 2>/dev/null
cat /proc/net/dev    > /ks/netdev.after.txt 2>/dev/null

########## ⑥ 重导 perf.log + 并入辅助文件 ##########
cd /work/langraph
# 脚本 save_perf 早于 perf record 结束，这里重新导出完整 perf.log
perf script -F comm,pid,tid,cpu,flags,time,event,ip,sym,dso,trace,symoff,dso,ipc \
    > perf.log.new 2> /ks/perf_script.err
if [ -s perf.log.new ]; then mv perf.log.new perf.log; else rm -f perf.log.new; fi
stamp "PERF_LOG lines=$(wc -l < perf.log)"
cd /work
cp /ks/kallsyms.txt /ks/modules.txt /ks/version.txt /ks/timeline.txt \
   /ks/capture.log /ks/guest_server.log /ks/guest_client.json \
   /ks/guest_client.err /ks/perf_script.err \
   /ks/interrupts.before.txt /ks/interrupts.after.txt \
   /ks/netdev.before.txt /ks/netdev.after.txt langraph/
echo "[6] langraph/ 内容:"
ls -lh langraph/ | sed 's/^/    /'

########## ⑦ 回传 ##########
tar cf /tmp/result.tar -C /work langraph
echo "[7] tar=$(wc -c < /tmp/result.tar) bytes"
cat /tmp/result.tar | nc 10.0.2.2 9999 && echo "[7] nc push OK" || echo "[7] nc push FAILED"
cp /tmp/result.tar /work/result.tar
httpd -p 8080 -h /work 2>/dev/null &
sleep 6
echo "########## GUEST DONE ##########"
poweroff -f
