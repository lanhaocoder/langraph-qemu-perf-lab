# lockbench 压力源 trace

详见上级 `../README.md`。

- `lock2-chain.pftrace` / `.json`（+ `.gz`）：13 ms 窗口，7 组配对成功的等锁 slice + 唤醒链
- `lock2-full.pftrace.gz` / `.json.gz`：24 s 全量（原 165 MB / 200 MB，gzip 后 11.2 MB / 9.3 MB）

`.gz` 可直接拖进 ui.perfetto.dev（自动解压）。点选 slice 看 Args 里的
`stack_start` / `stack_end` / `stack_depth` / `wait_us` / `flags` / `tid`。

主线程等待锁的 slice 名为 `等锁：lockbench`，挂在「锁 / 同步事件轨」按锁地址分的子轨上；
`lock 竞争开始` / `竞争结束` 是 instant 事件点。唤醒链（sched_waking → wakeup → switch）
的 flow 箭头需点选一端才显示。

校验见 `../verify.json`：两份 pftrace 零 health 告警；两份 json 各 1 条
`slice_spill_overlapping_complete_event`（多等待者同锁重叠，JSON 格式限制，数据不丢）。
