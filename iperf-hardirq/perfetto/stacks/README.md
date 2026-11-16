# 带调用栈标注的 Perfetto trace（实验 6）

在实验 5 的完整硬中断因果链基础上，把 `ftrace.log` 里每个 tracepoint 紧跟的
`<stack trace>` / `<user stack trace>` 解析出来，作为 args 挂到对应 slice / instant / flow 上；
并把**等待/终止态（S/D 睡眠、Z 僵尸、X 退出）画成 instant 事件点**，不再铺满状态轨长条。

数据源仍是 `../iperf-hardirq.tar.gz` 解出的 `langraph/ftrace.log`（低速小包双向 TCP iperf，
26 s，4 vCPU，QEMU `-accel tcg` + `e1000`，`irq=11 eth0`）。工具见
[`LanGraph/script/ftrace2perfetto/`](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)。

## 状态轨：时段 vs 事件

| 状态 | 表示 | 全量数量 |
|---|---|---:|
| RUNNING / R 可运行 / I idle | slice 时段 | 79,665 段 |
| S 可中断睡眠 | instant 事件点 | 20,207 |
| Z 僵尸 | instant 事件点 | 39 |
| X 退出中 | instant 事件点 | 31 |
| D 不可中断睡眠 | instant 事件点 | 5 |

事件点 args 带 `comm` / `tid` / `state` / `raw` 及堆栈字段；例如 `iperf3:378` 的 S 睡眠点：
`stack_start=__traceiter_sched_switch+0x47/0x70`、`stack_end=entry_SYSCALL_64_after_hwframe`。
`--show-sleep-state` 可把这些态改回时段 slice。

## 「起始堆栈 / 末尾堆栈」的定义

ftrace 每个 tracepoint 后面跟的是一个**完整内核调用栈**（栈顶 → 栈底），不是两个栈。所以：

- `stack_start` = 栈顶第一帧 = **事件触发的直接调用点**
- `stack_end` = 栈底最后一帧 = **调用链入口**
- `stack_depth` = 内核栈帧数

| tracepoint | 次数 | 起始帧 `stack_start` | 末尾帧 `stack_end` |
|---|---:|---|---|
| `sched_stat_runtime` | 97,008 | `update_curr` | `entry_SYSCALL_64_after_hwframe` |
| `sched_switch` | 39,696 | `__traceiter_sched_switch` | `entry_SYSCALL_64_after_hwframe` |
| `softirq_raise` | 27,224 | `__raise_softirq_irqoff` | `secondary_startup_64_no_verify` |
| `softirq_entry` / `exit` | 26,987 | `__do_softirq` | `secondary_startup_64_no_verify` |
| `sched_waking` | 20,483 | `try_to_wake_up` | `secondary_startup_64_no_verify` |
| `irq_handler_entry` / `exit` | 18,531 | `__handle_irq_event_percpu` | `secondary_startup_64_no_verify` |
| `napi_poll` | 18,522 | `__napi_poll` | `secondary_startup_64_no_verify` |

规模：399 MB / 797 万行中 **315,293 个事件行，100% 带内核栈**，99.8% 还带用户栈，
共约 580 万内核栈帧 + 122 万用户栈帧。归属必须**按 CPU**：按「全局最近事件行」归属会串轨
（曾让 `update_curr` 混进 `irq_handler_entry`、14% 事件栈为空）；按 `<stack trace>` 标记行
自带的 CPU 归属后，无栈事件降到 0.34%、孤立帧 0。

## 文件（压缩与自解压）

**Perfetto UI（ui.perfetto.dev）和 `trace_processor_shell` 都能直接加载 `.gz`，自动解压，无需手动 gunzip。**

