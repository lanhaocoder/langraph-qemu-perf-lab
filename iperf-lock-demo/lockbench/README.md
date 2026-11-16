# 实验 7b：lockbench 压力源 —— 密集锁竞争事件关联 demo

本目录是实验 7（`../` 下「自然竞争版」）的**加密版重抓**。上一轮低速 iperf 场景
全程只有 **2 组** `lock:contention_begin/end`，不足以演示「锁竞争 → 唤醒 → 上 CPU」的
事件关联。本轮在 ftrace 采集窗口内同时跑一个多线程压力源 `lockbench`，把竞争放大到
**2,822 组**。

## 为什么要制造压力

`lock:contention_begin/end` 只在**内核锁慢路径真正发生竞争**时触发。iperf 低速小包
几乎不争内核锁，所以自然样本稀疏。`lockbench.c` 用 16 线程高频执行三类操作主动制造竞争：

| worker | 操作 | 触发的锁 / 事件 |
|---|---|---|
| A | `mmap` 大块 + `memset` + `munmap` | `mm->mmap_lock`（rwsem 读写慢路径）→ `lock:contention_begin/end`，起始帧 `rwsem_down_{read,write}_slowpath` |
| B | `pthread_mutex` 争抢共享计数器 | 用户态锁竞争 → `futex_wait/wake` → `sched_waking/wakeup/switch` |
| C | 多线程对同一文件 `write`/`ftruncate` | `inode->i_rwsem` 写锁竞争 |

加压前先用字节码探针实测：5 秒即触发 5,693 组 contention + 26,540 次 sched_waking，
确认有效后才做完整重抓。

## 实测结果（Guest 6.8.0-31-generic，x86_64 TCG，4 vCPU）

| 事件 | 本轮 | 上一轮（自然） |
|---|---:|---:|
| `contention_begin` / `contention_end` | **2,822 / 2,817** | 2 / 2 |
| `sched_waking` / `sched_wakeup` | 20,646 / 20,645 | — |
| `sched_switch` | 32,187 | — |
| `rcu_utilization` | 206,971 | 166,086 |
| `timer_expire_entry` | 72,229 | — |

竞争起始帧 100% 是同步原语慢路径：`rwsem_down_write_slowpath` / `rwsem_down_read_slowpath`。
竞争从 14.496 s 持续到 25.189 s，几乎覆盖整个打流窗口。

一条真实等锁 slice（13 ms 展示窗口内）：

```
等锁：lockbench  wait_us=3348  flags=WRITE  tid=393
  stack_start = rwsem_down_write_slowpath+0x398/0x550
  stack_end   = entry_SYSCALL_64_after_hwframe
```

## trace 产物（`perfetto/`）

| 文件 | 原件 → 压缩 | 内容 |
|---|---|---|
| `lock2-chain.pftrace` / `.json`（+ `.gz`） | 297 KB / 372 KB → 26 KB / 22 KB | **先看这个**：13 ms 窗口（14.806~14.819 s），7 组配对成功的等锁 slice + 唤醒链 + rcu/timer |
| `lock2-full.pftrace.gz` | 165 MB → 11.2 MB | 24 s 全量，698,042 事件 / 272 track / 5,644 条 lock 事件 |
| `lock2-full.json.gz` | 200 MB → 9.3 MB | 全量 Chrome JSON |

`.gz` 可被 Perfetto UI 与 `trace_processor_shell` **直接读取自动解压**（已实测内容一致）。
全量文件先 `gunzip -k` 或直接拖 `.gz` 进 ui.perfetto.dev。

## 每个 tracepoint 的调用栈

由 tracepoint 产生的 slice / instant / flow，args 带 `stack_start` / `stack_end` /
`stack_depth`（SQL 里键名带 `debug.` 前缀）。逐条索引见 `tracepoint-stacks.jsonl.gz`
（854,511 条，供 `ftrace2perfetto.py --stacks` 复现）。

## 校验与已知限制

- 两份 **`.pftrace` 全部通过 `trace_processor_shell v59.1`，零 health 告警**。
- 两份 `.json` 有一条 `slice_spill_overlapping_complete_event`（chain 7 条、full 1,384 条）：
  同一把 mmap_lock 上多个线程的等锁时段重叠，落在同一条锁轨，Chrome JSON 无法表达
  重叠的 X slice，Perfetto 自动挪到 overflow track，**数据不丢，仅显示布局可能不同**。
  `.pftrace` 无此问题，故主交付用 pftrace。这是 JSON 格式表达力限制，非数据错误。
- **TCG 软件模拟**：延迟（wake2run）、带宽无真实性能意义，本产物仅作事件关联 demo。
- **架构/版本**：Guest 是 x86_64 6.8 内核，与目标 v6.18 arm64 不同；但同步原语
  （rwsem/mutex/futex）代码路径一致，满足 demo 要求。
- **回传缺陷**：本轮 nc 回传的 tar 被截断，只完整解出 `ftrace.log`（977 MB），
  `timeline.txt`/`kallsyms.txt`/`lockbench.log` 等辅助小文件丢失。不影响 trace 转换
  （ftrace.log 自带事件与堆栈），但这些佐证文件缺失，特此说明。

## 复现

```bash
# 在仓库内（initramfs 需 host 侧 gcc 静态编译 lockbench.c 后放入 /tmp/guest/）
gcc -O2 -static -pthread -o /tmp/guest/lockbench lockbench.c
cd .. && bash run.sh                      # 起 QEMU，ftrace + iperf + lockbench 同抓
# 转换（在 ftrace2perfetto 工具目录）
python3.11 ftrace2perfetto.py <ftrace.log> -o lock2-chain.json --format both \
  --start 14.8060 --end 14.8190 --stacks <tracepoint-stacks.jsonl.gz>
```
