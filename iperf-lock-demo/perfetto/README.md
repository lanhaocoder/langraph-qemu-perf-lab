# 锁/同步事件关联 demo 的 Perfetto trace

由 `../langraph/ftrace.log`（1.1 GB，未入库）转换，工具见
[`LanGraph/script/ftrace2perfetto/`](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)。

| 文件 | 大小 | 说明 |
|---|---:|---|
| `lock-chain.pftrace` / `.json`（+.gz） | 50 KB / 67 KB | **先看**：3 ms 窗口，一条 rwsem 读锁竞争（等锁 909 µs）+ 唤醒 flow + rcu/timer/workqueue |
| `lock-full.pftrace.gz` | 8 MB | 24 s 全量（原件 149 MB），含全部调度/中断/锁/RCU/timer 事件 |
| `lock-full.json.gz` | 7 MB | 全量 Chrome JSON（原件 181 MB） |
| `lock-*.stats.json` | 5 KB | 转换统计 |

`.gz` 可被 Perfetto UI / `trace_processor_shell` 直接加载、自动解压，无需 gunzip。

**看点**：左侧 group「锁 / 同步事件轨」→ 每把 `lock_addr` 一条子轨。`contention_begin` 是 instant
点，`等锁：iperf3` 是 begin→end 的 slice，args 含 `lock_addr` / `flags`(READ / SPIN\|WRITE) /
`wait_us` / `waiter_cpu` / `comm` / `tid` / `stack_start`=`rwsem_down_read_slowpath`。
配合「CPU 运行泳道」「线程状态轨」「IRQ / softirq 轨」看锁等待与调度/中断的先后关系。

四份均通过 `trace_processor_shell v59.1` 校验、零 health 告警（见 `../verify-lock.json`）。
TCG 软件模拟，延迟/带宽无性能意义，仅作事件链路 demo。详见 `../README.md`。
