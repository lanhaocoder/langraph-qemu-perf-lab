# Guest 内核符号表（6.8.0-31-generic）

## 为什么必须单独保存

`perf record` 写进 `perf.data` 的只是**采样地址**；`perf script` 输出里那些 `tcp_rcv_established`、`e1000_clean` 符号名，是解析时现场读 Guest 的 `/proc/kallsyms` 翻译出来的。Guest 一关机，运行时符号表就没了，之后想重新解析同一份 `perf.data`、做栈展开或 `addr2line`，内核栈会退化成一串裸地址。

`System.map` 只能补静态内核符号，**不含已加载模块**。本实验的收包热点 `e1000_clean` / `e1000_clean_rx_irq` / `e1000_intr` 全在 `e1000.ko` 里，System.map 里一个都没有（实测 `grep -c e1000_clean` = 0）。所以运行时的 kallsyms 不可省。

## 文件清单

| 文件 | 行数 | 说明 |
|---|---|---|
| `kallsyms-6.8.0-31-generic.txt.gz` | 203,527 | Guest 运行时全量符号：静态内核 + **688 个 e1000 模块符号**（带 `[e1000]` 标记） |
| `System.map-6.8.0-31-generic.txt.gz` | 202,841 | 内核包自带静态符号表，链接地址，不含模块 |
| `modules.txt` | 1 | 已加载模块及基址：`e1000 180224 0 - Live 0xffffffffc0201000` |
| `version.txt` | 1 | 内核构建信息（gcc 13.2.0 / binutils 2.42 / PREEMPT_DYNAMIC） |
| `capture-kallsyms.sh` | — | 可复现抓取脚本（QEMU TCG + `nokaslr` + nc 回传） |

两个 `.gz` 都是 `gzip -9` 无损压缩，合计约 2.9 MB，已过 `gzip -t`，解压行数与原始一致。

## 抓取要点

`/proc/kallsyms` 只包含**当前已加载**模块的符号，所以 `capture-kallsyms.sh` 里的顺序是：`insmod e1000.ko` → `echo 0 > /proc/sys/kernel/kptr_restrict` → 再 `cp /proc/kallsyms`。先加载模块、放开地址可见性，才能拿到完整符号和真实地址。

抓取时 QEMU `-append` 加了 **`nokaslr`**，内核基址固定，已验证与 System.map 逐项一致：

| 符号 | kallsyms | System.map |
|---|---|---|
| `tcp_rcv_established` | `ffffffff81fe3220` | `ffffffff81fe3220` |
| `tcp_write_xmit` | `ffffffff81feae60` | `ffffffff81feae60` |
| `__napi_poll` | `ffffffff81efc920` | `ffffffff81efc920` |
| `netif_receive_skb_list_internal` | `ffffffff81efbe60` | `ffffffff81efbe60` |

模块符号在 `ffffffffc02xxxxx` 段（如 `e1000_clean_rx_irq` = `ffffffffc0203450`），只有运行时 kallsyms 里有。

## ⚠️ 与已有实测数据的对应关系

`run1/` / `run2/` 那两轮 perf/ftrace 是在**默认 KASLR** 下采的，内核基址带随机偏移，与本表的固定地址**不对应**：

- `perf.log` 是当时在 Guest 内完成符号化后的**文本**，符号名齐全，不受影响，可正常读；
- 本仓库未入库 `perf.data`（原始采样数据），所以没有"拿旧数据配新符号表"的场景；
- 需要严格对应（地址换算、重新符号化）时，用修好的 `iperf-perf/guest-init.sh` + 启动参数加 `nokaslr` 重跑一轮，符号表会随采集日志一起回传。

另外本仓库只有压缩的 `vmlinuz`，**没有 `vmlinux`**，因此无法做地址 → 源码行号的 `addr2line`。需要 `linux-image-6.8.0-31-generic-dbgsym` 调试符号包，或用内核源码树里的 `scripts/extract-vmlinux` 从 vmlinuz 提取。

## 用法

```bash
# 不解压直接查符号
gzip -dc kallsyms-6.8.0-31-generic.txt.gz | grep '\[e1000\]' | head
gzip -dc kallsyms-6.8.0-31-generic.txt.gz | grep -E ' (tcp_rcv_established|e1000_clean)$'

# 用指定符号表重新解析 perf.data
gzip -dc kallsyms-6.8.0-31-generic.txt.gz > /tmp/kallsyms.txt
perf script --kallsyms /tmp/kallsyms.txt -i perf.data > perf.log

# 模块内偏移 = 采样地址 - 模块基址（基址见 modules.txt）
# 例：ffffffffc0203450 - ffffffffc0201000 = 0x2450 → e1000_clean_rx_irq+0x0
```

## 以后不用手动抓

`iperf-perf/guest-init.sh` 已在采集前导出 `kallsyms.txt` / `modules.txt` / `version.txt`，并放进 `result.tar` 一起回传；宿主机 `run.sh` 用的是 `tar xf result.tar -C guest`，新文件会自动解出，无需改宿主机侧。想复现本目录内容，直接跑 `./capture-kallsyms.sh`（依赖 `/tmp/vmlinuz`、`/tmp/e1000.ko`、`busybox` 静态版）。
