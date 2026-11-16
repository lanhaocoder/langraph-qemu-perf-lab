# 带调用栈标注的 Perfetto trace（实验 6）

在实验 5 的完整硬中断因果链基础上，把 `ftrace.log` 里每个 tracepoint 紧跟的
`<stack trace>` / `<user stack trace>` 解析出来，作为 args 挂到对应 slice / instant / flow 上，
同时**隐藏 S（可中断睡眠）状态段**。

数据源仍是 `../iperf-hardirq.tar.gz` 解出的 `langraph/ftrace.log`（低速小包双向 TCP iperf，
26 s，4 vCPU，QEMU `-accel tcg` + `e1000`，`irq=11 eth0`）。工具见
[`LanGraph/script/ftrace2perfetto/`](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)。

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
| `sched_process_fork` | 25 | `kernel_clone` | `entry_SYSCALL_64_after_hwframe` |
| `sched_process_exec` | 7 | `exec_binprm` | `entry_SYSCALL_64_after_hwframe` |

规模：399 MB / 797 万行中 **315,293 个事件行，100% 带内核栈**，99.8% 带用户栈，
共约 580 万内核栈帧 + 122 万用户栈帧。归属必须**按 CPU**：trace 文件按时间戳合并 4 个 CPU
的 buffer，按「全局最近事件行」归属会串轨（第一版曾让 `update_curr` 混进 `irq_handler_entry`、
14% 事件栈为空）；按 `<stack trace>` 标记行自带的 CPU 归属后，无栈事件降到 1,057 条（0.34%），
孤立帧 0。

## 文件

| 文件 | 说明 |
|---|---|
| `iperf-hardirq-stack-chain.pftrace` / `.json` | **3 ms 单条完整因果链，先看这个**（45 KB / 59 KB，原件直传） |
| `iperf-hardirq-stack-full.pftrace.gz` / `.json.gz` | 26 s 全量，**gzip -9 压缩**：81 MB→4.4 MB，97 MB→3.6 MB |
| `iperf-hardirq-stack-*.stats.json` | 转换器统计（含 `stacks` / `hide_sleep_state` 参数） |
| `tracepoint-stacks.jsonl.gz` | 逐条索引，315,293 条事件的 `start/end/start_sym/end_sym/depth`，2.2 MB；`--stacks` 的输入 |
| `tracepoint-stacks.json` | 按 tracepoint 聚合的起始/末尾帧分布 |
| `verify-stack.json` | `trace_processor_shell v59.1` 官方校验结果 |

全量文件先解压再用：

```bash
gunzip -k iperf-hardirq-stack-full.pftrace.gz   # 或 .json.gz
# 得到 .pftrace，拖进 https://ui.perfetto.dev
```

## 怎么看堆栈

在 Perfetto UI **点选一个 slice**，详情面板的 Args 里会出现：

- `stack_start` — 栈顶起始帧（如 `__handle_irq_event_percpu+0x131/0x1b0`）
- `stack_end` — 栈底末尾帧（如 `asm_common_interrupt+0x27/0x40`）
- `stack_depth` — 内核栈帧数

用 `trace_processor_shell` 查询时键名带前缀 `debug.`：

```sql
SELECT key, count(*) FROM args
WHERE key IN ('debug.stack_start','debug.stack_end','debug.stack_depth')
GROUP BY key;
```

覆盖率：全量 `.json` 347,746 行中 347,593 行带 `stack_start`（99.96%），未带的只是
track 命名 metadata 和收尾引导 slice；pftrace 里三个键各 27,277 个去重 arg_set。
`S 可中断睡眠` slice 精确查询为 **0 条**；`D 不可中断睡眠` 等其它状态保留。

## 生成命令

```bash
# 1) 先从 ftrace.log 生成逐条堆栈索引
python3 tracepoint_stacks.py langraph/ftrace.log \
  -o tracepoint-stacks.txt --json tracepoint-stacks.json \
  --events-out tracepoint-stacks.jsonl --samples 20 --topn 10
gzip -9 -k tracepoint-stacks.jsonl

# 2) 转换时挂上堆栈（S 段默认隐藏，--show-sleep-state 可恢复）
SRC=langraph/ftrace.log
python3 ftrace2perfetto.py "$SRC" -o iperf-hardirq-stack-chain.json \
  --format both --start 14.5600 --end 14.5630 \
  --stacks tracepoint-stacks.jsonl.gz
python3 ftrace2perfetto.py "$SRC" -o iperf-hardirq-stack-full.json \
  --format both --stacks tracepoint-stacks.jsonl.gz
python3 verify.py iperf-hardirq-stack-chain.pftrace iperf-hardirq-stack-chain.json \
                  iperf-hardirq-stack-full.pftrace iperf-hardirq-stack-full.json \
                  --json-out verify-stack.json
```

## 校验（trace_processor_shell v59.1）

| 文件 | slice | flow | track | args | health |
|---|---:|---:|---:|---:|---|
| `iperf-hardirq-stack-chain.pftrace` | 183 | 46 | 23 | 1,034 | 无告警 |
| `iperf-hardirq-stack-chain.json` | 119 | 42 | 20 | 635 | 无告警 |
| `iperf-hardirq-stack-full.pftrace` | 347,638 | 96,064 | 66 | 248,601 | 无告警 |
| `iperf-hardirq-stack-full.json` | 211,218 | 96,061 | 87 | 220,937 | 无告警 |

四份全部零 health 告警；单链 `wake2run_us` 均值 69 µs，全量 pftrace 均值 412.6 µs。

## 已知限制

- 栈里含 ftrace 采集器自身的帧：`stack_trace_save_user` / `ftrace_trace_userstack` /
  `trace_event_buffer_commit` / `trace_event_raw_event_*`，甚至嵌套上一次 tracepoint 的采集路径。
  栈均值 15~29 帧、最深 125 帧。
- 用户栈 98% 未符号化（Guest 内 busybox/perf.bin 无符号表），显示成 `<0x...>`；不是空串。
- `.pftrace` 的 flow 端点仍是 `dur=0` 的 instant，与实验 5 相同；肉眼确认箭头优先用 `.json`。
- TCG 是软件模拟，时间戳/延迟/带宽没有真实性能意义，只能验证链路与代码路径。
- 本目录只归档 Perfetto 文件与数据索引，不含 HTML/PNG 展示物。
