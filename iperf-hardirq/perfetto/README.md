# 带完整硬中断因果链的 Perfetto trace（实验 5）

由 `../iperf-hardirq.tar.gz` 解出的 `langraph/ftrace.log`（低速小包双向 TCP iperf，
26 s，4 vCPU，QEMU `-accel tcg` + `e1000`，`irq=11 eth0`）转换而成，
工具为 [`LanGraph/script/ftrace2perfetto/`](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)。

与实验 4 的区别：**这次抓到了硬中断**。上一轮 `-P 20` 持续大流量下 e1000 的
NAPI 中断被持续屏蔽，稳定打流 10 s 内 eth0 硬中断为 0，因果链只能从 NET_RX 软中断起。
本轮改成 `-P 2 -b 1M -l 256`（2.0 Mbps），包间隔 ~ms 级，每轮 `napi_poll` 都能排空
RX 队列并重开中断，于是得到完整链：

```
irq_handler_entry(irq=11 eth0)   ← 硬中断（PCI INTA#）
  → softirq_raise(vec=3 NET_RX)  ← ISR 里 __napi_schedule，只置位
  → napi_poll(device eth0)       ← work=0 < budget=64，队列排空、重开中断
  → sched_waking(iperf3)         ← 协议栈唤醒用户态
  → sched_wakeup(iperf3)
  → sched_switch(next=iperf3)    ← 上 CPU 运行
```

工具把上面整条链做成**一条跨轨 flow**：IRQ 轨起点/步进 → 唤醒 → CPU 运行轨终点，
终点 slice 带 `wake2run_us` 参数。

## 文件

| 文件 | 说明 |
|---|---|
| `iperf-hardirq-chain.pftrace` / `.json` | **3 ms 专用窗口**，单条完整因果链，建议先看这个 |
| `iperf-hardirq-full.pftrace` / `.json` | 26 s 全量（约 36.8 万 slice / 9.6 万 flow） |
| `iperf-hardirq-*.stats.json` | 转换器统计 |
| `hardirq-chain-preview.png` | 泳道反绘图（红箭头 = 唤醒 flow） |
| `hardirq-result.html` | **自包含结果页**：真实原始链 + 泳道图 + 结论，不依赖 Perfetto UI |
| `verify-hardirq.json` | `trace_processor_shell v59.1` 官方校验结果 |

`.pftrace` 用于 ui.perfetto.dev，`.json` 是 Chrome Trace Event 格式（也可直接拖进 Perfetto UI）。

## 生成命令

```bash
SRC=langraph/ftrace.log     # 从 ../iperf-hardirq.tar.gz 解出
python3 ftrace2perfetto.py "$SRC" -o iperf-hardirq-chain.json \
  --format both --start 14.5600 --end 14.5630
python3 ftrace2perfetto.py "$SRC" -o iperf-hardirq-full.json --format both
python3 verify.py iperf-hardirq-chain.pftrace iperf-hardirq-chain.json \
                  iperf-hardirq-full.pftrace iperf-hardirq-full.json \
                  --json-out verify-hardirq.json
python3 preview.py iperf-hardirq-chain.json -o hardirq-chain-preview.png
```

## 在 Perfetto UI 里看箭头

Perfetto UI **默认不画 flow 箭头**，必须点选事件：

1. 打开 `iperf-hardirq-chain.pftrace`（别用全量文件，视野太密）；
2. 展开「CPU 运行泳道」和「IRQ / softirq 轨」；
3. 滚轮放大到 0.5~2 ms 视野；
4. **点选紫色三角 ▲（instant 端点）或有宽度的 slice** → 相连的 flow 箭头才画出，
   详情面板出现 Flow 字段。

最省事：直接打开 `hardirq-result.html`，已把真实链画成 SVG，不依赖 UI 交互。

## 校验（trace_processor_shell v59.1）

| 文件 | slice | flow | track | args | health |
|---|---:|---:|---:|---:|---|
| `iperf-hardirq-chain.pftrace` | 193 | 46 | 23 | 559 | 无告警 |
| `iperf-hardirq-chain.json` | 129 | 42 | 20 | 348 | 无告警 |
| `iperf-hardirq-full.pftrace` | 367,845 | 96,064 | 67 | 156,605 | 无告警 |
| `iperf-hardirq-full.json` | 231,425 | 96,061 | 87 | 147,269 | 无告警 |

全量：20,288/20,483 次 `sched_waking` 匹配到 `sched_switch`，23 个 tid 的
`fork → free` 闭合；`wake2run_us` 均值 415 µs（pftrace）。四份文件均零 health 告警。

原始数据证据（`../hardirq-analysis.json`）：`irq=11 eth0` 18,522 次、
NET_RX raise 15,184 次、`napi_poll` 18,522 次（与硬中断 1:1）、
`/proc/interrupts` IRQ11 增量 +18,698、完整因果链候选 **17,586 条**。

## 已知限制

- **`.pftrace` 的 flow 端点全部是 `dur=0` 的 instant**，Perfetto UI 对 instant 端点的
  箭头渲染不如落在有宽度 slice 上稳妥；`.json` 版本的端点多数落在 slice 内，
  因此**需要肉眼确认箭头时优先用 `.json`**，或直接看 `hardirq-result.html`。
  把 pftrace 端点改成带 `dur` 的 slice 是后续待修项，不影响本次数据完整性。
- TCG 是软件模拟，时间戳/延迟/带宽**没有真实性能意义**，只能验证链路与代码路径，
  不能用来分析真实延迟或性能回归。

## 实验 6：带调用栈标注的版本 → [`stacks/`](stacks/README.md)

同一份 `ftrace.log` 又生成了一套**带堆栈**的 trace，放在 `stacks/`：
每个 tracepoint 的 slice / instant / flow 的 args 里带 `stack_start` / `stack_end` /
`stack_depth`（栈顶触发点、栈底入口、帧数），并**隐藏 S 可中断睡眠段**。
单链原件直传，全量以 gzip 压缩（97 MB→3.6 MB），另附 315,293 条事件的逐条堆栈索引。
四份同样通过官方校验、零告警。该目录只含 Perfetto 文件与数据索引，无 HTML/PNG。
