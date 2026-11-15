#!/usr/bin/env python3
"""分析「低速小包」抓包结果：证明硬中断段真实存在，并抽完整因果链。

用法: python3 analyze_hardirq.py <ftrace.log> [out.json]
"""
import re, json, collections, sys, os

LOG = sys.argv[1] if len(sys.argv) > 1 else "/workspace/iperf-hardirq/langraph/ftrace.log"
OUT = sys.argv[2] if len(sys.argv) > 2 else "/workspace/iperf-hardirq/hardirq-analysis.json"

# iperf3-379    (  371) [002] d...  16.904500: sched_waking: comm=iperf3 ...
LINE = re.compile(
    r'^\s*(?P<comm>.+?)-(?P<pid>\d+)\s+\(\s*(?P<tgid>[^)]*)\)\s+'
    r'\[(?P<cpu>\d+)\]\s+(?P<flags>\S+)\s+(?P<ts>\d+\.\d+):\s+'
    r'(?P<ev>[\w]+):\s*(?P<args>.*)$')

IRQ11 = "irq_handler_entry: irq=11"
NETRX_RAISE = "softirq_raise: vec=3"
NAPIPOLL = "napi_poll"

def kv(args):
    d = {}
    for m in re.finditer(r'(\w+)=([^\s]+)', args):
        d[m.group(1)] = m.group(2)
    return d

