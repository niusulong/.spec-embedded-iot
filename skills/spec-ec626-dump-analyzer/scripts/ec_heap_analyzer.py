#!/usr/bin/env python3
"""EC platform heap analysis: trace_node allocation tracking and TLSF heap scanning."""

import struct
import sys
import io

# Ensure stdout handles non-GBK characters from corrupted dump data
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding=sys.stdout.encoding, errors='replace')

from ec_constants import (
    u32,
    MM_TRACE_NODE_SIZE,
    MM_TRACE_V1_MEMPTR, MM_TRACE_V1_FUNPTR, MM_TRACE_V1_LENGTH,
    MM_TRACE_V1_TASK, MM_TRACE_V1_NEXT, MM_TRACE_V1_TASK_LEN,
    MM_TRACE_V2_MEMPTR, MM_TRACE_V2_FUNPTR, MM_TRACE_V2_LENGTH,
    MM_TRACE_V2_TASK, MM_TRACE_V2_NEXT, MM_TRACE_V2_TASK_LEN,
    TLSF_FREE_BIT, TLSF_HEAD_BOUNDARY, TLSF_BLOCK_START_OFFSET,
)
from ec_build_config import find_symbol


# ── trace_node (mm_debug) allocation tracking ──────────────────────────

# Layout descriptor: (off_memptr, off_funptr, off_length, off_task, off_next, task_len)
_LAYOUT_V1 = (MM_TRACE_V1_MEMPTR, MM_TRACE_V1_FUNPTR, MM_TRACE_V1_LENGTH,
              MM_TRACE_V1_TASK, MM_TRACE_V1_NEXT, MM_TRACE_V1_TASK_LEN)
_LAYOUT_V2 = (MM_TRACE_V2_MEMPTR, MM_TRACE_V2_FUNPTR, MM_TRACE_V2_LENGTH,
              MM_TRACE_V2_TASK, MM_TRACE_V2_NEXT, MM_TRACE_V2_TASK_LEN)


def _count_valid_entries(dump_data, trace_node_addr, num_nodes, layout):
    """Count valid trace_node entries for a given layout. Returns (valid_count, any_nonzero)."""
    off_mp, off_fp, off_ln, off_tn, off_nx, tn_len = layout
    dlen = len(dump_data)
    valid = 0
    nonzero = False
    for i in range(min(num_nodes, 20)):  # sample first 20 entries
        base = trace_node_addr + i * MM_TRACE_NODE_SIZE
        if base + MM_TRACE_NODE_SIZE > dlen:
            break
        mp = u32(dump_data, base + off_mp)
        fp = u32(dump_data, base + off_fp)
        ln = u32(dump_data, base + off_ln)
        tn_byte = dump_data[base + off_tn]
        if mp == 0 and fp == 0 and ln == 0:
            continue
        nonzero = True
        if (0 < mp < dlen) and (0 < ln < dlen) and tn_byte != 0:
            valid += 1
    return valid, nonzero


def _detect_layout(dump_data, trace_node_addr, num_nodes):
    """Auto-detect trace_node layout by comparing V1 vs V2 validity."""
    v1_valid, v1_nonzero = _count_valid_entries(dump_data, trace_node_addr, num_nodes, _LAYOUT_V1)
    v2_valid, v2_nonzero = _count_valid_entries(dump_data, trace_node_addr, num_nodes, _LAYOUT_V2)

    if v1_valid >= v2_valid:
        return _LAYOUT_V1, 1
    else:
        return _LAYOUT_V2, 2


