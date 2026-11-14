# `get_ftrace.sh` 原生产物归档：`langraph/`

## 为什么单独有这份

`get_ftrace.sh` 的默认输出是 **`output="langraph"`**（脚本第 5 行），运行时会在当前目录生成 `langraph/` 目录，里面放 `ftrace.log`、`ftrace_format.log`、`ftrace_symbol.log`、`perf.log`、`perf.data`；`-z 1` 还会自动打成 `langraph.tar.gz`。

之前几轮（实验 2、3）为了把日志单独回传，用了 `-o /langraph_out -z 0`，**改了输出目录名、关掉了脚本自带压缩、又把文件拆开单独打包**，所以仓库里一直只有散装的 `perf.log.gz` / `ftrace.log.gz`，没有这个脚本原生的 `langraph/` 产物。这一轮按原生语义重跑补齐。

## 采集配置

```bash
# Guest 内（工作目录 /work，脚本未做任何修改）
sh /get_ftrace.sh -t 20 -p 1 -z 0 -o langraph
```

| 项 | 值 |
|---|---|
| `-p 1` | **perf 与 ftrace 同时采集**（`capture.log` 里 `start perf capture` + `start ftrace capture` 均在） |
| `-o langraph` | 脚本默认输出目录名，产物为 `langraph/` |
| `-t 20` | 采集 20 秒 |
| `-z 0` | 不在 Guest 内压缩（TCG 下太慢），回传后在宿主机用 `pigz -9` 归档 |
| QEMU `-append` | 加 **`nokaslr`**，内核基址固定 |
| 负载 | 宿主机 `iperf3 -P 20 --bidir -t 15`（40 条流，双向） |

Guest 内仍用 `bin/perf` wrapper 把脚本默认的 `cycles` 换成 `-e cpu-clock`、dwarf 栈展开换成 fp —— **脚本本身没有改动**。

## 归档内容

`langraph.tar.gz`（**9.23 MB**，204 MB → 4.5%，`gzip -t` 通过），解压得到 `langraph/`：

| 文件 | 规模 | 说明 |
|---|---|---|
| `perf.data` | 12.23 MB | **原始采样数据**，63,828 个 `cpu-clock` 采样，可重新解析 |
| `perf.log` | 60.29 MB / 1,141,976 行 | `perf script` 符号化文本 |
| `ftrace.log` | 122.69 MB / 2,227,814 行 | ftrace 事件流，91,772 条 sched/irq |
| `ftrace_format.log` | 503 行 | 事件 format 定义 |
| `ftrace_symbol.log` | 454 行 | 用户栈 `addr2line` 结果 |
| `kallsyms.txt` | 203,527 行 | **运行时符号表**，含 688 个 e1000 模块符号 |
| `modules.txt` / `version.txt` | — | 模块基址 / 内核构建信息 |
| `capture.log` | 18 行 | 采集脚本输出 |

完整清单（含 sha256）见 `MANIFEST.txt`。内核栈热点仍以收包路径为主：`__napi_poll` 12,264、`e1000_clean` 12,221、`e1000_clean_rx_irq` 12,013、`netif_receive_skb_list_internal` 10,660、`tcp_rcv_established` 7,644。

## 三个补上的东西

1. **`ftrace_symbol.log` 生成了。** 之前 Guest 没装 `addr2line`，脚本直接跳过（`capture.log` 里是 `not exist addr2line`）。这轮把宿主机的 `addr2line` 及 libbfd/libz/libzstd/libsframe 一起装进 Guest，脚本走完 `Get ftrace user symbol start...`，产出 454 行。
2. **`perf.data` 完整且可重新解析。** 脚本的 `save_perf` 早于 `perf record`（`sleep t+5`）结束，所以 Guest 里等 perf record 写完后重跑了一次 `perf script` 覆盖 `perf.log`；`perf.data` 是写完后的完整文件。
3. **符号表与本次数据严格对应。** 加了 `nokaslr`，`kallsyms.txt` 里 `tcp_rcv_established = ffffffff81fe3220`、`__napi_poll = ffffffff81efc920`，与 `System.map` 一致。这份 `perf.data` 配同目录的 `kallsyms.txt` 做地址换算/重新符号化是成立的 —— 而 `logs/full/` 里那两轮旧数据是默认 KASLR 采的，地址对不上。

## 用法

```bash
tar xzf langraph.tar.gz            # 得到 langraph/
cd langraph

# 用同目录符号表重新解析原始采样
perf script --kallsyms kallsyms.txt -i perf.data > perf.relog

# 内核栈热点
grep -oE ' (tcp_|e1000_|netif_|__napi_)[a-z_]*\+0x' perf.log \
  | sort | uniq -c | sort -rn | head -20

# 模块内偏移 = 采样地址 - 模块基址（基址见 modules.txt）
```

复现：`./run-langraph.sh`（宿主机编排，约 3~4 分钟），Guest 侧 `guest-init.sh` 会被打进 initramfs；`guest-serial.log` 是这次的完整串口输出，`host_to_guest.json` 是 iperf 双向结果。

## 限制（照例说清）

- **`ftrace_symbol.log` 内容基本是 `?? ??:0`**：Guest 里的 busybox / iperf3 都没有 debug info，`addr2line` 只能确认文件与地址、给不出源码行。文件存在、链路走通，但符号信息有限。真要用户态符号得带 debug 版二进制。
- **TCG 时序、带宽、调度延迟无真实性能意义**；本轮 iperf 40 流发送 270 Mbps / 接收 2.39 Mbps，接收侧偏低是软件模拟下 Guest 发包能力受限，不是配置问题。
- **硬件 PMU 不可用**，perf 用的是 `cpu-clock` 软件事件，`cycles` / `instructions` 在 TCG 下 `ENOENT`。
- `'trace' not valid ... Ignoring` 是 `perf script -F` 里 trace 字段对软件事件不适用，不影响内容。
