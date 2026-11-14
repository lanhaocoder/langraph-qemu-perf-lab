# 完整采集日志（gzip 压缩）

原始日志共 **577 MB**，用 `gzip -9` 压到 **21.5 MB（压缩率 3.7%）**，全部无损入库。日志格式重复度极高（大量重复的 ftrace 行与 perf 调用栈），所以压缩比特别好；最大单文件 10.3 MB，远低于 GitHub 100 MB 上限。

## 目录

| 目录 | 说明 |
|---|---|
| `run2/` | **第二轮（实验报告采用）**：双向 20 流均成功。`ftrace.log` 4,665,579 行 / 252 MB，`perf.log` 1,821,644 行 / 98 MB |
| `run1/` | 首轮：两组流量并发，Guest 主动方向报 `control socket has closed unexpectedly`，留作对照。`ftrace.log` 2,999,702 行 / 164 MB，`perf.log` 1,198,117 行 / 64 MB |

每轮目录里还保留了脚本输出、事件格式定义、串口日志和 iperf json 等小文件（未压缩，原样）。`samples/` 里的 json 与 `run2/` 下的是同一份。

大日志以 `.log.gz` 形式存放，`MANIFEST.txt` 里记录了原始大小、行数、压缩后大小和 sha256 前缀。**四个包都已通过 `gzip -t`，且解压后行数与原始文件逐一比对一致。**

## 还原

```bash
./unpack.sh              # 全部解压到 ./raw/run1/ 和 ./raw/run2/
./unpack.sh --verify     # 只校验压缩包完整性，不解压

# 或者只取单个文件 / 不落盘直接看
gunzip -k run2/perf.log.gz
zcat run2/ftrace.log.gz | head -20
```

`raw/` 已被 `.gitignore` 排除，解压出来的 577 MB 不会误提交。

## 常用分析

```bash
./unpack.sh
cd raw/run2

grep -c 'cpu-clock:' perf.log                        # 采样数：108,944
grep -c 'iperf3' perf.log                           # 命中 iperf3 的行：7,495
grep -oE ' (tcp_|ip_|e1000_|netif_|napi_)[a-z_]*\+0x' perf.log \
  | sed 's/^ //; s/+0x$//' | sort | uniq -c | sort -rn | head -30   # 内核栈热点

awk '{for(i=1;i<=NF;i++) if($i ~ /^(sched|irq)_[a-z_]+:/) {print $i; break}}' ftrace.log \
  | sort | uniq -c | sort -rn | head -20            # 事件类型分布
```

`../perf-hotspots.txt` 和 `../ftrace-stats.txt` 是已经算好的结果摘要，不解压也能看。

## 别忘了这个前提

**TCG 软件模拟下，所有时间戳、调度时序、锁竞争行为和带宽都被模拟器扭曲。** 这些日志能用来验证采集链路、看代码路径和符号热点，但**不能**作为真实延迟或性能回归的结论。真实分析必须在真机或 KVM 加速的 QEMU 上重新采集。

perf 用的是 `cpu-clock` 软件事件（TCG 不虚拟 PMU），不是硬件 `cycles`；细节见 [`../../README.md`](../../README.md) 与 [`../../../docs/02-iperf-perf实验报告.md`](../../../docs/02-iperf-perf实验报告.md)。