def scan_trace_node(dump_data, trace_node_addr, trace_node_size=0,
                    node_hash_addr=None, free_node_addr=None,
                    map_file=None, flash_start=0, flash_end=0):
    """Scan FreeRTOS mm_debug trace_node array for heap allocation tracking."""
    dlen = len(dump_data)
    if trace_node_addr == 0 or trace_node_addr + MM_TRACE_NODE_SIZE > dlen:
        return None

    if trace_node_size > 0:
        num_nodes = trace_node_size // MM_TRACE_NODE_SIZE
    else:
        num_nodes = 128

    # Auto-detect layout version
    layout, layout_ver = _detect_layout(dump_data, trace_node_addr, num_nodes)
    off_mp, off_fp, off_ln, off_tn, off_nx, tn_len = layout

    free_node_ptr = 0
    if free_node_addr and free_node_addr + 4 <= dlen:
        free_node_ptr = u32(dump_data, free_node_addr)

    free_node_addrs = set()
    if free_node_ptr != 0:
        ptr = free_node_ptr
        walk_limit = num_nodes + 10
        while ptr != 0 and len(free_node_addrs) < walk_limit:
            if ptr in free_node_addrs:
                break
            if ptr < trace_node_addr or ptr >= trace_node_addr + num_nodes * MM_TRACE_NODE_SIZE:
                break
            free_node_addrs.add(ptr)
            next_off = ptr + off_nx
            if next_off + 4 > dlen:
                break
            ptr = u32(dump_data, next_off)

    active_nodes = []
    all_zero = True
    max_valid_length = dlen

    for i in range(num_nodes):
        offset = trace_node_addr + i * MM_TRACE_NODE_SIZE
        if offset + MM_TRACE_NODE_SIZE > dlen:
            break

        memptr = u32(dump_data, offset + off_mp)
        funptr = u32(dump_data, offset + off_fp)
        length = u32(dump_data, offset + off_ln)
        task_name_bytes = dump_data[offset + off_tn:offset + off_tn + tn_len]
        next_ptr = u32(dump_data, offset + off_nx)

        is_valid_memptr = (0 < memptr < dlen)
        is_valid_length = (0 < length < max_valid_length)

        if is_valid_memptr and is_valid_length and task_name_bytes[0] != 0:
            all_zero = False
            task_name = task_name_bytes.split(b'\x00')[0].decode('ascii', errors='replace')
            is_free = (trace_node_addr + i * MM_TRACE_NODE_SIZE) in free_node_addrs
            active_nodes.append({
                'index': i,
                'addr': trace_node_addr + i * MM_TRACE_NODE_SIZE,
                'memptr': memptr,
                'funptr': funptr,
                'length': length,
                'task_name': task_name,
                'next_ptr': next_ptr,
                'is_free': is_free,
            })
        elif memptr != 0 or funptr != 0 or length != 0:
            all_zero = False

    if all_zero:
        return None

    truly_active = [n for n in active_nodes if not n['is_free']]
    if not truly_active and active_nodes:
        truly_active = active_nodes

    total_allocated = sum(n['length'] for n in truly_active)

    by_task = {}
    for n in truly_active:
        t = n['task_name']
        if t not in by_task:
            by_task[t] = {'count': 0, 'total_bytes': 0}
        by_task[t]['count'] += 1
        by_task[t]['total_bytes'] += n['length']

    by_caller = {}
    funptr_to_caller = {}
    for n in truly_active:
        fp = n['funptr']
        if fp not in funptr_to_caller:
            if map_file:
                sym = find_symbol(map_file, fp)
                funptr_to_caller[fp] = sym[0] if sym else f'0x{fp:08X}'
            else:
                funptr_to_caller[fp] = f'0x{fp:08X}'
        caller = funptr_to_caller[fp]
        if caller not in by_caller:
            by_caller[caller] = {'count': 0, 'total_bytes': 0, 'tasks': set()}
        by_caller[caller]['count'] += 1
        by_caller[caller]['total_bytes'] += n['length']
        by_caller[caller]['tasks'].add(n['task_name'])

    top_by_size = sorted(truly_active, key=lambda x: -x['length'])

    leak_indicators = []
    caller_task_large = {}
    for n in truly_active:
        if n['length'] >= 64:
            if map_file:
                sym = find_symbol(map_file, n['funptr'])
                caller = sym[0] if sym else f'0x{n["funptr"]:08X}'
            else:
                caller = f'0x{n["funptr"]:08X}'
            key = (caller, n['task_name'])
            if key not in caller_task_large:
                caller_task_large[key] = []
            caller_task_large[key].append(n['length'])

    for (caller, task), sizes in sorted(caller_task_large.items(),
                                         key=lambda x: -sum(x[1])):
        if len(sizes) >= 3:
            leak_indicators.append({
                'caller': caller,
                'task': task,
                'count': len(sizes),
                'total': sum(sizes),
                'sizes': sorted(sizes, reverse=True),
            })

    return {
        'total_nodes': num_nodes,
        'active_nodes': len(truly_active),
        'free_nodes': num_nodes - len(truly_active),
        'free_node_ptr': free_node_ptr,
        'trace_node_full': free_node_ptr == 0 and len(truly_active) == num_nodes,
        'total_allocated_bytes': total_allocated,
        'nodes': truly_active,
        'by_task': by_task,
        'by_caller': by_caller,
        'funptr_to_caller': funptr_to_caller,
        'top_by_size': top_by_size,
        'leak_indicators': leak_indicators,
        'layout_ver': layout_ver,
    }


def _safe(s):
    """Sanitize string for console output (replace non-ASCII-safe chars)."""
    return ''.join(c if ord(c) < 128 and c.isprintable() else '?' for c in s)

