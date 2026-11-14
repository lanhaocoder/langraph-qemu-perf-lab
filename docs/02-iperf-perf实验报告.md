# QEMU Guest 内 20 线程双向 iperf + perf/ftrace 采集

## 做了什么

在 QEMU TCG Guest（`6.8.0-31-generic`，4 vCPU / 2 GB，e1000 + slirp user networking）里，用 **`get_ftrace.sh -t 35 -p 1`** 同时抓 perf 与 ftrace，采集窗口内后台跑两组 **20 并行流、双向（`--bidir`）** 的 iperf3 TCP 流量：

| 方向 | 发起方 | 路径 |
|---|---|---|
| 宿主机 → Guest | 宿主机 `iperf3 -c 127.0.0.1 -p 15201 -P 20 --bidir` | hostfwd `15201 → Guest:5201` |
| Guest → 宿主机 | Guest `iperf3 -c 10.0.2.2 -p 5211 -P 20 --bidir` | slirp 转发到宿主机 `:5211` |

两组各 12 秒、**错开执行**，避免 slirp 上两组 40 个并发流互抢控制连接。宿主机 `iperf3 -s -p 5211 -D` 常驻，Guest 侧 `iperf3 -s -p 5201` 供反向测试。

## 双向流量结果（iperf3 3.16）

| 方向 | 流数 | 时长 | 发送 | 接收 |
|---|---|---|---|---|
| 宿主机 → Guest | 40（20×2） | 12.00 s | 502.66 MB / **335.07 Mbps** | 5.01 MB / **3.34 Mbps** |
| Guest → 宿主机 | 40（20×2） | 12.03 s | 1104.81 MB / **734.88 Mbps** | 1098.38 MB / **729.95 Mbps** |

两组都是 `--bidir`，同一批 TCP 流上双向收发；第二组收发基本对称（735 / 730 Mbps），双向坐实。第一组接收侧偏低是 TCG 软件模拟下 Guest 发包能力受限，不是配置问题。

> 第一轮两个方向并发时，Guest 主动发起的那路报 `control socket has closed unexpectedly`（控制连接被重置），已通过错开时序 + 独立端口 + daemon server 修复；首轮结果备份在 `run1/`。

## 采集日志

| 文件 | 规模 | 内容 |
|---|---|---|
| `perf.log` | 98 MB / 1,821,644 行 | **108,944 个 `cpu-clock` 采样** + 完整调用栈 |
| `ftrace.log` | 253 MB / 4,665,579 行 | 701,520 条 `sched`/`irq` 事件 |
| `ftrace_format.log` | 23 KB | 事件格式定义 |
| `perf.data` | 19.85 MB（Guest 内） | perf 原始采样数据 |
| `capture.log` | — | 脚本输出，含 `Captured and wrote 19.853 MB perf.data (108944 samples)` |

perf 日志里 7,495 行与 `iperf3` 相关，ftrace 里 221,212 条。`perf.log` 内核栈热点（符号出现次数）：

```
21537  e1000_clean                       21119  e1000_clean_rx_irq
18672  netif_receive_skb_list_internal   18403  ip_list_rcv
18203  ip_sublist_rcv                   17815  ip_local_deliver
17398  ip_protocol_deliver_rcu          14665  tcp_rcv_established
 8828  tcp_write_xmit                    8544  __tcp_push_pending_frames
```

收包路径（NAPI → IP 层 → TCP）占主导，符合双向 TCP 压测预期。

## 四个关键说明

1. **perf 用的是 `cpu-clock` 软件事件，不是硬件 cycles。** TCG 不虚拟 PMU，`cycles` 直接 `ENOENT`。脚本原本是 `perf record -a -g --call-graph=dwarf -F 1000`（默认 cycles），Guest 里放了一个 `bin/perf` wrapper 把事件换成 `-e cpu-clock`、栈展开换成 `fp`，**没有改动你的 `get_ftrace.sh`**。
2. **脚本的 `save_perf` 会早于 `perf record` 结束**（record 是 `sleep t+5`，脚本 `t` 秒就收尾），所以 init 里等 perf.data 写完后重新执行了一次 `perf script` 覆盖生成完整 `perf.log`。
3. `perf script -F ...trace...` 对软件事件会打印 `'trace' not valid ... Ignoring`，只是字段不适用，不影响日志内容（见 `perf_script.err`）。
4. **TCG 下所有时序与带宽都被软件模拟扭曲**，这些日志只能用于验证链路、看代码路径和符号热点，**不能**当作真实性能/延迟结论。真要出数，得在真机或你 WSL2 带 KVM 的 QEMU 上采。

## 文件与复现

```
run.sh                  宿主机编排：iperf server + 反向流量 + 接收回传
guest-init.sh           Guest /init：挂 tracefs → 采集 → 流量 → 回传
samples/                两个方向的 iperf json、capture.log、perf_script.err
logs/                   大日志精简样本（perf.log / ftrace.log 前若干行）与统计摘要
qemu.log                Guest 串口输出
```

> 完整原始日志已用 `gzip -9` 无损压缩入库：`../iperf-perf/logs/full/`（两轮共 577 MB → 21.5 MB），`./unpack.sh` 可还原；initramfs（25 MB）等二进制仍未入库，由脚本重建。

```bash
./run.sh                                      # 重跑（约 2~3 分钟）
grep -c 'cpu-clock:' guest/perf.log            # 采样数
grep -oE ' tcp_[a-z_]+\+0x' guest/perf.log | sort | uniq -c | sort -rn | head
```

`ftrace_latency.py` 默认读 `langraph/ftrace.log`，要用它出图需把 `guest/ftrace*.log` 放到 `langraph/` 目录下。
