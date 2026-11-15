# iperf 完整生命周期 · Perfetto 调度 trace

由 `ftrace2perfetto.py`（工具本体见 [LanGraph/script/ftrace2perfetto](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)）
把 QEMU Guest 内重抓的 `ftrace.log`（20 线程双向 TCP iperf，26 s，4 CPU，180,083 条 tracepoint）转换而成，
可直接在 <https://ui.perfetto.dev> 打开。

## 文件

| 文件 | 窗口 | 说明 |
|---|---|---|
| `iperf-lifecycle.pftrace` / `.json` | 全量 11.068 ~ 37.075 s | **主交付**：完整采集窗口，含全部 iperf 生死、尾部余量 |
| `iperf-focus.pftrace` / `.json` | 13.0 ~ 29.0 s + `--focus iperf3` | 只显示 iperf3 的 42 个 tid，其余留白 |
| `iperf-zoom.pftrace` / `.json` | 16.9045 ~ 16.9080 s（3.5 ms） | 秒开的窄窗口，含「软中断→唤醒→入队→上CPU运行」链 |
| `iperf-chain.pftrace` / `.json` | 28.5698 ~ 28.5722 s（2.4 ms） | **看因果链首选**：只框住一条真实完整 `iperf3` 链（4 节点、261 µs、`wake2run_us=109`） |
| `causal-chain-result.html` | — | **结果页**：真实链节点表 + SVG 链图 + 泳道图 + 校验表 + 查看指引（内嵌图片，离线可开） |
| `chain-preview.png` / `overview.png` | — | 泳道预览（红箭头 = 因果/唤醒 flow） |
| `ui-evidence/*.png` | — | 真实 Perfetto UI 截图：默认视图无箭头 / 展开轨道 / 放大后 instant 三角 / JSON 版对照 |
| `iperf-chain.stats.json` | — | chain 窗口的转换统计 |
| `verify.json` | — | 官方 `trace_processor_shell` 校验结果（零 health 告警） |

两种格式共用同一套事件模型：`.pftrace` 是 Perfetto 原生 protobuf（推荐，加载快、SQL 全）；
`.json` 是 Chrome Trace Event（便于文本检查，flow 端点落在有宽度的 slice 上，UI 里更好点选）。

## 三项修正（相对首版）

1. **idle 线程按 CPU 区分**：每个 CPU 的 idle 用 `swapper/N:0` 独立建轨，避免 `tid 0` 在各 CPU 间错乱。
2. **线程名可见**：状态轨 / 生命周期轨 / CPU 运行 slice 都带 comm（如 `iperf3 状态`、`iperf3:379`）。
3. **中断/软中断→唤醒→上 CPU 因果链连上**：一条跨轨 flow，从 IRQ 泳道指向被唤醒任务上 CPU 的切片；
   非中断触发的自发唤醒走独立的 `wakeup → run` 流。

## 为什么在 Perfetto UI 里「看不到因果链」

**结论：数据没丢，是「UI 交互机制 + 端点事件形态 + 全量尺度」三者叠加。**

数据核查（`trace_processor_shell v59.1` 对 `iperf-lifecycle.pftrace`）：

| 项 | 值 |
|---|---|
| `flow` 表 | **43,954** 条，配对 id 正常 |
| 跨轨 flow（两端 `track_id` 不同） | **4,641** 条 |
| 链节点 | 软中断 raise 15,746 / `sched_wakeup` 15,011 / `sched_waking` 13,108 / 唤醒(中断) 1,904 / 上CPU运行 1,823 / 硬中断 74 |
| health | 零告警 |

原因分层：

1. **Perfetto UI 机制（客观）**：官方文档原话 *"The link is displayed as an arrow in the UI, when one of the events is selected."*
   4.4 万条箭头不会默认全画，**必须点选某个 flow 端点事件**才显示相连箭头。全量 26 s 视图下看不到是必然的。
2. **pftrace 端点形态（当前实现的已知限制）**：43,954 条 flow **两端 100% 是 `dur=0` 的 instant**，
   UI 里渲染成 1px 紫色三角 ▲；`.json` 版有 34,548 条端点落在有宽度的 slice 上，容易点中。
   两种格式不对称，是待修项（计划把 pftrace 端点改为可点中的 slice）。
3. **尺度**：26 s / 20 万 slice，即便端点正常也需放大到 ms 级才看得清。

### 想看到箭头，按这 4 步

1. 打开 **`iperf-chain.pftrace`**（2.4 ms 专用窗口）或 `.json`，别用 26 s 全量文件；
2. 展开「CPU 运行泳道」「IRQ/softirq 轨」；
3. 滚轮放大到刻度 `00:000 500 000` 量级（0.5~2 ms 视野）；
4. **点选紫色三角 ▲（instant 端点）或有宽度的 slice** → 相连的 flow 箭头才画出，详情面板出现 Flow 字段。

最省事：直接打开 `causal-chain-result.html`，① 节已把真实链画成 SVG，不依赖 UI 交互。

### 原始数据限制（非转换问题）

`irq_handler_entry` 原始日志**仅 74 条**，`softirq_raise` 15,746 条 —— QEMU/virtio 下硬中断 tracepoint
基本没抓到，因此因果链绝大多数**从软中断（NET_RX / TASKLET / RCU）起点**，硬中断段在这份 ftrace 里
几乎不可见。要补齐需确认 `get_ftrace.sh` 已 enable `irq/irq_handler_entry` 后重抓。

## 校验（trace_processor_shell v59.1）

| 文件 | slice | flow | track | health |
|---|---:|---:|---:|---|
| `iperf-lifecycle.pftrace` | 203,682 | 43,954 | 107 | 无告警 |
| `iperf-lifecycle.json` | 132,943 | 43,951 | 162 | 无告警 |
| `iperf-chain.pftrace` | 82 | 14 | 23 | 无告警 |
| `iperf-chain.json` | 57 | 10 | 21 | 无告警 |

全量统计：15,012 次 `sched_waking` 中 13,123 次（87.4%）匹配到 `sched_switch` 并算出唤醒延迟
（p50 46 µs / p99 2.44 ms）；59 个 tid 的 `fork → free` 闭合。

## 打开方式

浏览器打开 <https://ui.perfetto.dev> → `Open trace file` → 选 `.pftrace` 或 `.json`。
纯前端 WASM，文件留在本机，无需上传。
</content>
