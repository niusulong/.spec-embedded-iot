#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Large Log Analyzer - 大日志分析工具
用于分析百万行级别的嵌入式系统日志文件
"""

import re
import os
import sys
from datetime import datetime
from collections import defaultdict

# ============ 时间解析 ============

def parse_log_time(line):
    """解析日志行的时间戳，返回 (日期部分, 时间部分) 用于跨天排序"""
    # 格式1: 26-04-20 20:42:51.760
    match = re.match(r'(\d{2}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}\.\d+)', line)
    if match:
        return match.group(1), match.group(2)
    # 格式2: 2026-04-20 20:42:51.760
    match = re.match(r'(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2}\.\d+)', line)
    if match:
        return match.group(1), match.group(2)
    # 格式3: 20:42:51.760
    match = re.match(r'^(\d{2}:\d{2}:\d{2}\.\d+)', line)
    if match:
        return None, match.group(1)
    return None, None


def time_to_ms(date_str, time_str):
    """将日期+时间转换为毫秒数，支持跨天排序"""
    try:
        parts = time_str.split(':')
        h, m = int(parts[0]), int(parts[1])
        s_parts = parts[2].split('.')
        s = int(s_parts[0])
        ms = int(s_parts[1]) if len(s_parts) > 1 else 0
        day_offset = 0
        if date_str:
            # 简单哈希日期为天数偏移，保证跨天排序正确
            cleaned = date_str.replace('-', '')
            try:
                day_offset = int(cleaned) % 10000
            except ValueError:
                day_offset = 0
        return (day_offset * 86400 + h * 3600 + m * 60 + s) * 1000 + ms
    except Exception:
        return 0


# ============ 日志提取（流式） ============

def extract_segment(input_file, output_file, start_time=None, end_time=None,
                    start_line=0, end_line=None, keywords=None):
    """流式提取指定时间段和关键字的日志片段"""
    lines = []
    start_ms = time_to_ms(None, start_time) if start_time else 0
    end_ms = time_to_ms(None, end_time) if end_time else float('inf')

    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f):
            if i < start_line:
                continue
            if end_line is not None and i > end_line:
                break

            date_part, time_part = parse_log_time(line)
            in_time_range = True
            if time_part and start_time and end_time:
                current_ms = time_to_ms(date_part, time_part)
                in_time_range = start_ms <= current_ms <= end_ms

            match_keyword = True
            if keywords:
                match_keyword = any(kw.lower() in line.lower() for kw in keywords)

            if in_time_range and match_keyword:
                lines.append((i, time_part, line.rstrip()))

    if output_file:
        os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            for _, _, line in lines:
                f.write(line + '\n')

    return lines


# ============ 关键字搜索（流式） ============

def search_keywords(input_file, keywords, time_range=None, line_range=None,
                    context_lines=0, max_results=1000):
    """流式搜索关键字，避免全量加载大文件"""
    results = []
    keywords = [k.lower() for k in keywords] if isinstance(keywords, list) else [keywords.lower()]

    start_ms = time_to_ms(None, time_range[0]) if time_range else 0
    end_ms = time_to_ms(None, time_range[1]) if time_range else float('inf')
    start_line = line_range[0] if line_range else 0
    end_line = line_range[1] if line_range else float('inf')

    # 上下文需要缓冲，用滑动窗口
    context_buffer = []
    buffer_size = context_lines + 1 if context_lines > 0 else 0

    with open(input_file, 'r', encoding='utf-8', errors='ignore') as f:
        for i, line in enumerate(f):
            if i < start_line or i > end_line:
                continue

            # 维护上下文滑动窗口
            if buffer_size > 0:
                context_buffer.append((i, line.rstrip()[:200]))
                if len(context_buffer) > buffer_size:
                    context_buffer.pop(0)

            date_part, time_part = parse_log_time(line)
            if time_range and time_part:
                current_ms = time_to_ms(date_part, time_part)
                if current_ms < start_ms or current_ms > end_ms:
                    continue

            if any(kw in line.lower() for kw in keywords):
                result = {
                    'line_no': i,
                    'time': time_part,
                    'content': line.rstrip()[:300],
                    'context': []
                }
                # 前置上下文从 buffer 取
                if context_lines > 0:
                    for j, ctx_line in context_buffer[:-1]:
                        result['context'].append((j, ctx_line))
                # 后置上下文需要额外读取
                if context_lines > 0:
                    for _ in range(context_lines):
                        try:
                            next_line = next(f)
                            i += 1
                            result['context'].append((i, next_line.rstrip()[:200]))
                        except StopIteration:
                            break
                results.append(result)
                if len(results) >= max_results:
                    break

    return results


# ============ 事件统计 ============

def analyze_events(input_file, keywords, time_range=None):
    """统计关键字出现次数和时间分布"""
    stats = defaultdict(lambda: {'count': 0, 'times': []})

    results = search_keywords(input_file, keywords, time_range)
    for r in results:
        for kw in keywords:
            if kw.lower() in r['content'].lower():
                stats[kw]['count'] += 1
                if r['time']:
                    stats[kw]['times'].append(r['time'])

    return dict(stats)


# ============ 流程对比（支持同文件/不同文件） ============

def compare_flows(normal_source, abnormal_source, keywords,
                  normal_time_range=None, abnormal_time_range=None,
                  normal_line_range=None, abnormal_line_range=None):
    """
    对比正常流程和异常流程。
    支持两种模式：
    - 同一文件：normal_source == abnormal_source，用 time_range/line_range 区分
    - 不同文件：normal_source 和 abnormal_source 为不同日志文件
    """
    normal_events = search_keywords(normal_source, keywords,
                                    normal_time_range, normal_line_range)
    abnormal_events = search_keywords(abnormal_source, keywords,
                                      abnormal_time_range, abnormal_line_range)

    normal_set = set(e['content'][:80] for e in normal_events)
    abnormal_set = set(e['content'][:80] for e in abnormal_events)

    return {
        'normal_count': len(normal_events),
        'abnormal_count': len(abnormal_events),
        'only_in_normal': list(normal_set - abnormal_set),
        'only_in_abnormal': list(abnormal_set - normal_set),
        'common': list(normal_set & abnormal_set)
    }


# ============ CLI 入口 ============

def main():
    import argparse
    parser = argparse.ArgumentParser(description='大日志分析工具')
    subparsers = parser.add_subparsers(dest='command', help='子命令')

    # extract 子命令
    p_extract = subparsers.add_parser('extract', help='提取日志片段')
    p_extract.add_argument('input', help='输入日志文件')
    p_extract.add_argument('-o', '--output', required=True, help='输出文件')
    p_extract.add_argument('-k', '--keywords', nargs='+', help='搜索关键字')
    p_extract.add_argument('--start-time', help='开始时间 (HH:MM:SS.mmm)')
    p_extract.add_argument('--end-time', help='结束时间 (HH:MM:SS.mmm)')
    p_extract.add_argument('--start-line', type=int, default=0, help='开始行号')
    p_extract.add_argument('--end-line', type=int, help='结束行号')

    # search 子命令
    p_search = subparsers.add_parser('search', help='搜索关键字')
    p_search.add_argument('input', help='输入日志文件')
    p_search.add_argument('-k', '--keywords', nargs='+', required=True, help='搜索关键字')
    p_search.add_argument('--start-time', help='开始时间')
    p_search.add_argument('--end-time', help='结束时间')
    p_search.add_argument('--start-line', type=int, default=0)
    p_search.add_argument('--end-line', type=int)
    p_search.add_argument('-c', '--context', type=int, default=0, help='上下文行数')
    p_search.add_argument('--max-results', type=int, default=1000)
    p_search.add_argument('--report', help='生成报告文件路径')

    # stats 子命令
    p_stats = subparsers.add_parser('stats', help='事件统计')
    p_stats.add_argument('input', help='输入日志文件')
    p_stats.add_argument('-k', '--keywords', nargs='+', required=True, help='统计关键字')
    p_stats.add_argument('--start-time', help='开始时间')
    p_stats.add_argument('--end-time', help='结束时间')
    p_stats.add_argument('--report', help='生成报告文件路径。推荐 .spec/bug/{id}_{desc}/analysis/log_report.md')

    # compare 子命令
    p_compare = subparsers.add_parser('compare', help='流程对比')
    p_compare.add_argument('normal', help='正常日志文件')
    p_compare.add_argument('abnormal', help='异常日志文件')
    p_compare.add_argument('-k', '--keywords', nargs='+', required=True, help='对比关键字')
    p_compare.add_argument('--normal-start-time', help='正常日志开始时间')
    p_compare.add_argument('--normal-end-time', help='正常日志结束时间')
    p_compare.add_argument('--abnormal-start-time', help='异常日志开始时间')
    p_compare.add_argument('--abnormal-end-time', help='异常日志结束时间')
    p_compare.add_argument('--report', help='生成报告文件路径。推荐 .spec/bug/{id}_{desc}/analysis/log_report.md')

    args = parser.parse_args()

    if args.command == 'extract':
        lines = extract_segment(args.input, args.output, args.start_time, args.end_time,
                                args.start_line, args.end_line, args.keywords)
        print(f"提取了 {len(lines)} 行到 {args.output}")

    elif args.command == 'search':
        time_range = None
        if args.start_time and args.end_time:
            time_range = (args.start_time, args.end_time)
        line_range = None
        if args.start_line or args.end_line:
            line_range = (args.start_line, args.end_line or float('inf'))

        results = search_keywords(args.input, args.keywords, time_range,
                                  line_range, args.context, args.max_results)
        print(f"找到 {len(results)} 个匹配项")
        for r in results[:50]:
            print(f"[{r['line_no']}] {r['time'] or ''} {r['content'][:100]}")

        if args.report:
            from datetime import datetime
            with open(args.report, 'w', encoding='utf-8') as f:
                f.write(f"# 日志搜索报告\n\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"## 关键事件\n\n")
                f.write("| 行号 | 时间 | 内容 |\n")
                f.write("|------|------|------|\n")
                for r in results[:100]:
                    f.write(f"| {r['line_no']} | {r['time'] or '-'} | {r['content'][:100]} |\n")
            print(f"报告已生成: {args.report}")

    elif args.command == 'stats':
        time_range = None
        if args.start_time and args.end_time:
            time_range = (args.start_time, args.end_time)
        stats = analyze_events(args.input, args.keywords, time_range)
        print("关键字统计:")
        for kw, data in stats.items():
            times = data.get('times', [])
            first = times[0] if times else '-'
            last = times[-1] if times else '-'
            print(f"  {kw}: {data['count']}次, 首次={first}, 最后={last}")

        if args.report:
            from datetime import datetime
            with open(args.report, 'w', encoding='utf-8') as f:
                f.write(f"# 日志统计报告\n\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"## 关键字统计\n\n")
                f.write("| 关键字 | 次数 | 首次出现 | 最后出现 |\n")
                f.write("|--------|------|----------|----------|\n")
                for kw, data in stats.items():
                    times = data.get('times', [])
                    first = times[0] if times else '-'
                    last = times[-1] if times else '-'
                    f.write(f"| {kw} | {data['count']} | {first} | {last} |\n")
            print(f"报告已生成: {args.report}")

    elif args.command == 'compare':
        normal_range = None
        if args.normal_start_time and args.normal_end_time:
            normal_range = (args.normal_start_time, args.normal_end_time)
        abnormal_range = None
        if args.abnormal_start_time and args.abnormal_end_time:
            abnormal_range = (args.abnormal_start_time, args.abnormal_end_time)

        result = compare_flows(args.normal, args.abnormal, args.keywords,
                               normal_range, abnormal_range)
        print(f"正常流程: {result['normal_count']} 个事件")
        print(f"异常流程: {result['abnormal_count']} 个事件")
        print(f"共同事件: {len(result['common'])} 个")
        print(f"\n仅在正常流程中出现 ({len(result['only_in_normal'])} 个):")
        for item in result['only_in_normal'][:20]:
            print(f"  + {item}")
        print(f"\n仅在异常流程中出现 ({len(result['only_in_abnormal'])} 个):")
        for item in result['only_in_abnormal'][:20]:
            print(f"  - {item}")

        if args.report:
            from datetime import datetime
            with open(args.report, 'w', encoding='utf-8') as f:
                f.write(f"# 日志对比报告\n\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write(f"- 正常日志: `{args.normal}`\n")
                f.write(f"- 异常日志: `{args.abnormal}`\n\n")
                f.write(f"## 统计\n\n")
                f.write(f"- 正常流程: {result['normal_count']} 个事件\n")
                f.write(f"- 异常流程: {result['abnormal_count']} 个事件\n")
                f.write(f"- 共同事件: {len(result['common'])} 个\n\n")
                f.write(f"## 仅在异常流程中出现（{len(result['only_in_abnormal'])} 个）\n\n")
                for item in result['only_in_abnormal'][:50]:
                    f.write(f"- {item}\n")
                f.write(f"\n## 仅在正常流程中出现（{len(result['only_in_normal'])} 个）\n\n")
                for item in result['only_in_normal'][:50]:
                    f.write(f"- {item}\n")
            print(f"报告已生成: {args.report}")

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
