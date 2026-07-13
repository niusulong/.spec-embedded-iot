#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
调用链峰值栈深度分析工具。

分析指定函数的调用图，计算从该函数出发的最深调用链的峰值栈用量。

Usage:
    python stack_analysis.py <axf_file> <map_file> --func <function_name>
    python stack_analysis.py <axf_file> <map_file> --addr 0x7e6f7255 --size 840

Example:
    python stack_analysis.py firmware.axf firmware.map --func fota_trigger_worker_thread
    python stack_analysis.py firmware.axf firmware.map --addr 0x7e6f7255 --size 840 --depth 5
"""

import struct
import re
import argparse
import sys
import os

# Import shared utilities
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_map_file as _parse_map_file, parse_elf_sections, find_elf_section, read_elf_bytes


# ---------------------------------------------------------------------------
# MAP file parser — wraps common.py with lightweight adapter for this script
# ---------------------------------------------------------------------------

def parse_map_file(map_path):
    """Parse ARM RVCT/ARMCC MAP file, return sorted list of (addr, name, size, section, is_thumb)."""
    entries, code_addrs = _parse_map_file(map_path)
    # Adapt to (addr, name, size, section, is_thumb) format used internally
    return [(e[0] & ~1, e[1], e[2], e[3], e[4]) for e in entries]


def lookup(symbols, target_addr):
    """Find the function containing target_addr using binary search."""
    if not symbols:
        return None
    import bisect
    addrs = [s[0] for s in symbols]
    idx = bisect.bisect_right(addrs, target_addr)
    if idx == 0:
        return None
    entry = symbols[idx - 1]
    addr, name, size = entry[0], entry[1], entry[2]
    if addr <= target_addr < addr + size:
        return (name, addr, size)
    return None


def find_by_name(symbols, pattern, exact=False):
    """Find function by name pattern. Returns first match."""
    for addr, name, size in symbols:
        if exact:
            if name == pattern:
                return (name, addr, size)
        else:
            if pattern in name:
                return (name, addr, size)
    return None


# ---------------------------------------------------------------------------
# ELF section parser — delegates to common.py
# ---------------------------------------------------------------------------

def _adapt_sections(sections):
    """Convert common.py sections (name, vaddr, offset, size) → (vaddr, offset, size)."""
    return [(s[1], s[2], s[3]) for s in sections]


def read_from_axf(axf_path, sections, addr, size):
    """Read bytes from AXF at virtual address."""
    # Try common.py format first
    for sa, so, ss in sections:
        if sa <= addr < sa + ss:
            with open(axf_path, 'rb') as f:
                f.seek(so + (addr - sa))
                return f.read(size)
    return None


# ---------------------------------------------------------------------------
# Stack frame analyzer
# ---------------------------------------------------------------------------

def analyze_stack_frame(axf_path, sections, func_addr, func_size):
    """
    Analyze a function's stack frame.
    Returns (push_bytes, sub_sp_bytes, bl_targets).
    """
    data = read_from_axf(axf_path, sections, func_addr, func_size)
    if not data:
        return 0, 0, []

    push_bytes = 0
    sub_sp_total = 0
    bl_targets = []

    pos = 0
    while pos < len(data) - 1:
        hw = struct.unpack_from('<H', data, pos)[0]
        hi5 = (hw >> 11) & 0x1F
        is_32bit = hi5 >= 0x1D

        if not is_32bit:
            # PUSH (16-bit): 1011 010x xxxx xxxx  or  1011 011x xxxx xxxx
            if (hw & 0xFE00) == 0xB400 and pos < 32:
                rlist = hw & 0xFF
                lr_bit = (hw >> 8) & 1
                push_bytes += (bin(rlist).count('1') + lr_bit) * 4
            # SUB SP, SP, #imm: 1011 0000 1 imm7
            elif (hw & 0xFF80) == 0xB080:
                sub_sp_total += (hw & 0x7F) * 4
            pos += 2
        else:
            if pos + 3 < len(data):
                hw2 = struct.unpack_from('<H', data, pos + 2)[0]
                # PUSH.W: E92D xxxx
                if hw == 0xE92D and pos < 32:
                    push_bytes += bin(hw2).count('1') * 4
                # SUB.W SP, SP, #imm12 (32-bit Thumb-2)
                # Encoding: hw1 = 1111 0 i 01 01 S 1101 (Rn=SP=13)
                #           hw2 = imm3 Rd(=1101=SP) imm8
                elif (hw & 0xFBEF) == 0xF1AD and ((hw2 >> 8) & 0xF) == 0xD:
                    i_bit = (hw >> 10) & 1
                    imm3 = (hw2 >> 12) & 7
                    imm8 = hw2 & 0xFF
                    imm12 = (i_bit << 11) | (imm3 << 8) | imm8
                    sub_sp_total += imm12
                # BL / BLX
                elif hi5 == 0x1E and (hw2 >> 14) == 3:
                    s = (hw >> 10) & 1
                    j1 = (hw2 >> 13) & 1
                    j2 = (hw2 >> 11) & 1
                    i1 = 1 - (j1 ^ s)
                    i2 = 1 - (j2 ^ s)
                    off = ((s << 24) | (i1 << 23) | (i2 << 22) |
                           ((hw & 0x3FF) << 12) | ((hw2 & 0x7FF) << 1))
                    if s:
                        off -= (1 << 25)
                    bl_targets.append(func_addr + pos + 4 + off)
            pos += 4

    return push_bytes, sub_sp_total, bl_targets


# ---------------------------------------------------------------------------
# Peak stack calculator — greedy mode
# ---------------------------------------------------------------------------

def calc_peak_stack_greedy(axf_path, sections, symbols, root_addr, root_size,
                           max_depth=20):
    """
    Greedy peak stack analysis: at each level, follow the callee with the
    largest own stack frame. Returns (peak_bytes, deepest_path).
    This finds a LOWER BOUND on peak stack — sufficient for overflow confirmation,
    but may underestimate if the true worst path isn't the greediest.
    """
    visited = set()
    path = []
    total = 0

    cur_addr, cur_size = root_addr, root_size

    for _ in range(max_depth):
        if cur_addr in visited:
            break
        visited.add(cur_addr)

        info = lookup(symbols, cur_addr)
        name = info[0] if info else ('0x%08x' % cur_addr)

        p, s, bls = analyze_stack_frame(axf_path, sections, cur_addr, cur_size)
        own = p + s
        total += own
        path.append((name, p, s, own))

        if not bls:
            break

        best = None
        best_own = 0
        for t in bls:
            t_info = lookup(symbols, t)
            if t_info and t_info[1] not in visited:
                tp, ts, _ = analyze_stack_frame(axf_path, sections,
                                                 t_info[1], t_info[2])
                if tp + ts > best_own:
                    best_own = tp + ts
                    best = t_info

        if best:
            cur_addr, cur_size = best[1], best[2]
        else:
            break

    return total, path


# ---------------------------------------------------------------------------
# Peak stack calculator — BFS with depth limit
# ---------------------------------------------------------------------------

def calc_peak_stack_bfs(axf_path, sections, symbols, root_addr, root_size,
                        max_depth=6):
    """
    BFS peak stack analysis: explores all call paths up to max_depth.
    Returns (peak_bytes, deepest_path).
    WARNING: combinatorial explosion for deep chains (printf calling printf).
    Use greedy mode for depth > 5.
    """
    best_total = 0
    best_path = []

    # worklist items: (func_addr, func_size, cumulative, path, depth, visited)
    worklist = [(root_addr, root_size, 0, [], 0, set())]

    while worklist:
        fa, fs, cum, path, depth, visited = worklist.pop()

        if fa in visited or depth > max_depth:
            if cum > best_total:
                best_total = cum
                best_path = path
            continue

        visited = visited | {fa}

        info = lookup(symbols, fa)
        name = info[0] if info else ('0x%08x' % fa)

        p, s, bls = analyze_stack_frame(axf_path, sections, fa, fs)
        own = p + s
        new_cum = cum + own
        new_path = path + [(name, p, s, own)]

        if not bls or depth >= max_depth:
            if new_cum > best_total:
                best_total = cum + own
                best_path = new_path
            continue

        for t in bls:
            t_info = lookup(symbols, t)
            if t_info:
                worklist.append((t_info[1], t_info[2], new_cum,
                                 new_path, depth + 1, visited))

    return best_total, best_path


def calc_peak_stack(axf_path, sections, symbols, root_addr, root_size,
                    max_depth=6, mode='auto'):
    """
    Unified peak stack calculator.
    mode: 'greedy' | 'bfs' | 'auto' (greedy if depth > 5, else bfs)
    """
    if mode == 'auto':
        mode = 'greedy' if max_depth > 5 else 'bfs'
    if mode == 'greedy':
        return calc_peak_stack_greedy(axf_path, sections, symbols,
                                       root_addr, root_size, max_depth)
    else:
        return calc_peak_stack_bfs(axf_path, sections, symbols,
                                    root_addr, root_size, max_depth)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description='Call-chain peak stack depth analyzer for ARM/Thumb binaries')
    parser.add_argument('axf_file', help='Path to AXF/ELF binary')
    parser.add_argument('map_file', help='Path to MAP file')
    parser.add_argument('--func', help='Function name to analyze')
    parser.add_argument('--addr', help='Function address (hex)')
    parser.add_argument('--size', type=int, default=0,
                        help='Function size in bytes (required with --addr)')
    parser.add_argument('--depth', type=int, default=20,
                        help='Max call chain depth (default: 20)')
    parser.add_argument('--mode', choices=['greedy', 'bfs', 'auto'],
                        default='greedy',
                        help='Search mode: greedy (default), bfs, or auto')
    parser.add_argument('--stack-size', type=int, default=0,
                        help='Actual stack allocation size for overflow judgment')
    args = parser.parse_args()

    symbols = parse_map_file(args.map_file)
    # common.py returns (name, vaddr, offset, size); adapt to (vaddr, offset, size)
    raw_sections = parse_elf_sections(args.axf_file)
    sections = _adapt_sections(raw_sections)

    # Resolve target function
    if args.func:
        info = find_by_name(symbols, args.func, exact=True)
        if not info:
            # Try partial match
            info = find_by_name(symbols, args.func, exact=False)
        if not info:
            print('Error: function "%s" not found in MAP file' % args.func)
            sys.exit(1)
        name, addr, size = info
        print('Target: %s (0x%x, %d bytes)' % (name, addr, size))
    elif args.addr:
        addr = int(args.addr, 16) & ~1
        if args.size > 0:
            size = args.size
            info = lookup(symbols, addr)
            name = info[0] if info else '0x%08x' % addr
        else:
            info = lookup(symbols, addr)
            if not info:
                print('Error: address 0x%x not found in MAP file' % addr)
                sys.exit(1)
            name, addr, size = info
        print('Target: %s (0x%x, %d bytes)' % (name, addr, size))
    else:
        parser.print_help()
        sys.exit(1)

    # Analyze own stack frame
    p, s, bls = analyze_stack_frame(args.axf_file, sections, addr, size)
    own_frame = p + s
    print('\nOwn stack frame: PUSH=%d + SUB_SP=%d = %d bytes' % (p, s, own_frame))
    print('Calls %d functions' % len(bls))

    # Calculate peak stack:
    # For greedy mode, trace from EACH top-level callee and pick global max.
    # Single greedy from root can miss deeper sub-paths (e.g., a small callee
    # that leads to a very deep chain through log/printf).
    if args.mode in ('greedy', 'auto'):
        best_peak = own_frame
        best_path = [(name, p, s, own_frame)]

        seen_callees = set()
        for t in bls:
            t_info = lookup(symbols, t)
            if not t_info or t_info[0] in seen_callees:
                continue
            seen_callees.add(t_info[0])

            t_peak, t_path = calc_peak_stack_greedy(
                args.axf_file, sections, symbols,
                t_info[1], t_info[2], max_depth=args.depth)
            if own_frame + t_peak > best_peak:
                best_peak = own_frame + t_peak
                best_path = [(name, p, s, own_frame)] + list(t_path)

        peak, path = best_peak, best_path
    else:
        peak, path = calc_peak_stack_bfs(args.axf_file, sections, symbols,
                                          addr, size, max_depth=args.depth)

    # Print deepest path
    print('\n=== Deepest call chain (mode=%s, max depth=%d) ===' %
          (args.mode, args.depth))
    cum = 0
    for i, (fname, fp, fs, fown) in enumerate(path):
        cum += fown
        indent = '  ' * (i + 1)
        print('%s%-45s push=%3d sub_sp=%3d  own=%4d  cum=%4d' %
              (indent, fname[:45], fp, fs, fown, cum))

    print('\n=== Peak stack from deepest chain: %d bytes ===' % peak)

    # Stack overflow judgment
    if args.stack_size > 0:
        print('\n=== Stack overflow judgment ===')
        print('Peak:   %d bytes' % peak)
        print('Alloc:  %d bytes' % args.stack_size)
        if peak > args.stack_size:
            print('Result: OVERFLOW (peak > alloc by %d bytes)' %
                  (peak - args.stack_size))
        else:
            print('Result: SAFE (headroom: %d bytes, %.1f%%)' %
                  (args.stack_size - peak,
                   (args.stack_size - peak) / float(args.stack_size) * 100))

    # Show ALL top-level callees with their peak contribution
    print('\n=== All callees peak stack contribution ===')
    callee_peaks = []
    seen = set()
    for t in bls:
        t_info = lookup(symbols, t)
        if t_info and t_info[0] not in seen:
            seen.add(t_info[0])
            tp, ts, tbls = analyze_stack_frame(args.axf_file, sections,
                                                t_info[1], t_info[2])
            tpeak, _ = calc_peak_stack(args.axf_file, sections, symbols,
                                        t_info[1], t_info[2],
                                        max_depth=args.depth,
                                        mode=args.mode)
            callee_peaks.append((t_info[0], tp, ts, tpeak, own_frame + tpeak))

    callee_peaks.sort(key=lambda x: -x[4])
    print('%-45s %5s %5s %5s %5s' % ('Function', 'PUSH', 'SUB', 'PEAK', 'TOTAL'))
    print('-' * 75)
    for cname, cp, cs, cpeak, ctotal in callee_peaks:
        flag = ''
        if args.stack_size > 0 and ctotal > args.stack_size:
            flag = ' ***'
        print('%-45s %5d %5d %5d %5d%s' %
              (cname[:45], cp, cs, cpeak, ctotal, flag))


if __name__ == '__main__':
    main()
