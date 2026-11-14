# iperf 完整生命周期 · Perfetto 调度 trace

由 `ftrace2perfetto.py`（工具本体见 [LanGraph/script/ftrace2perfetto](https://github.com/lanhaocoder/LanGraph/tree/main/script/ftrace2perfetto)）
把 QEMU Guest 内重抓的 `ftrace.log`（20 线程双向 TCP iperf，26 s，4 CPU）转换而成，
可直接在 <https://ui.perfetto.dev> 打开。

## 文件

| 文件 | 窗口 | 说明 |
|---|---|---|
| `iperf-lifecycle.pftrace` / `.json` | 全量 11.068 ~ 37.075 s | **主交付**：完整采集窗口，含全部 iperf 生死、尾部余量 |
| `iperf-focus.pftrace` / `.json` | 13.0 ~ 29.0 s + `--focus iperf3` | 只显示 iperf3 的 42 个 tid，其余留白 |
| `iperf-zoom.pftrace` / `.json` | 16.9045 ~ 16.9080 s（3.5 ms） | **秒开**：一条完整「硬中断→软中断→唤醒→上CPU运行」因果链 |
| `overview.png` | — | 从 `iperf-zoom.json` 反绘的泳道预览（红箭头=因果/唤醒 flow） |
| `verify.json` | — | 官方 `trace_processor_shell` 校验结果（零 health 告警） |

两种格式共用同一套事件模型：`.pftrace` 是 Perfetto 原生 protobuf（推荐，加载快、SQL 全）；
`.json` 是 Chrome Trace Event（便于文本检查）。

## 本轮三项修正（相对首版）

1. **idle 线程按 CPU 区分**：每个 CPU 的 idle 用 `swapper/N:0` 独立建轨，避免 `tid 0` 在各 CPU 间错乱。
2. **线程名可见**：状态轨 / 生命周期轨 / CPU 运行 slice 都带 comm（如 `iperf3 状态`、`iperf3:379`）。
3. **硬中断→软中断→用户态唤醒 因果链连上**：做成一条跨轨 flow，从 IRQ 泳道直接指向被唤醒任务上 CPU 的切片；
   非中断触发的自发唤醒走独立的 `wakeup → run` 流。

## 打开方式

浏览器打开 <https://ui.perfetto.dev> → `Open trace file` → 选 `.pftrace` 或 `.json`。
纯前端 WASM，文件留在本机，无需上传。
