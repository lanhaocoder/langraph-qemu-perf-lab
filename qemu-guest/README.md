# 在 QEMU 里执行 LanGraph —— 已实测跑通

## 结论

**能起，也已经跑起来了。** 云沙箱没有 `/dev/kvm`、CPU 不暴露 vmx/svm，所以用 QEMU 8.2.2 的 **TCG 纯软件模拟**；Guest 有自己独立的内核，自带 tracefs，因此绕开了宿主机对 perf/ftrace 的封锁。在 Guest 里实际执行了 `langraph`，也跑通了 `get_ftrace.sh`。

Guest 环境：`6.8.0-31-generic`，TCG，2 vCPU / 1 GB 内存，busybox 静态 initramfs。

## 实测对比

| 能力 | 云沙箱宿主机 | QEMU Guest |
|---|---|---|
| tracefs / debugfs 挂载 | `permission denied` | OK |
| ftrace 采集 sched/irq | 不可用 | **OK，381 条事件** |
| `get_ftrace.sh` | 不可用 | **跑通，ftrace.log 149 KB** |
| perf 软件事件 `task-clock` | `EPERM` | **OK** |
| perf 软件事件 `context-switches` | `EPERM` | **OK** |
| perf 硬件 PMU `cycles` | `EPERM` | `ENOENT`（TCG 不虚拟 PMU） |
| `langraph trace.dat` | 能跑 | **能跑，正常输出 sched_waking / 栈事件** |

完整串口记录在 `实测日志.txt`。

## 文件

| 文件 | 用途 |
|---|---|
| `vmlinuz` | Guest 内核（Ubuntu 6.8.0-31-generic，15 MB） |
| `initramfs.cpio.gz` | 已构建好的根文件系统，含 langraph、依赖库、trace.dat、采集脚本 |
| `guest-init.sh` | Guest 的 `/init`：挂 tracefs → 跑 langraph → 跑采集脚本 → perf 探测 → 关机 |
| `perftest.c` | Guest 内 perf_event_open 探测程序（静态编译） |
| `boot.sh` | 一条命令启动 |
| `build-guest.sh` | 改完代码后重建 initramfs |
| `kallsyms/` | Guest 运行时 `/proc/kallsyms`（含 e1000 模块符号）+ `System.map` + 抓取脚本，见其中 README |

## 用法

```bash
./boot.sh > run.log 2>&1      # 启动，几十秒内自动跑完并关机
./build-guest.sh              # 改了 guest-init.sh 或 LanGraph 代码后重建
./build-guest.sh /path/to/LanGraph
```

在你自己的 WSL2（KVM 可用）上，把 `boot.sh` 里的 `-accel tcg` 换成 `-enable-kvm`，速度会有数量级提升。

## 三个必须知道的限制

1. **TCG 下采到的调度延迟数据没有真实性能意义。** 指令是软件模拟的，时间戳、调度时序、锁竞争行为都被模拟器扭曲，只能用来验证「代码路径能跑通、能采到数据」，**不能**用来分析真实延迟、锁竞争或性能回归。真实结论必须在真机或 KVM 加速的 WSL2 上采集。
2. **硬件 PMU 不可用。** TCG 不虚拟 PMU，perf 只能用软件事件和 tracepoint，`cycles` / `instructions` / `cache-misses` 全部返回 `ENOENT`。
3. **脚本依赖已补齐的路径。** `get_ftrace.sh` 内部探测 `/sys/kernel/debug/tracing`，`guest-init.sh` 里挂了 debugfs 才走对分支；Guest 内没装 `addr2line`（用户态符号解析跳过）和 `trace-cmd`，所以脚本产出的是 ftrace 文本日志而非 `trace.dat`，`langraph` 读的是随包带进去的样例 `trace.dat`。若要 stat_sleep / stat_runtime 等事件，在 `-append` 里加 `schedstats=enable`。

> 你资料库里《NVMe-QEMU-验证环境》那套（WSL2 + KVM + 自编译 7.3.0-rc4 + busybox initramfs）有硬件加速，数据才具备参考价值；这里只是云沙箱内的功能验证环境。
