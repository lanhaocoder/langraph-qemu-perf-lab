# 低速小包重抓：带完整硬中断因果链的 iperf 生命周期

## 为什么要重抓

上一轮 `iperf-lifecycle/`（-P 20 持续大流量、520 Mbps）里，稳定打流的 17~27 s
**eth0 硬中断整整 10 秒为 0**，因果链只能从 NET_RX 软中断起点，看不到
「硬中断 → 软中断 → 用户态唤醒 → 上 CPU」的完整链。

根因不是 tracepoint 没开（`irq/irq_handler_entry` 一直是 enable 的），而是
**e1000 驱动的 NAPI 中断屏蔽**：`e1000_intr()` 写 IMC 关中断 →
`__napi_schedule()` → NET_RX softirq 里 `e1000_clean()` 批量收包；
只要 RX 队列不排空，就一直停在 polling 状态，不 `napi_complete()`、不写 IMS
重开中断。持续大流量下队列几乎不空，于是硬中断被持续屏蔽。

## 本轮做法

把流量从「持续大并发」改成「低速小包」，让每轮 NAPI poll 后队列都能排空：

| 参数 | 上一轮 | 本轮 |
|---|---|---|
| 并发 / 时长 | `-P 20 -t 10` | `-P 2 -t 12` |
| 限速 | 无限速 | `-b 1M`（每流，合计 2.0 Mbps） |
| 包长 | 默认 | `-l 256`（小包） |
| 实测吞吐 | 520 Mbps / 10 s 651 MB | **2.0 Mbps / 12 s 3.0 MB** |
| ftrace 事件 | `sched\|irq` | `sched\|irq\|napi`（新增 `napi_poll`） |
| 额外证据 | — | `/proc/interrupts`、`/proc/net/dev` 前后快照 |

包间隔被拉到 ~ms 级，远大于 NAPI poll 的百 µs 级耗时。其余时序
（READY 信号、iperf 自然结束、不 kill、perf+ftrace 同时抓）与上一轮完全一致。

环境：QEMU `-accel tcg` 4 vCPU / 3 GB，`-device e1000` + `-netdev user`(SLIRP)，
Guest 内核 `6.8.0-31-generic`，中断类型 `irq=11 eth0`（PCI INTA# legacy INTx）。

## 决定性结果

| 指标 | 上一轮 | 本轮 |
|---|---:|---:|
| `irq_handler_entry: irq=11 eth0` | 66 | **18,522** |
| `softirq_raise vec=3 NET_RX` | 5,578 | 15,184 |
| `napi_poll` 事件 | 未抓 | **18,522**（与硬中断 1:1） |
| `sched_waking` 唤醒 iperf3 | — | 38,944 |
| `sched_switch` 切到 iperf3 | — | 19,598 |
| 硬中断 : NET_RX | 1 : 85 | **1.2 : 1** |
| `/proc/interrupts` IRQ11 增量 | — | **+18,698**（3 → 18,701） |
| 完整因果链候选 | 0 | **17,586 条** |

> 注：`grep -aoE 'napi_poll'` 会数出 59,524，那是把 `stacktrace=1` 抓到的
> 内核栈帧里的 `napi_poll` 符号也算进去了；按事件行解析的 18,522 才是真实事件数。

按秒分布（稳定打流段 13~24 s，每秒都有上千次硬中断，上一轮为 0）：

```
sec   irq11  netrx  napi  wake_iperf3  switch_iperf3
 13    1136    858  1136        1914           958
 14    1520   1344  1520        2650          1329
 15    1480   1171  1480        3056          1539
 ...    ...    ...   ...         ...           ...
 23    1531   1270  1531        3190          1605
 24    1512   1246  1513        3176          1599
```

## 一条真实完整因果链（t0 = 14.561145 s，5/5 环节）

```
+  0 µs  irq_handler_entry: irq=11 name=eth0      ← 硬中断（PCI INTA#）
+ 10 µs  softirq_raise: vec=3 [action=NET_RX]     ← ISR 里 __napi_schedule，只置位
+ 48 µs  napi_poll: ... device eth0 work 0 budget 64
                                                   ← work=0：队列排空，重开中断
+251 µs  sched_waking: comm=iperf3 pid=380 target_cpu=003
+278 µs  sched_wakeup: comm=iperf3 pid=380 target_cpu=003
+391 µs  sched_switch: prev_comm=swapper/3 ... ==> next_comm=iperf3 next_pid=380
```

`napi_poll` 的 `work 0 < budget 64` 是「RX 队列排空 → `napi_complete()` →
写 IMS 重开中断」的直接证据，也解释了为什么下一个包能再次触发硬中断。

## 目录内容

- `run.sh` / `guest-init.sh` — 本轮抓包脚本（复现：`bash run.sh`，约 5 分半）
- `langraph/` — Guest 回传的原始采集：
  `ftrace.log`(399 MB / 797 万行，含栈帧)、`perf.log`、`perf.data`、
  `kallsyms.txt`、`ftrace_format.log`、`timeline.txt`、
  `interrupts.{before,after}.txt`、`netdev.{before,after}.txt`、
  `guest_client.json`、`guest_server.log`、`capture.log`
- `iperf-hardirq.tar.gz` — 上述 `langraph/` 的 pigz-9 压缩归档（21 MB）
- `host_client.json` / `host_server.log` / `host_timeline.txt` / `qemu.log` — Host 侧记录
- `analyze_hardirq.py` — 统计脚本（硬中断/NET_RX/napi_poll 分布、完整链抽取）
- `hardirq-analysis.json` / `analyze.log` — 分析结果与日志

生成的 Perfetto trace 与展示页在本目录 [`perfetto/`](perfetto/)：
`iperf-hardirq-chain.*`（3 ms 单条完整链）、`iperf-hardirq-full.*`（26 s 全量）、
`hardirq-result.html`（自包含结果页）、`hardirq-chain-preview.png`（泳道反绘）、
`verify-hardirq.json`（官方校验结果）。

## 与上一轮结论的关系

上一轮报告中「需在 `get_ftrace.sh` 里确认 enable `irq/irq_handler_entry`
后重抓补齐硬中断」这句是**错的**——tracepoint 本来就开着。
正确解释是 NAPI 屏蔽，已通过本轮低速重抓验证。
`iperf-lifecycle/perfetto/README.md` 与工具 README 中相关说明已同步更正。
