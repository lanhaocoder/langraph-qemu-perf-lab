# 实验 3：Guest 内 20 线程双向 iperf + perf/ftrace 采集

完整报告见 [`../docs/02-iperf-perf实验报告.md`](../docs/02-iperf-perf实验报告.md)。

## 拓扑

```
宿主机                                     QEMU Guest (TCG, 4 vCPU/2G)
  iperf3 -s :5211  ◄──── slirp ──────      iperf3 -c 10.0.2.2:5211 -P 20 --bidir
  iperf3 -c :15201 ──── hostfwd ─────►     iperf3 -s :5201
  nc -l :9999      ◄─── slirp ───────      回传 tar（兜底 httpd :8080 → hostfwd 18080）
```

两组各 12 秒、**错开执行**（`sleep 16`），避免 slirp 上两组 40 个并发流互抢控制连接。第一轮并发时 Guest 主动那路报 `control socket has closed unexpectedly`，错开时序 + 独立端口 + `iperf3 -s -D` 常驻后修复。

## 采集

Guest 内执行 `get_ftrace.sh -t 35 -p 1`（`-p 1` 打开 perf 分支），同时抓 perf 与 ftrace。

| 产物 | 规模 |
|---|---|
| `perf.data` | 19.85 MB，108,944 个 `cpu-clock` 采样 |
| `perf.log` | 98 MB / 1,821,644 行 |
| `ftrace.log` | 253 MB / 4,665,579 行，701,520 条 sched/irq 事件 |

## 文件

| 路径 | 说明 |
|---|---|
| `run.sh` | 宿主机编排：起 iperf server、起 QEMU、反向压测、接收回传并解包 |
| `guest-init.sh` | Guest `/init`：挂 tracefs/debugfs → insmod e1000 → 起采集 → 压测 → 导出日志 |
| `qemu.log` | Guest 串口输出（含采集与压测过程） |
| `samples/host_to_guest.json` | 宿主机 → Guest 方向 `--bidir` 结果（`-J` 格式） |
| `samples/iperf_guest_to_host.json` | Guest → 宿主机方向 `--bidir` 结果（`-J` 格式） |
| `samples/capture.log` | 采集脚本输出，含 `Captured and wrote 19.853 MB perf.data (108944 samples)` |
| `samples/perf_script.err` | `perf script` 的 stderr（`'trace' not valid` 属正常，字段对软件事件不适用） |
| `logs/perf.log.head.txt` | `perf.log` 前 300 行样本（含调用栈） |
| `logs/ftrace.log.head.txt` | `ftrace.log` 前 200 行样本 |
| `logs/perf-hotspots.txt` | 内核符号热点 top30 + 进程维度统计 |
| `logs/ftrace-stats.txt` | 事件类型分布统计 |
| `logs/full/` | **完整原始日志**：两轮采集的 `*.log.gz`（577 MB → 21.5 MB）、`MANIFEST.txt`、`unpack.sh`、`README.md` |

> 内核符号表见 `../qemu-guest/kallsyms/`。`guest-init.sh` 现在会在 `insmod e1000` 之后导出 `kallsyms.txt` / `modules.txt` / `version.txt`，随 `result.tar` 一起回传；`run.sh` 用 `tar xf` 解包，会自动落到 `guest/` 下。注意本目录已有的两轮日志是加这一步之前采的，未附带符号表。

## 两处处理说明

1. **perf 用 `cpu-clock` 软件事件。** TCG 不虚拟 PMU，`cycles` 直接 `ENOENT`。Guest 内放了一个 `bin/perf` wrapper 换成 `-e cpu-clock` + `-g --call-graph=fp`，宿主机的 `get_ftrace.sh` **未改动**。
2. **`save_perf` 早于 `perf record` 结束**（record 是 `sleep t+5`，脚本 `t` 秒收尾），所以 `guest-init.sh` 在等 perf.data 写完后重新跑一次 `perf script`，覆盖生成完整 `perf.log`。

## 重跑

```bash
./run.sh                                    # 约 2~3 分钟
grep -c 'cpu-clock:' guest/perf.log         # 采样数
grep -oE ' tcp_[a-z_]+\+0x' guest/perf.log | sort | uniq -c | sort -rn | head
```

前提：`/tmp/vmlinuz` 与 `/tmp/initramfs_iperf.cpio.gz` 已就位（由 `../qemu-guest/build-guest.sh` 构建，再装入 `e1000.ko`、`perf`、`iperf3`）。