| 文件 | 大小 | 说明 |
|---|---:|---|
| `iperf-hardirq-stack-chain.pftrace` / `.json` | 48 KB / 63 KB | 3 ms 单条完整因果链，原件直传 |
| `iperf-hardirq-stack-chain.pftrace.gz` / `.json.gz` | 6 KB / 5 KB | 单链压缩版 |
| `iperf-hardirq-stack-full.pftrace.gz` | 4.7 MB | 26 s 全量，原件 88.8 MB（压到 5.3%） |
| `iperf-hardirq-stack-full.json.gz` | 3.9 MB | 全量 Chrome JSON，原件 106.6 MB（压到 3.7%） |
| `iperf-hardirq-stack-*.stats.json` | 4 KB | 转换器统计（含 `wait_states_as_event` 参数） |
| `tracepoint-stacks.jsonl.gz` | 2.2 MB | 315,293 条逐条事件索引，`--stacks` 的输入 |
| `tracepoint-stacks.json` | 63 KB | 按 tracepoint 聚合的起始/末尾帧分布 |
| `verify-stack.json` | 1.7 KB | `trace_processor_shell v59.1` 官方校验结果 |

全量只保留 `.gz`（省 95% 体积）；要得到原件：`gunzip -k iperf-hardirq-stack-full.pftrace.gz`。
自解压已验证：`trace_processor_shell` 直接读 `.pftrace.gz` 的状态事件分布与原件完全一致
（S 20,207 / Z 39 / X 31 / D 5）。

## 怎么看

打开 `iperf-hardirq-stack-chain.pftrace`（或 `.gz`），**点选 slice / instant**，详情面板 Args 里：

- `stack_start` / `stack_end` / `stack_depth` — 调用栈起始帧、末尾帧、帧数
- 事件点还有 `comm` / `tid` / `state`（S/D/Z/X）

SQL 查询时 pftrace 的键名带前缀 `debug.`。全量 `.json.gz` 解压后 367,951 行中
367,798 行带 `stack_start`（99.96%），未带的是 metadata 与引导 slice。

## 生成命令

```bash
# 1) 从 ftrace.log 生成逐条堆栈索引
python3 tracepoint_stacks.py langraph/ftrace.log --events-out tracepoint-stacks.jsonl \
  --json tracepoint-stacks.json
gzip -9 -k tracepoint-stacks.jsonl

# 2) 转换（等待/终止态默认事件化；--show-sleep-state 改回时段）
SRC=langraph/ftrace.log
python3 ftrace2perfetto.py "$SRC" -o iperf-hardirq-stack-chain.json \
  --format both --start 14.5600 --end 14.5630 --stacks tracepoint-stacks.jsonl.gz
python3 ftrace2perfetto.py "$SRC" -o iperf-hardirq-stack-full.json \
  --format both --stacks tracepoint-stacks.jsonl.gz
python3 verify.py iperf-hardirq-stack-chain.pftrace iperf-hardirq-stack-chain.json \
                  iperf-hardirq-stack-full.pftrace iperf-hardirq-stack-full.json \
                  --json-out verify-stack.json

# 3) 压缩（gzip -9，保留原件；.gz 可被 Perfetto 直接加载）
gzip -9 -k -f iperf-hardirq-stack-{chain,full}.pftrace
gzip -9 -k -f iperf-hardirq-stack-{chain,full}.json
```

## 校验（trace_processor_shell v59.1）

| 文件 | slice | flow | track | args | health |
|---|---:|---:|---:|---:|---|
| `chain.pftrace` | 193 | 46 | 23 | 1,072 | 无告警 |
| `chain.json` | 129 | 42 | 20 | 670 | 无告警 |
| `full.pftrace` | 367,845 | 96,064 | 50 | 248,937 | 无告警 |
| `full.json` | 231,425 | 96,061 | 87 | 221,379 | 无告警 |

四份全部零 health 告警。

## 已知限制

- 栈里含 ftrace 采集器自身的帧（`stack_trace_save_user` / `trace_event_buffer_commit` 等）；
  用户栈 98% 未符号化（Guest 无符号表），显示成 `<0x...>`。
- `.pftrace` 的 flow 端点仍是 `dur=0` 的 instant，肉眼确认箭头优先用 `.json`。
- TCG 是软件模拟，时间戳/延迟/带宽没有真实性能意义，只能验证链路与代码路径。
