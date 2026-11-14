# 完整 iperf 进程生命周期采集（QEMU + get_ftrace.sh）

> 本目录是 `langraph-qemu-perf-lab` 仓库中的归档（实验 4）。
> 未压缩的 `langraph/` 原生产物（约 305 MB）以 [`iperf-lifecycle.tar.gz`](iperf-lifecycle.tar.gz) 提供，
> `tar xzf iperf-lifecycle.tar.gz` 即还原；`result.tar` 等传输中间产物、`.zip` 打包件按仓库 `.gitignore` 不入库。
> 复现脚本已改为「产物输出目录 = 脚本所在目录」，在本目录直接 `./run.sh` 即可。

在 QEMU Guest 内**重新采集**一次「20 线程双向 TCP iperf」的**完整进程生命周期**：
所有 `iperf3` 进程的 `sched_process_fork` → `sched_process_exec` → `sched_wakeup_new`
→ `sched_switch` → `sched_process_exit` → `sched_process_free` 全部落在
`get_ftrace.sh` 的采集窗口内，且 **perf 与 ftrace 同时抓包**。

> 详细的逐项判据与实测数字见 [`生命周期校验报告.md`](生命周期校验报告.md)，
> 机器可读结果见 `lifecycle_report.json`。

**最终结果：7/7 判据全部 PASS，42/42 个 iperf3 pid 生命周期完整闭合**
（40 个工作线程 + 2 个主进程，`fork/exec → exit → free` 全在窗口内）。
采集窗口 26.007 s，ftrace 先行余量 2.126 s，iperf 全部结束后仍有 8.402 s 余量；
iperf 全程 17.530 s（< 60 s），且早于 `get_ftrace.sh` 结束。

---

## 1. 严格时序（用户要求）

| 顺序 | 动作 | 位置 |
|---|---|---|
| ① | **先起 `get_ftrace.sh -p 1`**（perf + ftrace 同时） | Guest |
| ② | 起 **iperf 收包进程**（`iperf3 -s -1`，one-off） | Guest `:5201` + 沙盒 `:5211` |
| ③ | 起 **iperf 发包进程**（`iperf3 -c -P 20 -t 10`） | Guest + 沙盒，各自 20 线程 |
| ④ | 抓包 **10 秒**，双方 client 跑完 `-t 10` 自然退出 | — |
| ⑤ | one-off server 服务完会话后**自然退出**（不 kill） | — |
| ⑥ | 确认 `残留=0`，此时 `get_ftrace.sh` **仍在采集** | Guest |
| ⑦ | 采集窗口结束 → `save_ftrace` / `save_perf` → 回传 | — |

所有 iperf 进程在 **1 分钟内**结束，且**早于 `get_ftrace.sh` 结束**。
`timeline.txt` 用 `/proc/uptime` 逐节点留证（与 ftrace 的 `perf` clock 同源）。

## 2. 拓扑（两端分别起收包 / 发包进程）

```
   沙盒 (Host)                              QEMU Guest
 ┌───────────────────┐                ┌────────────────────────┐
 │ iperf3 -s :5211 -1│◀─── 20 流 ─────│ iperf3 -c 10.0.2.2:5211│  Guest 发包
 │  (收包, one-off)  │                │  -P 20 -t 10           │
 │                   │                │                        │
 │ iperf3 -c 127.0.0.1:15201 ───20 流──▶│ iperf3 -s :5201 -1    │  Guest 收包
 │  -P 20 -t 10      │   hostfwd      │  (收包, one-off)       │
 │  (发包)           │   15201→5201   │                        │
 └───────────────────┘                └────────────────────────┘
              ↑ ftrace/perf 只在 Guest 内核采集，故「生命周期」指 Guest 内 iperf
```

- `10.0.2.2` 是 QEMU SLIRP 的 Host 地址；`hostfwd 15201→5201` 把 Host 方向流量送进 Guest。
- 每个方向 20 条并行流（`-P 20`），双向共 40 条 → Guest 内产生 **40 个 iperf3 工作线程** + 2 个主进程。
- ftrace 只采 Guest 内核；沙盒侧 iperf 仅作流量对端，其生命周期**不在** ftrace 数据里。

## 3. 关键设计

| 设计 | 原因 |
|---|---|
| `get_ftrace.sh -t 26 -p 1 -m 131072 -z 0 -o langraph` | `-p 1` perf+ftrace 同时；`-m` 把 ring buffer 提到 128 MB/CPU，防止高负载**覆盖掉开头的 fork 事件**；`-o langraph` 保持脚本原生输出语义 |
| `iperf3 -s -1`（one-off） | 服务完一个 client 会话后**自然退出**，这样 `sched_process_exit/free` 才会出现。默认的 `-D` 常驻 server 永远抓不到终止 |
| READY 信号（Guest → Host `:9998`） | 不能用 `nc -z` 探测 one-off server 端口——探测连接本身会**消耗掉它唯一的一次会话** |
| 等待 `tracing_on` 完成 **1 → 0 → 1** 转换 | 内核启动后 `tracing_on` 默认就是 `1`（tracer=nop、无事件）；脚本 `init_ftrace` 会先置 `0` 再置 `1`。只判断 `==1` 会误判，导致 server 比真正开录早启动 |
| `-1` 之后不 `kill`，全部用 `wait` | 保证「自然结束」，并在 `timeline.txt` 记录每个进程的 `rc` 与残留数 |
| 两套 client 错开 3 秒 | 避免 SLIRP 上两组 20 流同时建控制连接被挤掉（历史踩过的坑） |
| `nokaslr` + 导出 `/proc/kallsyms` | 地址固定，便于离线 `addr2line` / 解析 perf 采样 |
| 脚本结束后等 8 秒再重导 `perf.log` | `perf record -- sleep(t+5)` 比脚本的 `sleep t` 晚 5 秒结束，`save_perf` 早于 `perf.data` 写完 |

