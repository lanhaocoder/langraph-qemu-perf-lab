#!/usr/bin/env python3.11
"""把本轮真实 ftrace 数据画成「iperf 进程完整生命周期甘特图」。

每个 iperf3 pid 一条横道：创建(fork/exec) → exit → free，
叠加 ftrace 采集窗口边界，直观证明所有生命周期都被完整覆盖。

分组（来自真实 fork 父 pid）：
  pid 361-380 : guest server 主进程(131) 创建的 20 个工作线程（接 host client 的 20 流）
  pid 383-402 : guest client 主进程(381) 创建的 20 个工作线程（打向 host server）
  pid 131/381 : 两个主进程

用法: python3.11 draw_lifecycle_gantt.py <ftrace.log> -o out/lifecycle-gantt.png
"""
import argparse
import os
import re
from collections import Counter, defaultdict

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
from matplotlib.patches import Patch

# matplotlib 默认不登记 Noto CJK，中文会渲染成方框。手动加载 TTC
# （首个 face 名为 ...CJK JP，字形覆盖简体中文），否则图例/标题全是缺字。
_CJK = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
if os.path.isfile(_CJK):
    try:
        fm.fontManager.addfont(_CJK)
    except Exception:
        pass
plt.rcParams['font.sans-serif'] = ['Noto Sans CJK JP', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

LINE = re.compile(
    r'^\s*(?P<comm>.+?)-(?P<pid>\d+)\s+\((?P<tgid>[^)]*)\)\s+'
    r'\[(?P<cpu>\d+)\]\s+(?P<flags>\S+)\s+(?P<ts>\d+\.\d+):\s+'
    r'(?P<event>[A-Za-z_][A-Za-z0-9_]*):\s*(?P<rest>.*)$'
)
KV = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)=([^\s\]]+)')


