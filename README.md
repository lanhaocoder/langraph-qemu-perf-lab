# langraph-qemu-perf-lab

在**云沙箱**（无 KVM、容器封锁 `perf_event_open` 和 tracefs）里，用 QEMU TCG 起最小 Guest 跑通 [LanGraph](https://github.com/lanhaocoder/LanGraph) 的 ftrace/perf 分析链路，并在 Guest 内做 20 并行流双向 TCP 压测、用 `get_ftrace.sh -p 1` 同时抓 perf 与 ftrace 日志的实验记录。

全部结论均由实测得出，不是推演。

## 四个实验，四个结论

| # | 实验 | 结论 |
|---|---|---|
| 1 | 云沙箱宿主机能否用 perf / ftrace | **不能。** `perf_event_open` 直接 `EPERM`，连 `task-clock` 软件事件都被拒；tracefs/debugfs 挂载 `permission denied`，`/sys/kernel/tracing` 是空目录。但**离线解析完全可用**（`libtraceevent`/`libtracefs`/`libtracecmd` 是纯用户态库） |
| 2 | 起 QEMU Guest 能否绕开 | **能。** Guest 有独立内核、自带 tracefs，ftrace 采集、perf 软件事件、`langraph` 执行全部跑通 |
| 3 | Guest 内 20 线程双向 iperf + perf 采集 | **跑通。** 抓到 108,944 个 `cpu-clock` 采样、701,520 条 sched/irq 事件；双向吞吐 735/730 Mbps 基本对称 |
| 4 | 完整 iperf 进程生命周期采集 | **7/7 判据全部 PASS，42/42 个 pid 生命周期闭合**（40 工作线程 + 2 主进程）。所有 `fork→exec→wakeup_new→switch→exit→free` 全落在 `get_ftrace.sh` 窗口内，perf 与 ftrace 同时抓。详见 [`iperf-lifecycle/`](iperf-lifecycle/README.md) |

详细记录见 [`docs/01-云空间环境评估.md`](docs/01-云空间环境评估.md) 和 [`docs/02-iperf-perf实验报告.md`](docs/02-iperf-perf实验报告.md)。

## 目录结构

```
docs/                        实验文档与结论
  01-云空间环境评估.md        宿主机能力实测 + QEMU 方案
  02-iperf-perf实验报告.md    20 线程双向 iperf + perf 采集报告
qemu-guest/                  最小 Guest 构建与启动（实验 2）
  boot.sh                    一条命令启动 Guest
  build-guest.sh             修改代码后重建 initramfs
  guest-init.sh              Guest 的 /init
  perftest.c                 Guest 内 perf_event_open 能力探测
  串口日志.txt                完整启动与执行日志
  kallsyms/                  Guest 运行时内核符号表（含 e1000 模块符号）+ System.map
iperf-perf/                  双向流量 + perf/ftrace 采集（实验 3）
  run.sh                     宿主机编排：iperf server + 反向流量 + 日志回传
  guest-init.sh              Guest /init：挂 tracefs → 采集 → 压测 → 回传
  qemu.log                   Guest 串口输出
  samples/                   iperf3 双向结果 json、采集脚本日志
  logs/                      统计摘要、日志头部样本
    full/                    完整原始日志（gzip 压缩，577 MB → 21.5 MB）+ 还原脚本
  langraph-native/           get_ftrace.sh 原生 `langraph/` 产物归档（9.23 MB）+ 复现脚本
iperf-lifecycle/             完整 iperf 进程生命周期采集（实验 4）
  run.sh / guest-init.sh      Host 编排 + Guest /init（READY 信号同步、自然退出）
  analyze_lifecycle.py       7 项判据闭合校验（42 个 pid）
  draw_lifecycle_gantt.py    真实数据甘特图
  生命周期校验报告.md         逐项判据与实测数字
  lifecycle-gantt.png        ★ 42 个 pid 的 fork→free 横道图
  iperf-lifecycle.tar.gz     ★ `langraph/` 原生产物压缩归档（perf + ftrace，12.0 MB）
  round1/ round2/            前两轮迭代留档
  MANIFEST.txt               全量 sha256 清单（sha256sum -c 可校验）
```

## 关键实测数据

### iperf3 3.16，20 并行流 `--bidir`

| 方向 | 流数 | 发送 | 接收 |
|---|---|---|---|
| 宿主机 → Guest | 40（20×2） | 502.66 MB / **335.07 Mbps** | 5.01 MB / 3.34 Mbps |
| Guest → 宿主机 | 40（20×2） | 1104.81 MB / **734.88 Mbps** | 1098.38 MB / **729.95 Mbps** |

第二组收发基本对称，双向坐实；第一组接收侧偏低是 TCG 下 Guest 发包能力受限。

### perf 内核栈热点（108,944 采样）

```
21656  __napi_poll                       21537  e1000_clean
21119  e1000_clean_rx_irq                19304  napi_gro_receive
18672  netif_receive_skb_list_internal   18403  ip_list_rcv
17815  ip_local_deliver                  14665  tcp_rcv_established
 8828  tcp_write_xmit                     8544  __tcp_push_pending_frames
```

收包路径（NAPI → IP → TCP）占主导，符合双向 TCP 压测预期。完整 top30 见 [`iperf-perf/logs/perf-hotspots.txt`](iperf-perf/logs/perf-hotspots.txt)。

### ftrace 事件分布（4,665,579 行）

```
59672 sched_stat_runtime    25822 sched_switch
13033 sched_waking          13033 sched_wakeup
 1136 sched_migrate_task     270 irq_handler_entry/exit
```

### 完整 iperf 生命周期（实验 4，重抓 180,083 条事件）

| 判据 | 实测 |
|---|---|
| 生命周期闭合 | **42/42**（工作线程 40/40、主进程 2/2），`unclosed_pids=[]` |
| 采集窗口 | `11.068 ~ 37.075 s`，跨度 **26.007 s** |
| ftrace 先于 iperf | 最早创建 `13.194 s`，**先行余量 2.126 s** |
| iperf 早于采集结束 | 最晚 `free=28.673 s`，**尾部余量 8.402 s** |
| iperf 全程 | `17.530 s`（< 60 s），且早于 `get_ftrace.sh` 结束 |
| perf 与 ftrace 同时 | `perf.log` 1,145,692 行 / 63.89 MB；`perf.data` 12.90 MB |
| 双向吞吐 | Host→Guest 520.92 Mbps；Guest→Host 429.41 / 424.84 Mbps，无 error |

关键时序：Guest 内**先**起 `get_ftrace.sh -t 26 -p 1 -m 131072 -o langraph`（perf+ftrace 同时、ring buffer 128 MB/CPU 防止开头 fork 事件被覆盖），再起两端 one-off 收包 server（`-1`）、两端各 20 线程发包 client（`-P 20 -t 10`），全部 `wait` 自然退出后才结束采集。完整时间轴见 `iperf-lifecycle/langraph/timeline.txt`（在压缩包内）。

### 完整硬中断因果链（实验 5，低速小包重抓）

实验 4 的稳定打流段 eth0 硬中断为 0，因果链只能从 NET_RX 软中断起。**根因不是 tracepoint
没开**，而是 e1000 的 NAPI 中断屏蔽（详见 [`iperf-lifecycle/perfetto/README.md`](iperf-lifecycle/perfetto/README.md)）。
本轮把流量改成 `-P 2 -b 1M -l 256`（2.0 Mbps），让每轮 poll 都能排空队列、重开中断。

| 指标 | 实验 4 | 实验 5 |
|---|---:|---:|
| `irq_handler_entry: irq=11 eth0` | 66 | **18,522** |
| `napi_poll` 事件 | 未抓 | **18,522**（与硬中断 1:1） |
| `/proc/interrupts` IRQ11 增量 | — | **+18,698** |
| 完整因果链候选 | 0 | **17,586 条** |

真实链例（t0 = 14.561145 s）：硬中断 → +10 µs `softirq_raise(NET_RX)` →
+48 µs `napi_poll(work=0)` → +251 µs `sched_waking(iperf3:380)` →
+278 µs `sched_wakeup` → +391 µs `sched_switch` 上 CPU3。

归档在 [`iperf-hardirq/`](iperf-hardirq/)：原始采集 `iperf-hardirq.tar.gz`（21 MB，
含 399 MB `ftrace.log`）、复现脚本、统计脚本与结果；转换产物在
[`iperf-hardirq/perfetto/`](iperf-hardirq/perfetto/)（3 ms 单链 + 26 s 全量，
四份均通过官方校验、零告警）。**先看** `hardirq-result.html`（自包含结果页）。

### 带调用栈标注的 trace（实验 6）

用 `tracepoint_stacks.py` 解析 `ftrace.log` 里每个 tracepoint 紧跟的内核/用户栈，
再通过 `ftrace2perfetto.py --stacks` 把 `stack_start`（栈顶触发点）、`stack_end`（栈底入口）、
`stack_depth` 挂到 slice / instant / flow 的 args；同时**默认隐藏 S 可中断睡眠段**。
399 MB 日志里 315,293 个事件行 100% 带内核栈、99.8% 带用户栈。

归档在 [`iperf-hardirq/perfetto/stacks/`](iperf-hardirq/perfetto/stacks/)：
3 ms 单链原件 + 26 s 全量 gzip（97 MB→3.6 MB）+ 315,293 条逐条索引 + 校验报告，
四份零 health 告警；该目录只含 Perfetto 文件与数据索引，无 HTML/PNG 展示物。

## 环境

| 项目 | 值 |
|---|---|
| QEMU | 8.2.2，`-accel tcg`（无 `/dev/kvm`，CPU 不暴露 vmx/svm） |
| Guest 内核 | Ubuntu `6.8.0-31-generic` |
| Guest 规格 | 实验 2：2 vCPU/1 GB；实验 3：4 vCPU/2 GB |
| 根文件系统 | busybox 静态 initramfs（实验 3 装入 e1000.ko、perf、iperf3 后 25 MB） |
| 网络 | `-netdev user`（slirp）+ `e1000` 网卡 + hostfwd |

## 复现

需要宿主有 `qemu-system-x86`、`busybox-static`、`iperf3`、`trace-cmd` 及 `libtrace{event,fs,cmd}-dev`。

```bash
# 实验 2：起 Guest 跑 langraph 与采集脚本
cd qemu-guest && ./boot.sh > run.log 2>&1

# 实验 3：双向 20 流 iperf + perf 采集（约 2~3 分钟）
cd iperf-perf && ./run.sh

# 实验 4：完整 iperf 生命周期重抓（约 5 分钟）
cd iperf-lifecycle && ./run.sh

# 实验 5：低速小包重抓，带完整硬中断因果链（约 5 分半）
cd iperf-hardirq && ./run.sh
# 只复核归档结论、不重跑 QEMU：
cd iperf-hardirq && tar xzf iperf-hardirq.tar.gz && \
  python3.11 analyze_hardirq.py langraph -o hardirq-analysis.json

# 或只复核归档结论，不重跑 QEMU
cd iperf-lifecycle && tar xzf iperf-lifecycle.tar.gz && \
  python3.11 analyze_lifecycle.py langraph -o lifecycle_report.json --md 生命周期校验报告.md
```

在 WSL2（KVM 可用）上把 `boot.sh` / `run.sh` 里的 `-accel tcg` 换成 `-enable-kvm` 即可获得硬件加速。

## 必须知道的限制

1. **TCG 下采到的时序、带宽、调度延迟都没有真实性能意义。** 指令是软件模拟的，时间戳、调度时序、锁竞争行为都被模拟器扭曲。这些数据只能验证「链路能跑通、代码路径和符号热点能被采到」，**不能**用来分析真实延迟、锁竞争或性能回归。真实结论必须在真机或 KVM 加速的 WSL2 上采。
2. **硬件 PMU 不可用。** TCG 不虚拟 PMU，perf 只能用软件事件和 tracepoint，`cycles` / `instructions` / `cache-misses` 全部 `ENOENT`。本仓库的 perf 数据用的是 `cpu-clock` 软件事件。
3. **`get_ftrace.sh` 未做任何修改。** Guest 内放了一个 `bin/perf` wrapper，把脚本默认的 `cycles` 换成 `-e cpu-clock`、`-g --call-graph=dwarf` 换成 `fp`，脚本本身保持原样。
4. **完整日志已压缩入库。** `ftrace.log` 253 MB、`perf.log` 98 MB 原本超 GitHub 100 MB 单文件上限，但日志重复度极高，`gzip -9` 后两轮共 577 MB 压到 **21.5 MB（3.7%）**，全部无损存入 `iperf-perf/logs/full/`，`./unpack.sh` 一键还原。`logs/` 下的头部样本和统计摘要仍然保留，方便不解压直接看。未入库的只剩内核、initramfs 等可由脚本重建的二进制。
5. **运行时符号表单独保存了，且与新归档严格对应。** `qemu-guest/kallsyms/` 存了 Guest 的 `/proc/kallsyms`（203,527 行，含 688 个 e1000 模块符号）与 `System.map`，`nokaslr` 抓、地址固定。`run1/` / `run2/` 那两轮是默认 KASLR 采的，基址带随机偏移，**不能**给旧数据做地址换算；但 `iperf-perf/langraph-native/` 这轮用 `nokaslr` 重跑，`langraph/` 里自带 `kallsyms.txt` 与完整的 `perf.data`，两者严格对应，可以放心重新符号化。`perf.log` 本身是已符号化文本，任何一轮都不受影响。详见两处 README。`iperf-perf/guest-init.sh` 已加自动导出，以后每轮采集会随日志一起回传。
6. **`get_ftrace.sh` 原生产物是 `langraph/` 目录。** 脚本默认 `output="langraph"`、`-p 1` 同时开 perf 与 ftrace、`-z 1` 自动压缩。前几轮为了单独回传日志用了 `-o /langraph_out -z 0` 改名并拆分，没留下这个标准产物；`iperf-perf/langraph-native/` 是按原生语义重跑补齐的归档（含 `perf.data` / `ftrace_symbol.log` / `kallsyms.txt`）。
7. **实验 4 的生命周期结论只代表「采集完整性」，不代表性能。** 判定标准是 42 个 iperf3 pid 的 `fork/exec → exit → free` 是否全落在采集窗口内，判据与数字见 `iperf-lifecycle/生命周期校验报告.md`。两个易踩的坑：① `sched_process_free` 的行前缀 comm 是 `<idle>`/`ksoftirqd`，**被释放的 iperf3 只在 args 的 `comm=iperf3 pid=...` 里**，按行前缀匹配会漏；② 内核启动后 `tracing_on` 默认就是 `1`（tracer=nop、无事件），必须等 `init_ftrace` 走完完整的 **1→0→1** 转换才能起 iperf，否则 server 会早于真正开录。TCG 下的时间戳同样只用于事件先后关系，不能当真实延迟。

## 相关

- [LanGraph](https://github.com/lanhaocoder/LanGraph) —— 基于 trace-cmd 的 ftrace 调度延迟可视化分析工具，本仓库是它的环境验证配套
- 云沙箱的离线分析路径：`trace.dat` 架构无关，x86_64 上可直接解析 arm64 目标板采集的数据
