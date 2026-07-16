#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 FreeRTOS 堆深度分析（TLSF 物理块遍历 + 内存归属 + 碎片化）。

平台用 TLSF 分配器，MM_DEBUG_EN + MM_HEAD_BOUNDARY + HEAP_MEM_DEBUG 启用，
每个 used 块头记录 alloc_owner = (funcPtr & 0xFFFFFF) | (taskNum << 24)，
据此可精确回答「哪些功能块占用大量内存」。

块头布局（MM_DEBUG，已从 tlsf.c + dump 验证）：
  +0x00 prev_phys_block
  +0x04 head_bound      (0xBEAFDEAD)
  +0x08 alloc_owner     (used: funcPtr|taskNum)
  +0x0C size            (bit0=free, bit1=prev_free; used 低16=alloc 高16=wanted)
遍历：next = block + 0x0C + block_size，block_size==0 为 last。

⚠️ 重要修正：psSlp2FreeBytesRemaining 是 sleep/retention 上下文的**另一个堆**，
不是主 TLSF 应用堆，不能当主堆 free 读取。主堆统计以 TLSF 遍历为准。
"""
from qcx216_common import u32

# MM_DEBUG 块头偏移
BLK_OFF_PREV = 0x00
BLK_OFF_HEADBOUND = 0x04
BLK_OFF_OWNER = 0x08
BLK_OFF_SIZE = 0x0C
HEAD_BOUNDARY_MAGIC = 0xBEAFDEAD
FREE_BIT = 0x1


def _block_size(size_field: int) -> tuple:
    """返回 (is_free, alloc_size, wanted_size)。"""
    is_free = size_field & FREE_BIT
    if is_free:
        return (True, size_field & ~0x3, 0)           # free: 全 32 位为 size
    alloc = (size_field & ~0x3) & 0xFFFF               # used 低 16 位
    wanted = (size_field >> 16) & 0xFFFF               # used 高 16 位
    return (False, alloc, wanted)


def _get_pool(data: bytes, elf):
    """从 pool_group 取第一个池：(mem_start, size)。first block = mem - 4。"""
    pg = elf.find_symbol("pool_group")
    if not pg:
        return None
    cnt = u32(data, pg.addr)
    if not cnt:
        return None
    node_start = pg.addr + 8              # pool_group{cnt, size_total, node[cnt]}
    size = u32(data, node_start)
    mem = u32(data, node_start + 4)
    if mem and size:
        return (mem, size)
    return None


def walk_tlsf(data: bytes, elf, max_blocks: int = 8000):
    """遍历 TLSF 池所有物理块。返回 (used_list, free_list)。

    used_list: [(block_addr, alloc_size, wanted_size, owner)]
    free_list: [(block_addr, size)]
    """
    pool = _get_pool(data, elf)
    if not pool:
        return ([], [])
    mem, pool_size = pool
    first_block = mem - 4                 # first block 的 prev_phys_block 落在 pool 外
    pool_hi = mem + pool_size + 0x200

    used, free = [], []
    b = first_block
    guard = 0
    while b < pool_hi and guard < max_blocks:
        guard += 1
        szf = u32(data, b + BLK_OFF_SIZE)
        if szf is None:
            break
        is_free, asize, wsize = _block_size(szf)
        if asize == 0 or asize > 0x100000:        # last block / 异常
            break
        if is_free:
            free.append((b, asize))
        else:
            owner = u32(data, b + BLK_OFF_OWNER) or 0
            used.append((b, asize, wsize, owner))
        b = b + BLK_OFF_SIZE + asize
    return (used, free)


def analyze_heap(data: bytes, elf):
    """主 TLSF 堆综合分析。"""
    used, free = walk_tlsf(data, elf)
    result = {
        "used_blocks": len(used), "free_blocks": len(free),
        "total_used": sum(u[1] for u in used),
        "total_free": sum(f[1] for f in free),
        "max_free_block": max((f[1] for f in free), default=0),
        "free_block_count": len(free),
    }
    total = result["total_used"] + result["total_free"]
    result["total"] = total
    result["used_pct"] = round(result["total_used"] / total * 100, 1) if total else 0
    # 碎片化：空闲总量中最大连续块占比越低 = 越碎片化
    if result["total_free"] > 0:
        result["frag_pct"] = round(100 * (1 - result["max_free_block"] / result["total_free"]), 1)
    else:
        result["frag_pct"] = 0
    # 内存归属 TOP（按 funcPtr 归类）
    by_owner = {}
    for _b, asz, _w, owner in used:
        fp = owner & 0xFFFFFF
        tn = (owner >> 24) & 0xFF
        rec = by_owner.setdefault(fp, {"bytes": 0, "count": 0, "tasks": set()})
        rec["bytes"] += asz
        rec["count"] += 1
        rec["tasks"].add(tn)
    result["by_owner"] = sorted(by_owner.items(), key=lambda x: -x[1]["bytes"])

    # SLP2 另一堆（标注，不当主堆）
    slp = elf.find_symbol("psSlp2FreeBytesRemaining")
    if slp:
        result["slp2_free"] = u32(data, slp.addr)
    return result


def format_heap(data: bytes, elf) -> str:
    h = analyze_heap(data, elf)
    lines = ["## Heap Utilization (TLSF traversal)"]
    if not h["total"]:
        lines.append("  (无法定位 TLSF 池；检查 pool_group 符号)")
        return "\n".join(lines)
    lines.append(f"  Total (used+free) : {h['total']:>8} bytes")
    lines.append(f"  Used  : {h['used_blocks']:>4} blocks, {h['total_used']:>8} bytes  ({h['used_pct']:.1f}%)")
    lines.append(f"  Free  : {h['free_blocks']:>4} blocks, {h['total_free']:>8} bytes")
    lines.append(f"  Max free block     : {h['max_free_block']:>8} bytes  (最大可用连续块)")
    lines.append(f"  Fragmentation      : {h['frag_pct']:.1f}%  "
                 f"(越低越连续；>50% 视为碎片化严重)")
    # 判定
    if h["used_pct"] >= 95:
        lines.append(f"  >> 判定: 堆严重不足({h['used_pct']:.1f}% used)")
    elif h["max_free_block"] < 256 and h["total_free"] > 1024:
        lines.append(f"  >> 判定: 碎片化严重(总free {h['total_free']}B 但最大块仅 {h['max_free_block']}B)")
    elif h["frag_pct"] > 50:
        lines.append(f"  >> 判定: 碎片化偏高({h['frag_pct']:.1f}%)")
    else:
        lines.append(f"  >> 判定: 堆健康({h['used_pct']:.1f}% used, 碎片化 {h['frag_pct']:.1f}%)")
    if "slp2_free" in h:
        lines.append(f"  psSlp2FreeBytes    : {h['slp2_free']} bytes  (注: sleep/retention 另一堆, 非主堆)")

    # 内存归属 TOP
    if h["by_owner"]:
        lines.append("")
        lines.append("  ### Top memory consumers (按分配者 alloc_owner funcPtr 归类)")
        lines.append(f"    {'funcPtr':<10} {'symbol':<34} {'bytes':>8} {'blocks':>6} tasks")
        for fp, rec in h["by_owner"][:12]:
            sym = elf.sym_at(fp)
            nm = sym.name if sym and sym.name else "?"
            if nm.startswith("$"):
                nm = "(data/sub-symbol)"
            tasks = sorted(rec["tasks"]) if rec["tasks"] else []
            lines.append(f"    0x{fp:06X}   {nm[:34]:<34} {rec['bytes']:>8} {rec['count']:>6}   {tasks}")
        lines.append("    说明: funcPtr=分配者返回地址低24位, tasks=高8位任务号; "
                     "静态快照无法判定泄漏, 需 spec-memory-leak-analyzer 埋点追踪时间序列")
    return "\n".join(lines)
