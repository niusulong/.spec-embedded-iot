#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mem_leak_analyzer.py — 内存泄漏日志分析（call-stack 追踪法）

解析带调用者地址（caller）的分配/释放日志，按内存地址配对，定位"只分配不释放"
的内存块及其调用者位置；可选地用 MAP 文件把 caller 地址映射到函数名，并输出
可用堆趋势。仅依赖 Python 标准库。

标准日志格式（详见 references/call-stack-tracking-guide.md）：
    MEM_ALLOC: addr=0x3a4b0, size=128, caller=0x60102169
    MEM_FREE:  addr=0x3a4b0, caller=0x60102169
    MEM_HEAP:  total=67168, free=23008, used=44160
兼容旧式 MALLOC: / MFREE: 格式；字段顺序无关（按 key=value 解析）。

用法：
    python scripts/mem_leak_analyzer.py <log> [--map MAP] [--heap]
                                         [--top N] [--report OUT] [--encoding ENC]
示例：
    python scripts/mem_leak_analyzer.py aplog.trc --map fw.map --report leak.md
    python scripts/mem_leak_analyzer.py aplog.trc --heap --top 30
"""
import argparse
import bisect
import re
import sys
from collections import defaultdict

# 匹配 key=value，值可为十六进制(0x...)或十进制
_KV = re.compile(r'(\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)')
# 行首标签（兼容 MEM_ALLOC / MALLOC 两种写法）
_TAG = re.compile(r'^\s*(MEM_ALLOC|MALLOC|MEM_FREE|MFREE|MEM_HEAP|HEAP)\b', re.I)


def _to_int(s):
    """'0x3a4b0' -> 0x3a4b0; '128' -> 128; None -> None"""
    if s is None:
        return None
    s = s.strip()
    return int(s, 16) if s.lower().startswith('0x') else int(s, 10)


def _norm_tag(tag):
    t = tag.upper()
    if t in ('MEM_ALLOC', 'MALLOC'):
        return 'alloc'
    if t in ('MEM_FREE', 'MFREE'):
        return 'free'
    if t in ('MEM_HEAP', 'HEAP'):
        return 'heap'
    return None


def parse_log(lines):
    """逐行解析，返回事件流。

    每个事件为元组：
      ('alloc', addr, size, caller, lineno)
      ('free',  addr, lineno)
      ('heap',  free, lineno)
    """
    events = []
    for i, line in enumerate(lines, 1):
        m = _TAG.match(line)
        if not m:
            continue
        kind = _norm_tag(m.group(1))
        kv = dict(_KV.findall(line))
        if kind == 'alloc':
            addr = kv.get('addr') or kv.get('a')
            size = kv.get('size') or kv.get('sz')
            caller = kv.get('caller') or kv.get('ra')
            addr, size, caller = _to_int(addr), _to_int(size), _to_int(caller)
            if addr is not None and size is not None and caller is not None:
                events.append(('alloc', addr, size, caller, i))
        elif kind == 'free':
            addr = _to_int(kv.get('addr') or kv.get('a'))
            if addr is not None:
                events.append(('free', addr, i))
        elif kind == 'heap':
            free = _to_int(kv.get('free'))
            if free is not None:
                events.append(('heap', free, i))
    return events


def pair_events(events):
    """按地址配对 alloc/free。

    返回 (leaked_records, stats)。
    处理地址复用：若同一地址在未释放前被再次 alloc，旧记录计入泄漏。
    """
    outstanding = {}          # addr -> record
    leaked = []
    alloc_n = free_n = mismatch = 0
    for ev in events:
        if ev[0] == 'alloc':
            _, addr, size, caller, ln = ev
            alloc_n += 1
            if addr in outstanding:
                leaked.append(outstanding[addr])     # 旧块未释放即被覆盖
            outstanding[addr] = {'addr': addr, 'size': size, 'caller': caller, 'lineno': ln}
        elif ev[0] == 'free':
            _, addr, ln = ev
            free_n += 1
            if addr in outstanding:
                del outstanding[addr]
            else:
                mismatch += 1                         # 释放无对应分配（外部/追踪盲区）
    leaked.extend(outstanding.values())
    stats = {'alloc': alloc_n, 'free': free_n, 'mismatch': mismatch,
             'outstanding': len(outstanding)}
    return leaked, stats


def aggregate(leaked):
    """按 caller 聚合，返回 [(caller, {'count','bytes'}), ...] 按字节降序。"""
    by_caller = defaultdict(lambda: {'count': 0, 'bytes': 0})
    for r in leaked:
        c = by_caller[r['caller']]
        c['count'] += 1
        c['bytes'] += r['size']
    return sorted(by_caller.items(), key=lambda kv: kv[1]['bytes'], reverse=True)


def load_map(path):
    """启发式解析 MAP 文件，返回排序去重的 [(start, end, name), ...]。

    支持 GCC/ld 与多数 ARM/IAR 格式：行内含 名称 + 0x起始地址 + 0x大小。
    注意：精确的地址→行号定位应以 addr2line 为准，本解析仅供快速定位函数。
    """
    pat = re.compile(r'(\.?[\w$_.+-]+)\s+(0x[0-9a-fA-F]+)\s+(0x[0-9a-fA-F]+)')
    ranges = []
    seen = set()
    with open(path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            m = pat.search(line)
            if not m:
                continue
            name, s, sz = m.group(1), m.group(2), m.group(3)
            start, size = int(s, 16), int(sz, 16)
            if size == 0 or start == 0:
                continue
            key = (start, name)
            if key in seen:
                continue
            seen.add(key)
            ranges.append((start, start + size, name))
    ranges.sort(key=lambda r: r[0])
    return ranges


def build_resolver(ranges):
    """根据 MAP 区间构造 addr->name 解析函数。"""
    if not ranges:
        return lambda addr: None
    starts = [r[0] for r in ranges]
    ends = [r[1] for r in ranges]
    names = [r[2] for r in ranges]

    def resolve(addr):
        i = bisect.bisect_right(starts, addr) - 1
        if i >= 0 and starts[i] <= addr < ends[i]:
            return names[i]
        return None
    return resolve


def heap_trend(events):
    """返回 [(lineno, free), ...]。"""
    return [(ev[2], ev[1]) for ev in events if ev[0] == 'heap']


def fmt_addr(a):
    return '0x%08x' % a


def print_summary(leaked, stats, ranking, resolve_fn, heap_pts, top):
    total_bytes = sum(r['size'] for r in leaked)
    rate = (len(leaked) / stats['alloc'] * 100.0) if stats['alloc'] else 0.0
    print('=' * 64)
    print('内存泄漏分析结果')
    print('=' * 64)
    print(f'分配次数(MEM_ALLOC): {stats["alloc"]}')
    print(f'释放次数(MEM_FREE) : {stats["free"]}')
    print(f'无对应分配的释放    : {stats["mismatch"]}  (>0 提示存在追踪盲区/替换不全)')
    print(f'泄漏块数           : {len(leaked)}')
    print(f'泄漏总量           : {total_bytes} 字节')
    if stats['alloc']:
        print(f'泄漏率(按分配次数) : {rate:.1f}%')
    print()

    if not leaked:
        print('未检测到内存泄漏。')
        return

    print(f'按调用者(caller)排名 Top {min(top, len(ranking))}：')
    print('-' * 64)
    print(f'{"caller":<12}{"映射函数":<34}{"块数":>6}{"字节":>10}')
    print('-' * 64)
    for caller, info in ranking[:top]:
        sym = resolve_fn(caller) if resolve_fn else None
        sym_str = sym if sym else '(未映射，需 MAP/addr2line)'
        if len(sym_str) > 32:
            sym_str = sym_str[:31] + '…'
        print(f'{fmt_addr(caller):<12}{sym_str:<34}{info["count"]:>6}{info["bytes"]:>10}')
    print('-' * 64)

    if heap_pts:
        first, last = heap_pts[0][1], heap_pts[-1][1]
        lo = min(p[1] for p in heap_pts)
        print()
        print(f'可用堆趋势: 共 {len(heap_pts)} 个采样点')
        print(f'  首次 free={first}  末次 free={last}  变化 {last - first:+d}  最低 free={lo}')
        if last < first:
            print('  [!] 可用堆整体下降，符合泄漏特征。')


def write_report(path, log_name, map_name, leaked, stats, ranking, resolve_fn, heap_pts, top):
    total_bytes = sum(r['size'] for r in leaked)
    rate = (len(leaked) / stats['alloc'] * 100.0) if stats['alloc'] else 0.0
    L = []
    L.append('# 内存泄漏分析报告')
    L.append('')
    L.append(f'- 日志文件: `{log_name}`')
    if map_name:
        L.append(f'- MAP 文件: `{map_name}`')
    L.append('')
    L.append('## 概览')
    L.append('')
    L.append(f'| 项目 | 值 |')
    L.append(f'|------|-----|')
    L.append(f'| 分配次数 | {stats["alloc"]} |')
    L.append(f'| 释放次数 | {stats["free"]} |')
    L.append(f'| 无对应分配的释放 | {stats["mismatch"]} |')
    L.append(f'| 泄漏块数 | {len(leaked)} |')
    L.append(f'| 泄漏总量 | {total_bytes} 字节 |')
    L.append(f'| 泄漏率(按分配次数) | {rate:.1f}% |')
    L.append('')

    if not leaked:
        L.append('未检测到内存泄漏。')
    else:
        L.append(f'## 按 caller 排名 Top {min(top, len(ranking))}')
        L.append('')
        L.append('| caller | 映射函数 | 块数 | 字节 |')
        L.append('|--------|----------|------|------|')
        for caller, info in ranking[:top]:
            sym = resolve_fn(caller) if resolve_fn else None
            L.append(f'| {fmt_addr(caller)} | {sym or "(未映射)"} | {info["count"]} | {info["bytes"]} |')
        L.append('')

        L.append('## 泄漏块明细（全部）')
        L.append('')
        L.append('| 地址 | size | caller | 映射函数 | 日志行 |')
        L.append('|------|------|--------|----------|--------|')
        for r in leaked:
            sym = resolve_fn(r['caller']) if resolve_fn else None
            L.append(f'| {fmt_addr(r["addr"])} | {r["size"]} | {fmt_addr(r["caller"])} | {sym or "(未映射)"} | {r["lineno"]} |')
        L.append('')

    if heap_pts:
        first, last = heap_pts[0][1], heap_pts[-1][1]
        lo = min(p[1] for p in heap_pts)
        L.append('## 可用堆趋势')
        L.append('')
        L.append(f'共 {len(heap_pts)} 个采样点；首次 free={first}，末次 free={last}，'
                 f'变化 {last - first:+d}，最低 free={lo}。')
        L.append('')
        # 采样点过多时抽稀，最多列 40 个
        step = max(1, len(heap_pts) // 40)
        L.append('| 行号 | free heap |')
        L.append('|------|-----------|')
        for ln, fr in heap_pts[::step]:
            L.append(f'| {ln} | {fr} |')
        L.append('')

    L.append('## 定位建议')
    L.append('')
    L.append('- 未映射的 caller 地址，用 `addr2line -e <固件.elf> -f <addr>` 取函数名+行号（最权威）。')
    L.append('- 命中 `.part.`/`.isra.` 后缀表示被内联，查 MAP 中附近符号找真实调用者。')
    L.append('- 若"无对应分配的释放"较多，说明仍有 malloc/free 未替换为追踪接口，需复查 Step 4。')
    L.append('')

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(L))


def main(argv=None):
    # Windows 控制台默认 GBK，遇到非常规字符(emoji 等)会 UnicodeEncodeError；统一容错
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors='replace')
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description='内存泄漏日志分析（call-stack 追踪法）：配对 alloc/free，按 caller 聚合，'
                    '可选 MAP 映射与堆趋势。',
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('log', help='带 MEM_ALLOC/MEM_FREE/MEM_HEAP 追踪的日志文件')
    ap.add_argument('--map', metavar='MAP', help='MAP 文件（地址→符号映射，可选）')
    ap.add_argument('--heap', action='store_true', help='在 stdout 打印完整堆趋势采样点')
    ap.add_argument('--top', type=int, default=20, help='caller 排名显示条数（默认 20）')
    ap.add_argument('--report', metavar='OUT', help='同时输出 markdown 报告到该文件')
    ap.add_argument('--encoding', default='utf-8', help='日志文件编码（默认 utf-8，自动忽略非法字节）')
    args = ap.parse_args(argv)

    try:
        with open(args.log, 'r', encoding=args.encoding, errors='ignore') as f:
            lines = f.readlines()
    except OSError as e:
        print(f'无法读取日志 {args.log}: {e}', file=sys.stderr)
        return 2

    events = parse_log(lines)
    if not events:
        print(f'未在 {args.log} 中识别到 MEM_ALLOC/MEM_FREE/MEM_HEAP 追踪记录。\n'
              f'请确认固件已按 references/call-stack-tracking-guide.md 埋点并烧录。',
              file=sys.stderr)
        return 3

    leaked, stats = pair_events(events)
    ranking = aggregate(leaked)

    resolve_fn = None
    if args.map:
        try:
            resolve_fn = build_resolver(load_map(args.map))
        except OSError as e:
            print(f'警告: 无法读取 MAP {args.map}: {e}（将不进行地址映射）', file=sys.stderr)

    heap_pts = heap_trend(events)

    print_summary(leaked, stats, ranking, resolve_fn, heap_pts, args.top)
    if args.heap and heap_pts:
        print()
        print('完整堆趋势采样点：')
        for ln, fr in heap_pts:
            print(f'  行{ln}: free={fr}')

    if args.report:
        write_report(args.report, args.log, args.map, leaked, stats,
                     ranking, resolve_fn, heap_pts, args.top)
        print(f'\n报告已写入: {args.report}')

    return 0


if __name__ == '__main__':
    sys.exit(main())
