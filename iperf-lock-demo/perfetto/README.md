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

## 关系字段版（SQL 可 join 分析任意事件关系）

蓝方审查指出：flow 箭头是按同 CPU + 时间窗猜的展示层，**不适合当分析基础**
（全量 9 万条 flow 里约 2.4 万条终点是合成事件）。真正的因果关系改为**写进每个事件的
args**，可直接用 SQL join 精确分析，不依赖 UI 里箭头是否可见。

| 文件 | 大小 | 说明 |
|---|---:|---|
| `lock-rel-chain.pftrace.gz` / `lock-rel-chain.json` | 36 KB / 449 KB | 13 ms 窗口关系字段版 |
| `lock-rel-full.pftrace.gz` / `lock-rel-full.json.gz` | 17 MB / 15 MB | 全量关系字段版（原件 206 MB / 244 MB） |
| `relations-*.json` | 3 KB | SQL 可查询性验证报告 |

关系字段（args 表，key 带 `debug.` 前缀）：`causal_id`（同链共享）、
`parent_ts_ns`+`parent_event`（直接前驱，ns 与 `slice.ts` 同单位）、
`waker_tid`/`wakee_tid`（唤醒双方）、`irq_depth`/`parent_irq_ts_ns`（中断嵌套）、
`lock_holder_tid`/`lock_holder_inferred`（推断持锁者，demo 启发式）。

全量实证（见 `relations-full-report.json`）：**188 万条前驱边** SQL join 命中、
**44,757 条因果链**（最大一条链 13 个事件）、**46,639 条唤醒边**。验证脚本与查询模板见
工具仓 `ftrace2perfetto/relations_check.py` 与 `README.md` §6.1。

> 修复记录：`lock_holder_tid` 曾用 `-1` 作「未知」哨兵，导致 protobuf varint 对负数
> 算术右移死循环（转换从 60 s 恶化到 220 s+ 且 OOM）。现改为缺省（SQL 里 NULL），
> 并给 `_vi()` 加 int64 补码防御。修复后全量转换恢复 63 s。