def print_trace_node_report(result, verbose=False):
    """Print formatted heap memory trace report from trace_node scan."""
    if result is None:
        print("  No trace_node data found (mm_debug not enabled or array empty)")
        return

    total = result['total_nodes']
    active = result['active_nodes']
    total_bytes = result['total_allocated_bytes']
    layout_ver = result.get('layout_ver', 1)

    if layout_ver == 2:
        print(f"  [Layout V2 detected: memptr@+8, funptr@+12, task_name@+20(4B)]")
    print(f"  {active}/{total} trace_node entries in use, "
          f"{total_bytes} bytes ({total_bytes/1024:.1f} KB) tracked")

    if result['trace_node_full']:
        print(f"  *** trace_node array FULL (free_node=NULL) -- possible untracked allocations ***")

    top_n = min(20, len(result['top_by_size']))
    if top_n > 0:
        funptr_map = result.get('funptr_to_caller', {})
        print(f"\n  Top {top_n} Largest Unfreed Blocks:")
        print(f"  {'Size':>8}  {'Addr':>10}  {'Task':>8}  {'Caller'}")
        print(f"  {'-'*8}  {'-'*10}  {'-'*8}  {'-'*40}")
        for n in result['top_by_size'][:top_n]:
            caller_name = funptr_map.get(n['funptr'], f"0x{n['funptr']:08X}")
            print(f"  {n['length']:8d}  0x{n['memptr']:08X}  {_safe(n['task_name']):>8s}  {caller_name}")

    if result['by_task']:
        print(f"\n  Allocations by Task:")
        for task, info in sorted(result['by_task'].items(), key=lambda x: -x[1]['total_bytes']):
            print(f"    {_safe(task):>8s}: {info['count']:3d} blocks, "
                  f"{info['total_bytes']:6d} bytes ({info['total_bytes']/1024:.1f} KB)")

    if result['by_caller']:
        print(f"\n  Allocations by Caller (funptr):")
        for caller, info in sorted(result['by_caller'].items(),
                                    key=lambda x: -x[1]['total_bytes']):
            tasks_str = ','.join(sorted(_safe(t) for t in info['tasks']))
            print(f"    {_safe(caller):<40s}: {info['count']:3d} blocks, "
                  f"{info['total_bytes']:6d} bytes ({info['total_bytes']/1024:.1f} KB) [{tasks_str}]")

    if result['leak_indicators']:
        print(f"\n  Potential Leak Indicators (same caller+task, >=3 blocks >=64B):")
        for li in result['leak_indicators']:
            sizes_str = '+'.join(str(s) for s in li['sizes'][:5])
            if len(li['sizes']) > 5:
                sizes_str += '...'
            print(f"    {_safe(li['caller']):<40s} [{_safe(li['task'])}]: "
                  f"{li['count']} blocks, total {li['total']} bytes ({sizes_str})")

    if verbose and result['nodes']:
        print(f"\n  All Active trace_node Entries:")
        for n in sorted(result['nodes'], key=lambda x: -x['length']):
            print(f"    [{n['index']:3d}] ptr=0x{n['memptr']:08X} size={n['length']:5d} "
                  f"task={_safe(n['task_name']):8s} caller=0x{n['funptr']:08X}")


# ── TLSF heap scanning (heap_6) ────────────────────────────────────────