## 4. 三轮迭代（真实踩坑记录）

| 轮次 | 结果 | 根因与修复 |
|---|---|---|
| 1 | 工作线程 40/40 闭合；server 主进程缺 `fork/exec` | server 在 `tracing_on=1` 后 0.01 s 就创建，恰好卡在边界前 ~26 ms → 加 1 s guard |
| 2 | 工作线程 40/40；`exit=42 free=42`；主进程 1/2 | 检测循环把**内核初始的 `tracing_on=1`** 当成「已开启」，server 实际比开录早 2.8 s → 改为等完整 1→0→1 |
| 3（最终） | **7/7 PASS，42/42 闭合**（工作线程 40/40、主进程 2/2） | 修好检测 + 保底 2 s |

另外修正了分析器对 `sched_process_free` 的识别：该 tracepoint 在 `free_task()`
上下文触发，行前缀 comm 是 `<idle>` / `ksoftirqd`，**被释放的 iperf3 在 args 里**
（`comm=iperf3 pid=369`），不能按行前缀判断。

## 5. 目录

```
iperf-lifecycle/                        本目录（仓库内归档）
├── README.md                           本说明
├── 生命周期校验报告.md                 7 项判据 + 事件统计 + 主进程明细
├── lifecycle_report.json              机器可读校验结果
├── lifecycle-gantt.png                ★ 完整生命周期甘特图（每 pid 一条横道）
├── run.sh                             Host 编排：READY 同步 + 双向 iperf + 接收/压缩
├── guest-init.sh                      Guest /init：采集 + 完整生死 + 时间轴 + 回传
├── analyze_lifecycle.py               生命周期闭合校验（生成 JSON + Markdown）
├── draw_lifecycle_gantt.py            真实数据甘特图生成
├── host_client.json / host_server.log / host_timeline.txt
├── qemu.log                           Guest 串口日志
├── ready.flag                         Guest → Host 的 READY 信号原文
├── iperf-lifecycle.tar.gz             ★ langraph/ 原生产物压缩归档（12.0 MB）
├── round1/ round2/                    前两轮的时间轴、报告与试画甘特图（迭代留档）
│
│   ── 以下为 `tar xzf iperf-lifecycle.tar.gz` 还原出的 langraph/（不入库）──
└── langraph/
    ├── ftrace.log                     调度/IRQ 全量文本（含内核栈回溯，219 MB）
    ├── perf.log / perf.data           perf 采样与脚本化输出
    ├── ftrace_format.log / ftrace_symbol.log
    ├── kallsyms.txt / modules.txt / version.txt
    ├── timeline.txt                  ★ 关键节点时间轴（完整性证据）
    ├── capture.log / guest_client.json / guest_server.log
    └── perf_script.err
```

## 6. 复现

```bash
# 只复核归档结论（无需重跑 QEMU）：解压后即可重新校验与画图
tar xzf iperf-lifecycle.tar.gz
python3.11 analyze_lifecycle.py langraph -o lifecycle_report.json --md 生命周期校验报告.md
python3.11 draw_lifecycle_gantt.py langraph/ftrace.log -o lifecycle-gantt.png

# 完整重跑（会覆盖本目录内上述产物，约 5 分钟，TCG 下 addr2line 后处理较慢）
cd /path/to/langraph-qemu-perf-lab/iperf-lifecycle && ./run.sh
```

前置资产（沿用历史工作区）：`/tmp/vmlinuz`、`/tmp/guest/`（含 `iperf3`、`perf`
wrapper、`get_ftrace.sh`、`e1000.ko`）、`qemu-system-x86_64`、`iperf3 3.16`、`pigz`。

## 7. 提醒

- `ftrace.log` 里占绝大多数的 ` => ` 行是 `stacktrace=1` 产生的内核栈回溯，
  **不是解析失败**；校验脚本已单独统计。
- `capture.log` 中的 `'trace' not valid for ... events. Ignoring.` 是
  `perf script -F ...trace...` 对非 tracepoint 事件的常规告警，不影响采样。
- `timeline.txt` 的 `CAPTURE_EXIT` 包含 `save_ftrace` / `addr2line` 后处理耗时；
  **真实采集窗口以 `ftrace.log` 的 tmin~tmax 为准**（约 26 s）。
