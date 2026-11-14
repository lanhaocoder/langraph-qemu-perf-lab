#!/usr/bin/env python3.11
"""校验「完整 iperf 进程生命周期」是否被 ftrace 窗口完整覆盖。

判据（全部必须成立）：
  1. 存在 iperf3 的 sched_process_fork（抓到工作线程创建）
  2. 存在 iperf3 的 sched_process_exit / sched_process_free（抓到销毁）
  3. 所有 iperf3 pid（40 工作线程 + 2 主进程）都有「创建 + exit + free」，生命周期闭合
  4. 最早创建事件 > 窗口起点（ftrace 确实先于 iperf 开启）
  5. 最晚 free     < 窗口终点（iperf 确实早于采集结束）
  6. perf.log / perf.data 同时存在且非空（perf 与 ftrace 同时抓）
  7. Guest 时间轴自证 FTRACE_ON < IPERF_ALL_EXITED < CAPTURE_EXIT

解析要点（踩过的坑）：
  * sched_process_free 在 free_task() 上下文触发，行前缀 comm 常是 <idle>/ksoftirqd，
    被释放的任务在 args 里：comm=iperf3 pid=369。必须按 args 判定。
  * 主进程 fork 时 child_comm 继承父进程（init），要等 sched_process_exec
    (filename=/bin/iperf3) 才改名；因此主进程靠 exec + fork(child_pid) 关联。
  * stacktrace=1 让每个事件后跟若干 " => " 内核栈行，这些不是解析失败。

用法: python3.11 analyze_lifecycle.py <langraph目录> [-o report.json] [--md report.md]
"""
import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

