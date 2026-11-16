# 同步/锁事件关联 demo（iperf3 双向 TCP）

这不是性能分析，是一个**内核事件关联可视化 demo**：把一次真实 iperf3 收发过程中，
调度、硬/软中断、锁竞争、RCU、定时器、工作队列、信号等 tracepoint 串到同一条时间轴上，
在 Perfetto UI 里按「线程 / CPU / 锁 / IRQ」轨查看它们的因果关系。

按蓝方审查的反馈做了两处修正：
- **开锁相关事件**：`lock:contention_begin/end`、`rcu:*`、`workqueue:*`、`timer:*`、
  `signal:*` 全开；**napi 已去掉**。
- **明确边界**：TCG 软件模拟、x86_64 6.8 guest（非 arm64 v6.18），所有延迟/带宽数字
  **没有性能意义**，只用来验证事件链路与代码路径。架构/版本与 v6.18 arm64 不必精确一致，
  tracepoint 代码路径一致即可。

## 采集

Guest 内先起 `get_ftrace.sh -t 24 -p 1 -m 131072 -e "sched|irq|lock|rcu|workqueue|timer|signal"`，
再起双向 iperf3（`-P 2 -t 12 -b 1M -l 256`，低速小包保证各事件都有样本）。
Guest 是 `qemu-system-x86_64 -accel tcg` + e1000，4 vCPU。

原始 `ftrace.log` 1.1 GB / 4,028 万行（其中 3,671 万栈帧行），**不入库**；
`langraph/perf.log` 55 MB 以 `.gz` 入库，其余小文件原价。

## 抓到的事件（578,220 条 tracepoint）

| 类别 | 事件 | 数量 |
|---|---|---:|
| 调度 | `sched_switch` / `sched_waking` / `sched_wakeup` / `sched_stat_runtime` / `sched_migrate_task` / fork-exec-exit-free | ~115K |
| 中断 | `irq_handler_entry/exit` 16,101、`softirq_*` ~72K、`tasklet_*` 184 | ~104K |
| **锁竞争** | **`contention_begin/end` 各 2**（rwsem 读锁 + spinlock 写锁） | 4 |
| RCU | `rcu_utilization`（scheduler-tick 起止） | 166,086 |
| Timer | `hrtimer_*` / `timer_*` | ~335K |
| Workqueue | `queue_work` / `execute_start/end` | 512 |
| Signal | `signal_generate` / `signal_deliver` | 38 |

**锁竞争样本**（真实栈，正是同步原语慢路径）：

```
t=24.791948  contention_begin: lock=ca58c639 flags=READ
             iperf3:385 on CPU3, 栈: rwsem_down_read_slowpath+0x2ee
t=24.792857  contention_end:   lock=ca58c639 ret=0
             iperf3:385 on CPU0, 等锁 909 µs, 栈: rwsem_down_read_slowpath+0x413

t=26.103573  contention_begin: lock=95dcc6ed flags=SPIN|WRITE
             iperf3:390 on CPU1, 栈: rwsem_down_read_slowpath
t=26.103619  contention_end:   lock=95dcc6ed ret=0, 等锁 46 µs
```

## 怎么看

Perfetto UI（ui.perfetto.dev）直接打开 `.pftrace` 或 `.pftrace.gz`（自动解压，无需 gunzip）：

1. **`perfetto/lock-chain.pftrace`**（50 KB，3 ms 窗口）——先看这个：一条 rwsem 读锁竞争
   从 `contention_begin` 到 `contention_end`（等锁 909 µs），同窗口有唤醒 flow、`rcu` / `timer` / `workqueue` 事件。
2. **`perfetto/lock-full.pftrace.gz`**（8 MB，24 s 全量）——两条锁竞争 + 全部调度/中断/RCU/timer 事件。
3. 左侧按 group 展开：`CPU 运行泳道` / `线程状态轨（按 tid）` / `IRQ / softirq 轨` /
   `进程生命周期事件` / **`锁 / 同步事件轨`**（其下每把 `lock_addr` 一条子轨，还有 Rcu/Timer/Workqueue/Signal 事件子轨）。
4. 点选「等锁：iperf3」slice，Args 里有 `lock_addr` / `flags` / `wait_us` / `waiter_cpu` /
   `comm` / `tid` / `stack_start` / `stack_end` / `stack_depth`（起始帧就是 `rwsem_down_read_slowpath`）。

注意 Perfetto UI 的 flow 箭头默认不显示，需点选事件才出现。

## 转换工具

源码见 [`LanGraph/script/ftrace2perfetto/`](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)。
本轮新增：

- `WANTED` 增加锁/RCU/workqueue/timer/signal 事件；新增 `G_LOCK=5`「锁 / 同步事件轨」分组；
- `contention_begin/end` 按 `lock_addr`（ftrace 打的是裸 hex）配对成「等锁」slice，
  args 带锁地址、flags、ret、等待者 comm/tid/cpu、wait_us 与调用栈；
- `rcu_utilization`（自由文本）等事件画成 instant 点，各自成子轨；
- 等待/终止态（S/D 睡眠、Z/X 退出）仍是 instant 事件点（上一轮的改动）。

## 校验

四份 trace 全部通过 `trace_processor_shell v59.1`，零 health 告警（见 `verify-lock.json`）。
`.gz` 自解压已验证：`trace_processor_shell` 直读 `.pftrace.gz` 的事件分布与原件完全一致。

## 边界（再说一次）

TCG 软件模拟 + x86_64 6.8 guest ⇒ 延迟、带宽、时间戳不能用于性能判断；
锁竞争只有 2 组样本，足以演示事件关联，不足以统计分析。要分析 v6.18 arm64 的真实锁行为，
需在目标架构上用 `perf lock` / lockstat / bpftrace 重新采。