def scan_tlsf_heap(dump_data, heap_start, total_heap_size, map_file=None,
                   flash_start=0, flash_end=0):
    """Scan FreeRTOS TLSF heap (heap_6) for memory utilization from RAM dump."""
    dlen = len(dump_data)
    if heap_start == 0 or total_heap_size == 0:
        return None
    if heap_start + total_heap_size > dlen:
        return None

    heap_data = dump_data[heap_start:heap_start + total_heap_size]

    first_boundary = -1
    for i in range(0, min(len(heap_data) - 3, 4096), 4):
        val = struct.unpack_from('<I', heap_data, i)[0]
        if val == TLSF_HEAD_BOUNDARY:
            first_boundary = i
            break

    if first_boundary < 0:
        return None

    block_offset = first_boundary - 4
    if block_offset < 0:
        return None

    blocks = []
    total_used = 0
    total_free = 0
    used_count = 0
    free_count = 0
    largest_free = 0
    max_walk = 500
    walk_count = 0

    while block_offset >= 0 and block_offset + 16 < len(heap_data) and walk_count < max_walk:
        size_field = struct.unpack_from('<I', heap_data, block_offset + 12)[0]
        is_free = size_field & TLSF_FREE_BIT

        if is_free:
            blk_size = size_field & ~3
        else:
            blk_size = (size_field & 0xFFFF) & ~3
            wanted_size = (size_field >> 16) & 0xFFFF

        if blk_size == 0 or blk_size > total_heap_size:
            break

        abs_addr = heap_start + block_offset
        alloc_owner = 0
        if block_offset + 8 + 4 <= len(heap_data):
            alloc_owner = struct.unpack_from('<I', heap_data, block_offset + 8)[0]

        if is_free:
            total_free += blk_size
            free_count += 1
            if blk_size > largest_free:
                largest_free = blk_size
            blocks.append({
                'addr': abs_addr,
                'size': blk_size,
                'wanted': 0,
                'is_free': True,
                'alloc_owner': 0,
            })
        else:
            total_used += blk_size
            used_count += 1
            blocks.append({
                'addr': abs_addr,
                'size': blk_size,
                'wanted': wanted_size,
                'is_free': False,
                'alloc_owner': alloc_owner,
            })

        next_offset = block_offset + TLSF_BLOCK_START_OFFSET + blk_size - 4
        if next_offset <= block_offset:
            break
        block_offset = next_offset
        walk_count += 1

    util_pct = (total_used / total_heap_size * 100) if total_heap_size > 0 else 0
    free_pct = (total_free / total_heap_size * 100) if total_heap_size > 0 else 0

    if util_pct >= 95:
        status = 'CRITICAL'
    elif util_pct >= 80:
        status = 'HIGH'
    else:
        status = 'OK'

    used_blocks = sorted([b for b in blocks if not b['is_free']],
                         key=lambda x: -x['size'])
    top_used = used_blocks[:20]
    free_blocks = sorted([b for b in blocks if b['is_free']],
                         key=lambda x: -x['size'])
    top_free = free_blocks[:10]

    if map_file:
        for b in top_used:
            owner = b.get('alloc_owner', 0)
            func_ptr = owner & 0x00FFFFFF
            if func_ptr:
                sym = find_symbol(map_file, func_ptr)
                b['alloc_owner_name'] = sym[0] if sym else f'0x{func_ptr:08X}'

    return {
        'heap_start': heap_start,
        'total_size': total_heap_size,
        'used_size': total_used,
        'free_size': total_free,
        'overhead': total_heap_size - total_used - total_free,
        'used_count': used_count,
        'free_count': free_count,
        'util_pct': util_pct,
        'free_pct': free_pct,
        'largest_free': largest_free,
        'status': status,
        'blocks': blocks,
        'top_used': top_used,
        'top_free': top_free,
    }


def print_tlsf_heap_report(result, verbose=False):
    """Print formatted TLSF heap utilization report."""
    if result is None:
        print("  No TLSF heap data found")
        return

    print(f"  Heap: 0x{result['heap_start']:08X}, {result['total_size']} bytes "
          f"({result['total_size']/1024:.1f} KB) total")
    print(f"  Used: {result['used_size']} bytes ({result['used_size']/1024:.1f} KB, "
          f"{result['util_pct']:.1f}%) in {result['used_count']} blocks")
    print(f"  Free: {result['free_size']} bytes ({result['free_size']/1024:.1f} KB, "
          f"{result['free_pct']:.1f}%) in {result['free_count']} blocks")
    print(f"  Largest free block: {result['largest_free']} bytes "
          f"({result['largest_free']/1024:.1f} KB)")
    print(f"  TLSF overhead: {result['overhead']} bytes")

    if result['status'] == 'CRITICAL':
        print(f"  *** HEAP CRITICALLY LOW: {result['free_pct']:.1f}% free ***")
    elif result['status'] == 'HIGH':
        print(f"  ** HEAP HIGH USAGE: {result['free_pct']:.1f}% free **")
    else:
        print(f"  Heap usage normal")

    if result['top_used']:
        print(f"\n  Top 10 Largest Used Blocks:")
        print(f"  {'Block':>8}  {'Size':>6}  {'Wanted':>6}  {'Caller'}")
        print(f"  {'-'*8}  {'-'*6}  {'-'*6}  {'-'*40}")
        for b in result['top_used'][:10]:
            owner_name = b.get('alloc_owner_name')
            if owner_name:
                owner_str = owner_name
            elif b['alloc_owner']:
                owner_str = f"0x{b['alloc_owner']:08X}"
            else:
                owner_str = '-'
            print(f"  0x{b['addr']:06X}  {b['size']:6d}  {b['wanted']:6d}  {owner_str}")

    if result['top_free']:
        print(f"\n  Top 5 Largest Free Blocks:")
        for b in result['top_free'][:5]:
            print(f"    0x{b['addr']:08X}: {b['size']:5d}B ({b['size']/1024:.1f} KB)")

    if verbose and result['free_count'] > 0:
        free_blocks = [b for b in result['blocks'] if b['is_free']]
        free_blocks.sort(key=lambda x: -x['size'])
        avg_free = result['free_size'] / result['free_count']
        frag_pct = (1 - result['largest_free'] / result['free_size']) * 100 if result['free_size'] > 0 else 0
        print(f"\n  Fragmentation: avg free block = {avg_free:.0f}B, "
              f"external frag = {frag_pct:.1f}%")
        print(f"  Free block sizes: {[b['size'] for b in free_blocks]}")