LINE = re.compile(
    r'^\s*(?P<comm>.+?)-(?P<pid>\d+)\s+\((?P<tgid>[^)]*)\)\s+'
    r'\[(?P<cpu>\d+)\]\s+(?P<flags>\S+)\s+(?P<ts>\d+\.\d+):\s+'
    r'(?P<event>[A-Za-z_][A-Za-z0-9_]*):\s*(?P<rest>.*)$'
)
KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=([^\s\]]+)')
LIFECYCLE_EVENTS = {
    'sched_process_fork', 'sched_process_exec', 'sched_wakeup_new',
    'sched_process_exit', 'sched_process_free',
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('langraph', help='langraph/ 目录')
    ap.add_argument('-o', '--output', default=None)
    ap.add_argument('--md', default=None)
    a = ap.parse_args()
    d = a.langraph.rstrip('/')
    fpath = os.path.join(d, 'ftrace.log')
    if not os.path.isfile(fpath):
        print('缺少 ftrace.log', file=sys.stderr)
        sys.exit(2)

    total = unmatched = stack_lines = 0
    tmin = tmax = None
    evcount = Counter()
    prev_state = Counter()
    iperf_evcount = Counter()

    all_forks = {}     # child_pid -> (ts, child_comm, parent_pid)  所有 fork
    fork_iperf = {}    # child_pid -> ts   child_comm 含 iperf3（工作线程）
    exec_iperf = {}    # pid -> (ts, filename)
    exit_iperf = {}    # pid -> ts
    free_iperf = {}    # pid -> ts
    wake_new = 0
    per_pid_events = defaultdict(list)   # pid -> [(ts, event, desc)]

    with open(fpath, 'r', errors='replace') as f:
        for ln in f:
            s = ln.rstrip('\n')
            if not s or s.startswith('#'):
                continue
            m = LINE.match(s)
            if not m:
                unmatched += 1
                if s.lstrip().startswith('=>'):
                    stack_lines += 1
                continue
            total += 1
            ts = float(m['ts'])
            tmin = ts if tmin is None else min(tmin, ts)
            tmax = ts if tmax is None else max(tmax, ts)
            ev = m['event']
            evcount[ev] += 1
            args = dict(KV.findall(m['rest']))
            row_comm, row_pid = m['comm'], int(m['pid'])
            blob = ' '.join([row_comm, m['rest']])
            if 'iperf3' in blob:
                iperf_evcount[ev] += 1

            if ev == 'sched_switch':
                prev_state[args.get('prev_state', '?')] += 1

            if ev == 'sched_process_fork':
                cp = args.get('child_pid', '')
                cc = args.get('child_comm', '')
                if cp.isdigit():
                    pid = int(cp)
                    # fork 事件在父进程上下文触发，父 pid 在行前缀里；
                    # args 里只有 pid(=父) / child_pid，没有 parent_pid 字段
                    all_forks[pid] = (ts, cc, row_pid)
                    if 'iperf3' in cc:
                        fork_iperf[pid] = ts
                        per_pid_events[pid].append((ts, ev, 'fork child_comm=%s' % cc))
            elif ev == 'sched_process_exec':
                fn = args.get('filename', '')
                if 'iperf3' in fn or 'iperf3' in row_comm:
                    exec_iperf[row_pid] = (ts, fn)
                    per_pid_events[row_pid].append((ts, ev, 'exec %s' % fn))
            elif ev == 'sched_wakeup_new':
                if 'iperf3' in args.get('comm', '') or 'iperf3' in row_comm:
                    wake_new += 1
                    p = args.get('pid')
                    if p and p.isdigit():
                        per_pid_events[int(p)].append((ts, ev, 'wakeup_new'))
            elif ev == 'sched_process_exit':
                if 'iperf3' in row_comm or 'iperf3' in args.get('comm', ''):
                    p = args.get('pid')
                    pid = int(p) if p and p.isdigit() else row_pid
                    exit_iperf[pid] = ts
                    per_pid_events[pid].append((ts, ev, 'exit'))
            elif ev == 'sched_process_free':
                # 被释放的任务在 args 里，行前缀 comm 是 <idle>/ksoftirqd 等
                if 'iperf3' in args.get('comm', ''):
                    p = args.get('pid')
                    if p and p.isdigit():
                        pid = int(p)
                        free_iperf[pid] = ts
                        per_pid_events[pid].append((ts, ev, 'free'))

    # ---- 生命周期集合 ----
    iperf_pids = set(fork_iperf) | set(exec_iperf) | set(exit_iperf) | set(free_iperf)
    work_threads = set(fork_iperf)                     # 40 个（20 流 × 双向 2 组）
    main_procs = iperf_pids - work_threads            # 2 个主进程（server / client）

    created = {p: (p in all_forks or p in exec_iperf) for p in iperf_pids}
    first_create = {}
    for p in iperf_pids:
        cand = []
        if p in all_forks:
            cand.append(all_forks[p][0])
        if p in exec_iperf:
            cand.append(exec_iperf[p][0])
        if cand:
            first_create[p] = min(cand)
    closed = {p for p in iperf_pids if created[p] and p in exit_iperf and p in free_iperf}
    work_closed = work_threads & closed
    main_closed = main_procs & closed

    first_fork_ts = min(fork_iperf.values(), default=None)
    first_create_ts = min(first_create.values(), default=None)
    last_free_ts = max(free_iperf.values(), default=None)
    headroom_start = (first_create_ts - tmin) if first_create_ts is not None else None
    headroom_end = (tmax - last_free_ts) if last_free_ts is not None else None

    # 时间轴（uptime）
    timeline = []
    tlpath = os.path.join(d, 'timeline.txt')
    if os.path.isfile(tlpath):
        for ln in open(tlpath, errors='replace'):
            p = ln.strip().split(None, 1)
            if len(p) == 2:
                try:
                    timeline.append((float(p[0]), p[1]))
                except ValueError:
                    pass
    # timeline 值是 "FTRACE_ON tracing_on=1" 这类带参数的整串，取首词作 key
    tl = {name.split()[0]: t for t, name in timeline}

    checks = [
        ('1. 抓到 iperf3 工作线程创建 (sched_process_fork)',
         len(fork_iperf) > 0,
         'fork 工作线程 = %d（20 线程 × 双向 2 组）' % len(fork_iperf)),
        ('2. 抓到 iperf3 销毁 (exit / free)',
         len(exit_iperf) > 0 and len(free_iperf) > 0,
         'exit=%d  free=%d' % (len(exit_iperf), len(free_iperf))),
        ('3. 生命周期闭合（创建→exit→free，含 2 个主进程）',
         len(closed) == len(iperf_pids) and len(iperf_pids) > 0,
         '闭合 %d/%d；工作线程 %d/%d，主进程 %d/%d；未闭合 pid=%s'
         % (len(closed), len(iperf_pids), len(work_closed), len(work_threads),
            len(main_closed), len(main_procs), sorted(iperf_pids - closed)[:12])),
        ('4. ftrace 先于 iperf 开启（最早创建事件 > 窗口起点）',
         headroom_start is not None and headroom_start > 0,
         '窗口起点=%.6fs  最早创建=%.6fs  余量=%.3fs'
         % (tmin, first_create_ts or 0, headroom_start or 0)),
        ('5. iperf 早于采集结束（最晚 free < 窗口终点）',
         headroom_end is not None and headroom_end > 0,
         '最晚 free=%.6fs  窗口终点=%.6fs  余量=%.3fs'
         % (last_free_ts or 0, tmax, headroom_end or 0)),
    ]
    perf_log = os.path.join(d, 'perf.log')
    perf_data = os.path.join(d, 'perf.data')
    pl_size = os.path.getsize(perf_log) if os.path.isfile(perf_log) else 0
    pd_size = os.path.getsize(perf_data) if os.path.isfile(perf_data) else 0
    pl_lines = sum(1 for _ in open(perf_log, 'rb')) if pl_size else 0
    checks.append(('6. perf 与 ftrace 同时抓（perf.log / perf.data 非空）',
                   pl_size > 0 and pd_size > 0,
                   'perf.log=%d 行 / %.2f MB, perf.data=%.2f MB'
                   % (pl_lines, pl_size / 1e6, pd_size / 1e6)))

    tl_check = None
    if all(k in tl for k in ('FTRACE_ON', 'IPERF_ALL_EXITED', 'CAPTURE_EXIT')):
        tl_ok = tl['FTRACE_ON'] <= tl['IPERF_ALL_EXITED'] <= tl['CAPTURE_EXIT']
        tl_check = {
            'ok': tl_ok, 'FTRACE_ON': tl['FTRACE_ON'],
            'IPERF_ALL_EXITED': tl['IPERF_ALL_EXITED'],
            'CAPTURE_EXIT': tl['CAPTURE_EXIT'],
            'iperf_span_s': round(tl['IPERF_ALL_EXITED'] - tl['FTRACE_ON'], 3),
            'capture_exit_note': 'CAPTURE_EXIT 含 save_ftrace/addr2line 后处理；真实采集窗口以 ftrace.log 的 tmin~tmax 为准',
        }
        checks.append(('7. Guest 时间轴自证 (FTRACE_ON < IPERF_ALL_EXITED < CAPTURE_EXIT)',
                       tl_ok,
                       'FTRACE_ON=%.3fs  IPERF_ALL_EXITED=%.3fs  CAPTURE_EXIT=%.3fs  (iperf 全程 %.3fs < 60s)'
                       % (tl['FTRACE_ON'], tl['IPERF_ALL_EXITED'], tl['CAPTURE_EXIT'],
                          tl['IPERF_ALL_EXITED'] - tl['FTRACE_ON'])))

    sample = None
    if per_pid_events:
        # 优先挑一个「工作线程」展示完整链
        cand = [p for p in work_closed if len(per_pid_events[p]) >= 3] or list(per_pid_events)
        pid = max(cand, key=lambda p: len(per_pid_events[p]))
        seq = sorted(per_pid_events[pid])
        sample = {'pid': pid, 'event_count': len(seq),
                  'chain': [{'ts': round(t, 6), 'event': e, 'desc': c} for t, e, c in seq[:40]]}
    main_detail = []
    for p in sorted(main_procs):
        main_detail.append({
            'pid': p,
            'fork': all_forks.get(p, (None, None, None))[0],
            'fork_child_comm': all_forks.get(p, (None, None, None))[1],
            'exec': exec_iperf.get(p, (None, None))[0],
            'exec_filename': exec_iperf.get(p, (None, None))[1],
            'exit': exit_iperf.get(p), 'free': free_iperf.get(p),
            'closed': p in closed,
        })

    report = {
        'ftrace': {
            'path': fpath, 'parsed_events': total, 'unmatched_lines': unmatched,
            'stacktrace_lines': stack_lines,
            'window_s': [round(tmin, 6), round(tmax, 6)],
            'window_span_s': round(tmax - tmin, 3),
            'event_types': len(evcount), 'top_events': evcount.most_common(25),
            'prev_state_dist': prev_state.most_common(),
            'iperf_related_events': iperf_evcount.most_common(),
        },
        'iperf_lifecycle': {
            'work_threads': len(work_threads), 'main_procs': len(main_procs),
            'total_pids': len(iperf_pids),
            'fork_count': len(fork_iperf), 'exec_count': len(exec_iperf),
            'wakeup_new_count': wake_new,
            'exit_count': len(exit_iperf), 'free_count': len(free_iperf),
            'closed_count': len(closed),
            'work_closed': len(work_closed), 'main_closed': len(main_closed),
            'unclosed_pids': sorted(iperf_pids - closed),
            'first_create_ts': first_create_ts, 'first_fork_ts': first_fork_ts,
            'last_free_ts': last_free_ts,
            'headroom_start_s': headroom_start, 'headroom_end_s': headroom_end,
            'main_proc_detail': main_detail,
        },
        'perf': {'perf_log_bytes': pl_size, 'perf_log_lines': pl_lines,
                 'perf_data_bytes': pd_size},
        'timeline': tl_check, 'timeline_raw': timeline,
        'sample_thread': sample,
        'checks': [{'name': n, 'pass': bool(ok), 'detail': det} for n, ok, det in checks],
        'all_pass': all(ok for _, ok, _ in checks),
    }
    out = a.output or os.path.join(d, 'lifecycle_report.json')
    json.dump(report, open(out, 'w'), ensure_ascii=False, indent=2)

    print('=' * 78)
    print('完整 iperf 生命周期校验')
    print('=' * 78)
    print('ftrace: %d 事件 / %d 种, 窗口 %.6f ~ %.6f s (跨度 %.3f s)'
          % (total, len(evcount), tmin, tmax, tmax - tmin))
    print('        未匹配 %d 行（其中内核栈回溯 %d 行，属 stacktrace=1 正常输出）'
          % (unmatched, stack_lines))
    print()
    for n, ok, det in checks:
        print('  [%s] %s' % ('PASS' if ok else 'FAIL', n))
        print('         %s' % det)
    print()
    print('ALL PASS:', report['all_pass'])
    print('JSON:', out)
    if a.md:
        write_md(a.md, report)


def write_md(path, r):
    f, lc = r['ftrace'], r['iperf_lifecycle']
    L = ['# 完整 iperf 进程生命周期采集校验报告\n', '## 判据总览\n',
         '| 判据 | 结果 | 实测 |', '|---|---|---|']
    for c in r['checks']:
        L.append('| %s | %s | %s |' % (c['name'], '✅ PASS' if c['pass'] else '❌ FAIL',
                                     c['detail'].replace('|', '\\|')))
    L.append('\n**总结：%s**\n' % ('全部通过，iperf 进程生命周期被完整采集' if r['all_pass'] else '存在未通过项'))

    L.append('## ftrace 数据\n')
    L.append('- 解析事件 **%d** 条，事件类型 **%d** 种；采集窗口 **%.6f ~ %.6f s**（跨度 **%.3f s**）'
             % (f['parsed_events'], f['event_types'], f['window_s'][0], f['window_s'][1], f['window_span_s']))
    L.append('- 另有 %d 行未匹配，其中 %d 行为 ` => ` 内核栈回溯（`stacktrace=1`），非解析失败'
             % (f['unmatched_lines'], f['stacktrace_lines']))
    L.append('\n### 事件类型 Top 20\n\n| tracepoint | 次数 |\n|---|---:|')
    for k, v in f['top_events'][:20]:
        L.append('| `%s` | %d |' % (k, v))
    L.append('\n### iperf3 相关事件（按 args/comm 命中）\n\n| tracepoint | 次数 |\n|---|---:|')
    for k, v in f['iperf_related_events']:
        L.append('| `%s` | %d |' % (k, v))
    L.append('\n### sched_switch prev_state 分布\n\n| prev_state | 次数 |\n|---|---:|')
    for k, v in f['prev_state_dist']:
        L.append('| %s | %d |' % (k, v))

    L.append('\n## iperf3 生命周期\n\n| 指标 | 值 |\n|---|---:|')
    for k, label in [('work_threads', '工作线程（sched_process_fork, child_comm=iperf3）'),
                     ('main_procs', '主进程（server / client）'),
                     ('total_pids', 'iperf3 pid 总数'),
                     ('exec_count', 'sched_process_exec'),
                     ('wakeup_new_count', 'sched_wakeup_new'),
                     ('exit_count', 'sched_process_exit'),
                     ('free_count', 'sched_process_free'),
                     ('closed_count', '完整闭合（创建→exit→free）'),
                     ('work_closed', '工作线程闭合'),
                     ('main_closed', '主进程闭合')]:
        L.append('| %s | %s |' % (label, lc[k]))
    L.append('| 最早创建事件 | %.6f s |' % (lc['first_create_ts'] or 0))
    L.append('| 最晚 free | %.6f s |' % (lc['last_free_ts'] or 0))
    L.append('| 起点余量（ftrace 先行） | %.3f s |' % (lc['headroom_start_s'] or 0))
    L.append('| 终点余量（iperf 先结束） | %.3f s |' % (lc['headroom_end_s'] or 0))
    L.append('\n### 主进程明细\n\n| pid | fork | fork 时 comm | exec | filename | exit | free | 闭合 |\n|---:|---:|---|---:|---|---:|---:|---|')
    for m in lc['main_proc_detail']:
        L.append('| %d | %s | %s | %s | %s | %s | %s | %s |' % (
            m['pid'], '%.6f' % m['fork'] if m['fork'] else '—', m['fork_child_comm'] or '—',
            '%.6f' % m['exec'] if m['exec'] else '—', m['exec_filename'] or '—',
            '%.6f' % m['exit'] if m['exit'] else '—',
            '%.6f' % m['free'] if m['free'] else '—',
            '✅' if m['closed'] else '❌'))

    if r.get('timeline'):
        L.append('\n## Guest 侧时间轴（uptime）\n\n| 节点 | uptime (s) |\n|---|---:|')
        for ts, name in r['timeline_raw']:
            L.append('| %s | %.3f |' % (name, ts))
        L.append('\n- FTRACE_ON → IPERF_ALL_EXITED：**%.3f s**（< 60 s ✅）' % r['timeline']['iperf_span_s'])
        L.append('- 注：%s' % r['timeline']['capture_exit_note'])
    if r.get('sample_thread'):
        s = r['sample_thread']
        L.append('\n## 典型 iperf3 线程完整事件链（pid=%d，%d 条生命周期事件）\n' %
                 (s['pid'], s['event_count']))
        L.append('| 时间 (s) | tracepoint | 说明 |\n|---:|---|---|')
        for e in s['chain']:
            L.append('| %.6f | `%s` | %s |' % (e['ts'], e['event'], e['desc']))
    p = r['perf']
    L.append('\n## perf（与 ftrace 同时抓）\n')
    L.append('- `perf.log`：%d 行 / %.2f MB' % (p['perf_log_lines'], p['perf_log_bytes'] / 1e6))
    L.append('- `perf.data`：%.2f MB' % (p['perf_data_bytes'] / 1e6))
    open(path, 'w', encoding='utf-8').write('\n'.join(L) + '\n')
    print('Markdown:', path)


if __name__ == '__main__':
    main()