def main():
    counts = collections.Counter()
    by_sec = collections.defaultdict(collections.Counter)
    irqs, netrx, napi, wakes, switches = [], [], [], [], []
    tmin, tmax = None, None
    nlines = 0
    with open(LOG, errors="ignore") as f:
        for ln in f:
            m = LINE.match(ln.rstrip("\n"))
            if not m:
                continue
            nlines += 1
            ev, args, ts, cpu, comm = m["ev"], m["args"], float(m["ts"]), int(m["cpu"]), m["comm"]
            counts[ev] += 1
            tmin = ts if tmin is None else min(tmin, ts)
            tmax = ts if tmax is None else max(tmax, ts)
            sec = int(ts)
            by_sec[sec][ev] += 1

            if ev == "irq_handler_entry" and "irq=11" in args:
                irqs.append((ts, cpu, comm, args.strip()))
            elif ev == "softirq_raise" and "vec=3" in args:
                netrx.append((ts, cpu, args.strip()))
            elif ev == "napi_poll":
                napi.append((ts, cpu, args.strip()))
            elif ev in ("sched_waking", "sched_wakeup", "sched_wakeup_new") and "iperf3" in args:
                k = kv(args)
                wakes.append((ts, cpu, ev, k.get("comm", ""), k.get("pid", ""),
                              k.get("target_cpu", ""), args.strip()))
            elif ev == "sched_switch" and "next_comm=iperf3" in args:
                k = kv(args)
                switches.append((ts, cpu, k.get("next_comm", ""), k.get("next_pid", ""),
                                 k.get("prev_comm", ""), k.get("prev_state", ""), args.strip()))

    # 按秒分布（只列关键事件）
    secs = []
    for s in sorted(by_sec):
        c = by_sec[s]
        secs.append({"sec": s,
                     "irq11": sum(1 for t, *_ in irqs if int(t) == s),
                     "netrx_raise": sum(1 for t, *_ in netrx if int(t) == s),
                     "napi_poll": sum(1 for t, *_ in napi if int(t) == s),
                     "wake_iperf3": sum(1 for w in wakes if int(w[0]) == s),
                     "switch_to_iperf3": sum(1 for w in switches if int(w[0]) == s)})

    # 抽完整因果链：对每个 eth0 硬中断，在其后 5ms 内找 NET_RX / napi_poll /
    # 唤醒 iperf3 / 入队 / switch 上 CPU，直到下一个硬中断为止
    chains = []
    for i, (t0, cpu0, c0, a0) in enumerate(irqs):
        t_next = irqs[i + 1][0] if i + 1 < len(irqs) else t0 + 5.0
        tend = min(t0 + 0.005, t_next)
        nodes = [{"dt_us": 0.0, "ts": t0, "cpu": cpu0, "comm": c0,
                  "event": "irq_handler_entry", "args": a0}]
        seen = set()
        for t, cpu, a in netrx:
            if t0 < t <= tend and ("raise", t) not in seen:
                nodes.append({"dt_us": round((t - t0) * 1e6, 1), "ts": t, "cpu": cpu,
                              "event": "softirq_raise(NET_RX)", "args": a}); break
        for t, cpu, a in napi:
            if t0 < t <= tend:
                nodes.append({"dt_us": round((t - t0) * 1e6, 1), "ts": t, "cpu": cpu,
                              "event": "napi_poll", "args": a}); break
        for w in wakes:
            if t0 < w[0] <= tend and w[2] == "sched_waking":
                nodes.append({"dt_us": round((w[0] - t0) * 1e6, 1), "ts": w[0], "cpu": w[1],
                              "event": "sched_waking(iperf3)", "comm": w[3], "pid": w[4],
                              "target_cpu": w[5], "args": w[6]}); break
        for w in wakes:
            if t0 < w[0] <= tend and w[2] in ("sched_wakeup", "sched_wakeup_new"):
                nodes.append({"dt_us": round((w[0] - t0) * 1e6, 1), "ts": w[0], "cpu": w[1],
                              "event": w[2] + "(iperf3)", "comm": w[3], "pid": w[4],
                              "target_cpu": w[5], "args": w[6]}); break
        for w in switches:
            if t0 < w[0] <= tend:
                nodes.append({"dt_us": round((w[0] - t0) * 1e6, 1), "ts": w[0], "cpu": w[1],
                              "event": "sched_switch(next=iperf3)", "comm": w[2], "pid": w[3],
                              "prev_comm": w[4], "prev_state": w[5], "args": w[6]}); break
        nodes.sort(key=lambda x: x["ts"])
        kinds = [n["event"] for n in nodes]
        score = sum(k in kinds for k in ("softirq_raise(NET_RX)", "napi_poll",
                                         "sched_waking(iperf3)", "sched_switch(next=iperf3)"))
        if score >= 2:
            chains.append({"score": score, "t0": t0, "t1": nodes[-1]["ts"],
                           "span_us": round((nodes[-1]["ts"] - t0) * 1e6, 1),
                           "nodes": nodes})
    chains.sort(key=lambda c: (-c["score"], c["span_us"]))

    # /proc/interrupts 前后差值
    intr = {}
    base = os.path.dirname(LOG)
    for tag in ("before", "after"):
        p = os.path.join(base, "interrupts.%s.txt" % tag)
        if os.path.exists(p):
            d = {}
            for ln in open(p, errors="ignore"):
                mm = re.match(r'\s*(\d+):\s+(\d+)\s+.*?(\S+)\s*$', ln.rstrip())
                if mm and ("eth" in ln or "virtio" in ln):
                    d[mm.group(3)] = int(mm.group(2))
            intr[tag] = d
    intr_delta = {}
    if "before" in intr and "after" in intr:
        for k in set(intr["before"]) | set(intr["after"]):
            intr_delta[k] = intr["after"].get(k, 0) - intr["before"].get(k, 0)

    res = {
        "log": LOG, "parsed_lines": nlines, "tmin": tmin, "tmax": tmax,
        "counts": {k: v for k, v in counts.most_common()},
        "totals": {"irq11_eth0": len(irqs), "netrx_raise": len(netrx),
                   "napi_poll": len(napi), "wake_iperf3": len(wakes),
                   "switch_to_iperf3": len(switches)},
        "by_second": secs,
        "interrupts_before": intr.get("before", {}),
        "interrupts_after": intr.get("after", {}),
        "interrupts_delta": intr_delta,
        "full_chains": chains[:5],
        "n_full_chains": len(chains),
    }
    json.dump(res, open(OUT, "w"), ensure_ascii=False, indent=2)

    print("解析事件行: %d  时间 %.3f~%.3f s" % (nlines, tmin, tmax))
    print("总计: eth0硬中断=%d  NET_RX raise=%d  napi_poll=%d  唤醒iperf3=%d  switch到iperf3=%d"
          % (len(irqs), len(netrx), len(napi), len(wakes), len(switches)))
    print("\n按秒分布:")
    print("  sec  irq11  netrx  napi  wake  switch")
    for s in secs:
        print("  %3d %6d %6d %5d %5d %7d" % (s["sec"], s["irq11"], s["netrx_raise"],
                                              s["napi_poll"], s["wake_iperf3"],
                                              s["switch_to_iperf3"]))
    print("\n/proc/interrupts 差值:", intr_delta if intr_delta else "(无)")
    print("\n完整链候选: %d 条；最完整一条 (score=%d, 跨度 %.1f µs):"
          % (len(chains), chains[0]["score"] if chains else 0,
             chains[0]["span_us"] if chains else 0))
    if chains:
        for n in chains[0]["nodes"]:
            print("   +%8.1f µs [CPU%s] %-30s %s" %
                  (n["dt_us"], n["cpu"], n["event"],
                   (n.get("comm", "") + " " + n.get("pid", "")).strip()))
    print("\n写出:", OUT)

if __name__ == "__main__":
    main()