def parse(path):
    tmin = tmax = None
    forks = {}       # child_pid -> (ts, child_comm, parent_pid)
    execs = {}       # pid -> ts
    exits = {}       # pid -> ts
    frees = {}       # pid -> ts
    wake_new = Counter()
    switch_in = Counter()
    with open(path, errors='replace') as f:
        for ln in f:
            m = LINE.match(ln.rstrip('\n'))
            if not m:
                continue
            ts = float(m['ts'])
            tmin = ts if tmin is None else min(tmin, ts)
            tmax = ts if tmax is None else max(tmax, ts)
            ev = m['event']
            d = dict(KV.findall(m['rest']))
            row_comm, row_pid = m['comm'], int(m['pid'])
            if ev == 'sched_process_fork':
                cp = d.get('child_pid', '')
                if cp.isdigit():
                    # fork 在父进程上下文触发；args 里是 pid(=父) / child_pid，无 parent_pid
                    forks[int(cp)] = (ts, d.get('child_comm', ''), row_pid)
            elif ev == 'sched_process_exec':
                if 'iperf3' in d.get('filename', '') or 'iperf3' in row_comm:
                    execs[row_pid] = ts
            elif ev == 'sched_process_exit':
                if 'iperf3' in row_comm or 'iperf3' in d.get('comm', ''):
                    p = d.get('pid')
                    exits[int(p) if p and p.isdigit() else row_pid] = ts
            elif ev == 'sched_process_free':
                if 'iperf3' in d.get('comm', ''):
                    p = d.get('pid')
                    if p and p.isdigit():
                        frees[int(p)] = ts
            elif ev == 'sched_wakeup_new':
                if 'iperf3' in d.get('comm', '') or 'iperf3' in row_comm:
                    p = d.get('pid')
                    if p and p.isdigit():
                        wake_new[int(p)] += 1
            elif ev == 'sched_switch':
                np = d.get('next_pid', '')
                if np.isdigit() and int(np) != 0:
                    switch_in[int(np)] += 1
    return tmin, tmax, forks, execs, exits, frees, wake_new, switch_in


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('ftrace')
    ap.add_argument('-o', '--output', default='lifecycle-gantt.png')
    a = ap.parse_args()

    tmin, tmax, forks, execs, exits, frees, wake_new, switch_in = parse(a.ftrace)
    iperf_pids = set(execs) | set(exits) | set(frees) | {
        p for p, (_, cc, _) in forks.items() if 'iperf3' in cc}

    # 工作线程按 fork 的 parent_pid 分成两组：先创建的主进程 = guest server。
    # 不硬编码 pid（每轮 QEMU 的 pid 都不同）。
    work_forks = {p: fk for p, fk in forks.items() if 'iperf3' in (fk[1] or '')}
    parents = sorted({fk[2] for fk in work_forks.values()})
    srv_parent = parents[0] if parents else None
    cli_parent = parents[1] if len(parents) > 1 else None

    rows = []
    for p in sorted(iperf_pids):
        fk = forks.get(p, (None, None, None))
        cr = min([x for x in (fk[0], execs.get(p)) if x is not None], default=None)
        ex, fr = exits.get(p), frees.get(p)
        if cr is None or fr is None:
            continue
        parent = fk[2]
        # 主进程：fork 时 child_comm 继承 init，靠 exec(filename=/bin/iperf3) 识别
        if p in execs or 'iperf3' not in (fk[1] or ''):
            grp = 'main'
        else:
            grp = 'srv' if parent == srv_parent else 'cli'
        rows.append(dict(pid=p, create=cr, exit=ex, free=fr, grp=grp,
                         parent=parent, sw=switch_in.get(p, 0),
                         closed=(ex is not None)))

    # 排序：主进程在上，其次 server 20 线程，再 client 20 线程
    order = {'main': 0, 'srv': 1, 'cli': 2}
    rows.sort(key=lambda r: (order[r['grp']], r['pid']))
    y = {r['pid']: i for i, r in enumerate(rows)}

    colors = {'main': '#f2a33c', 'srv': '#4ea8de', 'cli': '#63c98a'}

    def rng(ps):
        return '%d-%d' % (min(ps), max(ps)) if ps else '-'

    srv_pids = sorted(p for p, fk in work_forks.items() if fk[2] == srv_parent)
    cli_pids = sorted(p for p, fk in work_forks.items() if fk[2] == cli_parent)
    main_pids = sorted(r['pid'] for r in rows if r['grp'] == 'main')
    labels = {
        'main': '主进程 (%s)' % ', '.join(map(str, main_pids)),
        'srv': 'Guest server 的 %d 个工作线程 (%s)' % (len(srv_pids), rng(srv_pids)),
        'cli': 'Guest client 的 %d 个工作线程 (%s)' % (len(cli_pids), rng(cli_pids)),
    }

    fig, ax = plt.subplots(figsize=(15, 12), facecolor='#0d1117')
    ax.set_facecolor('#0d1117')
    span = tmax - tmin
    for r in rows:
        yy = y[r['pid']]
        x0 = r['create'] - tmin
        x1 = r['free'] - tmin
        c = colors[r['grp']]
        ax.barh(yy, max(x1 - x0, 0.004), left=x0, height=0.62,
                color=c, alpha=0.82, edgecolor='none', zorder=3)
        if r['exit'] is not None:
            xe = r['exit'] - tmin
            ax.plot([xe, xe], [yy - 0.34, yy + 0.34], color='#ff6b6b',
                    lw=1.5, zorder=5)
        # 创建点
        ax.plot([x0], [yy], marker='|', color='#ffffff', ms=9, zorder=6)
        ax.text(-0.28, yy, str(r['pid']), ha='right', va='center',
                fontsize=7.2, color='#8b949e', family='monospace')

    # ftrace 窗口边界
    ax.axvline(0, color='#e6c07b', lw=1.2, ls='--', alpha=0.8, zorder=2)
    ax.axvline(span, color='#e6c07b', lw=1.2, ls='--', alpha=0.8, zorder=2)
    first_create = min(r['create'] for r in rows) - tmin
    last_free = max(r['free'] for r in rows) - tmin
    ax.axvspan(first_create, last_free, color='#4ea8de', alpha=0.055, zorder=1)
    ax.text(first_create, len(rows) + 0.6, '最早 fork/exec', color='#8b949e',
            fontsize=8.5, ha='center')
    ax.text(last_free, len(rows) + 0.6, '最晚 free', color='#8b949e',
            fontsize=8.5, ha='center')

    ax.set_xlim(-1.6, span + 1.0)
    ax.set_ylim(-1.2, len(rows) + 1.8)
    ax.set_xlabel('相对 ftrace 采集窗口起点的时间 (s)', color='#c9d1d9', fontsize=11)
    ax.set_title('iperf3 进程完整生命周期  |  fork/exec → switch → exit → free\n'
                 '（本轮真实 ftrace 数据，共 %d 个 pid，窗口 %.3f s）' % (len(rows), span),
                 color='#e6edf3', fontsize=14, pad=22)
    ax.tick_params(colors='#8b949e', labelsize=9)
    for sp in ax.spines.values():
        sp.set_color('#30363d')
    ax.grid(axis='x', color='#21262d', lw=0.7, zorder=0)

    handles = [Patch(facecolor=colors[k], alpha=0.82, label=labels[k])
               for k in ('main', 'srv', 'cli') if any(r['grp'] == k for r in rows)]
    handles += [plt.Line2D([0], [0], color='#ff6b6b', lw=1.6, label='sched_process_exit'),
                plt.Line2D([0], [0], color='#e6c07b', lw=1.2, ls='--', label='ftrace 采集窗口边界')]
    ax.legend(handles=handles, loc='upper right', frameon=False,
              fontsize=9, labelcolor='#c9d1d9')
    # 统计行放在图外底部，避免压住最下面那条横道
    fig.text(0.5, 0.012,
             '起点余量 %.3f s（ftrace 先行）  ·  终点余量 %.3f s（iperf 先结束）  ·  闭合 %d/%d'
             % (first_create, span - last_free, sum(1 for r in rows if r['closed']), len(rows)),
             ha='center', color='#7ee787', fontsize=10)
    fig.tight_layout(rect=[0, 0.035, 1, 1])
    fig.savefig(a.output, dpi=130, facecolor='#0d1117')
    print('PNG:', a.output, ' pid 数:', len(rows), ' 窗口: %.3f s' % span)
    print('分组:', dict(Counter(r['grp'] for r in rows)))
    print('起点余量 %.3f s / 终点余量 %.3f s' % (first_create, span - last_free))


if __name__ == '__main__':
    main()
