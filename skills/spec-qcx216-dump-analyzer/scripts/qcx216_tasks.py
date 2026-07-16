#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 FreeRTOS 任务、栈与调用链分析。

Cortex-M3 + FreeRTOS，TCB 布局（用 IDLE 任务 TCB 验证）：
  +0x00 pxTopOfStack   当前栈指针
  +0x30 pxStack        栈底（低地址端，栈向下生长）
  +0x34 pcTaskName     任务名（ASCII）

提供三块能力：
  1. enumerate_tasks  扫描 RAM 枚举所有任务（0xA5 哨兵 + ASCII 名特征）
  2. backtrace        从异常 SP 扫描栈里的代码地址，还原调用链
  3. analyze_stack    单任务栈的溢出判定与使用率
"""
import re

from qcx216_common import (
    u32, to_ascii,
    TCB_OFF_TOP_OF_STACK, TCB_OFF_STACK_BASE, TCB_OFF_TASK_NAME,
    TCB_NAME_MAX, STACK_FILL_MAGIC,
)


def parse_tcb(data: bytes, tcb_addr: int):
    """解析单个 TCB。"""
    if tcb_addr == 0 or u32(data, tcb_addr) is None:
        return None
    top = u32(data, tcb_addr + TCB_OFF_TOP_OF_STACK)
    stack_base = u32(data, tcb_addr + TCB_OFF_STACK_BASE)
    raw = data[tcb_addr + TCB_OFF_TASK_NAME: tcb_addr + TCB_OFF_TASK_NAME + TCB_NAME_MAX]
    name = raw.split(b"\x00")[0].decode("ascii", errors="replace")
    return {"tcb_addr": tcb_addr, "name": name,
            "top_of_stack": top, "stack_base": stack_base}


def get_current_task(data: bytes, elf):
    """pxCurrentTCB -> 当前任务 TCB。"""
    sym = elf.find_symbol("pxCurrentTCB")
    if sym is None:
        return None
    tcb_addr = u32(data, sym.addr)
    if tcb_addr is None:
        return None
    tcb = parse_tcb(data, tcb_addr)
    if tcb:
        tcb["is_current"] = True
    return tcb


def _looks_like_task_name(data: bytes, off: int):
    """判断 data[off:off+16] 是否像任务名，返回名字或 None。"""
    nb = data[off:off + TCB_NAME_MAX]
    nlen = 0
    for b in nb:
        if 32 <= b < 127:
            nlen += 1
        else:
            break
    if not (2 <= nlen <= 15):
        return None
    if nb[nlen] != 0:
        return None
    name = nb[:nlen].decode("ascii", errors="replace")
    # 过滤纯随机字节串：要求字母占比高
    letters = sum(1 for c in name if c.isalpha())
    if letters < max(2, len(name) // 2):
        return None
    return name


def enumerate_tasks(data: bytes, elf, current_tcb: int = None):
    """扫描 RAM 枚举所有 FreeRTOS 任务。

    判据（全部满足才认为是真任务）：
      - +0x34 处是合法 ASCII 任务名
      - +0x30 pxStack / +0x00 pxTopOfStack 落在 RAM 范围
      - pxStack 处的栈底哨兵 == 0xA5A5A5A5（FreeRTOS 初始填充，真任务金标准）
    返回按栈底地址排序的任务列表。
    """
    tasks = []
    seen = set()
    for rstart, rend in elf.ram_ranges:
        if rend - rstart < 0x1000:   # 跳过零碎小段
            continue
        a = rstart
        while a < rend - TCB_NAME_MAX:
            name = _looks_like_task_name(data, a)
            if name is None:
                a += 4
                continue
            tcb = a - TCB_OFF_TASK_NAME
            if tcb in seen or tcb < rstart:
                a += 4
                continue
            pxstack = u32(data, tcb + TCB_OFF_STACK_BASE)
            pxtop = u32(data, tcb + TCB_OFF_TOP_OF_STACK)
            # 栈地址须落在 RAM，且 top >= base（栈向下生长）
            in_ram = (pxstack is not None and elf.is_ram(pxstack)
                      and pxtop is not None and elf.is_ram(pxtop))
            if not in_ram or pxtop < pxstack:
                a += 4
                continue
            sent = u32(data, pxstack)
            if sent != STACK_FILL_MAGIC:
                a += 4
                continue
            # 估算栈大小：找下一个非 0xA5 边界或用 name 前 RAM 间隔
            stack_size = _estimate_stack_size(data, pxstack, pxtop)
            seen.add(tcb)
            tasks.append({
                "tcb_addr": tcb, "name": name,
                "top_of_stack": pxtop, "stack_base": pxstack,
                "stack_size": stack_size, "sentinel": sent,
                "is_current": (tcb == current_tcb),
            })
            a = tcb + 0x44
    tasks.sort(key=lambda t: t["stack_base"])
    return tasks


def _estimate_stack_size(data: bytes, stack_base: int, top: int):
    """粗估任务栈大小：从栈底向上数连续 0xA5 + 已用部分，上限 16KB。"""
    unused = 0
    for off in range(0, 0x4000, 4):
        if u32(data, stack_base + off) == STACK_FILL_MAGIC:
            unused += 4
        else:
            break
    # 栈顶之上常见相邻任务/数据，难以精确取 size；用 unused + (top-base) 的较大者近似
    approx = max(top - stack_base, unused) + unused
    # 取一个合理上界：unused 向上对齐到 2 的幂附近的常见栈规格
    for sz in (256, 512, 1024, 2048, 4096, 6144, 8192, 12288, 16384):
        if sz >= (top - stack_base + 4):
            return sz
    return 16384


def analyze_stack(data: bytes, stack_base: int, top_of_stack=None, stack_size=None):
    """单任务栈的溢出判定与使用率。"""
    if stack_base == 0 or u32(data, stack_base) is None:
        return None
    result = {"stack_base": stack_base, "top_of_stack": top_of_stack, "stack_size": stack_size}
    scan_limit = stack_size if stack_size else 0x2000
    unused = 0
    for off in range(0, scan_limit, 4):
        if u32(data, stack_base + off) == STACK_FILL_MAGIC:
            unused += 4
        else:
            break
    result["unused"] = unused
    sample = min(scan_limit, 0x100)
    has_sentinel = any(u32(data, stack_base + off) == STACK_FILL_MAGIC
                       for off in range(0, sample, 4))
    result["has_sentinel"] = has_sentinel
    if stack_size and stack_size > 0:
        result["used_pct"] = round((stack_size - unused) / stack_size * 100, 1)
        if not has_sentinel:
            result["verdict"] = "NO SENTINEL (non-task/MSP?)"
        elif unused == 0:
            result["verdict"] = "OVERFLOW (sentinel corrupted)"
        elif result["used_pct"] > 95:
            result["verdict"] = "HIGH RISK (>95%)"
        elif result["used_pct"] > 80:
            result["verdict"] = "WARNING (>80%)"
        else:
            result["verdict"] = "OK"
    else:
        result["used_pct"] = None
        result["verdict"] = "NO SENTINEL" if not has_sentinel else "OK (size unknown)"
    return result


def backtrace(data: bytes, elf, sp: int, sp_limit: int, max_depth: int = 48):
    """从 sp 向 sp_limit（高地址）扫描栈，收集代码地址作为调用链。

    Cortex-M 默认不带帧指针，采用「栈扫描」启发式：函数序言 PUSH {..,lr}
    把返回地址压栈，连续的代码地址即调用链（内层→外层）。
    """
    if sp is None or sp_limit is None or sp >= sp_limit:
        return []
    chain = []
    seen = set()
    a = sp & ~3
    limit = min(sp_limit, sp + 0xA00)
    while a < limit and len(chain) < max_depth:
        v = u32(data, a)
        if v is not None and elf.is_code(v) and v not in seen:
            seen.add(v)
            chain.append((a, v))
        a += 4
    return chain


def format_backtrace(chain, elf, sp, title="Stack backtrace"):
    """格式化调用链。"""
    lines = [f"  ### {title}  (sp=0x{sp:08X})"]
    if not chain:
        lines.append("    (no code addresses found in stack range)")
        return "\n".join(lines)
    lines.append(f"    {'#':<3} {'stack@':<11} {'addr':<11} symbol")
    for i, (soff, addr) in enumerate(chain):
        loc = elf.locate(addr)
        sym = loc["symbol"] or "?"
        disp = f"{sym}+0x{loc['sym_offset']:X}" if loc["sym_offset"] is not None else sym
        extra = f"  [{loc['file']}:{loc['line']}]" if loc["file"] else ""
        lines.append(f"    {i:<3} 0x{soff:08X}  0x{addr:08X}  {disp}{extra}")
    return "\n".join(lines)


def format_tasks(data: bytes, elf, current_tcb=None) -> str:
    """格式化当前任务 + 全任务枚举 + 栈扫描。"""
    lines = ["## FreeRTOS Tasks & Stack Analysis"]

    cur = get_current_task(data, elf)
    if cur:
        current_tcb = cur["tcb_addr"]
        lines.append("")
        lines.append("  ### Current task (pxCurrentTCB)")
        lines.append(f"    TCB addr     : 0x{cur['tcb_addr']:08X}")
        lines.append(f"    Task name    : {cur['name']!r}")
        lines.append(f"    pxTopOfStack : 0x{cur['top_of_stack']:08X}")
        lines.append(f"    pxStack(base): 0x{cur['stack_base']:08X}")

    tasks = enumerate_tasks(data, elf, current_tcb)
    if tasks:
        lines.append("")
        lines.append(f"  ### All tasks ({len(tasks)})")
        lines.append(f"    {'name':<18} {'TCB':<11} {'stackBase':<11} {'size':<7} {'used%':<7} verdict")
        for t in tasks:
            a = analyze_stack(data, t["stack_base"], t["top_of_stack"], t["stack_size"])
            if a is None:
                continue
            used = f"{a['used_pct']:.0f}%" if a["used_pct"] is not None else "?"
            mark = " *" if t["is_current"] else "  "
            lines.append(
                f"   {mark}{t['name']:<18} 0x{t['tcb_addr']:08X} 0x{t['stack_base']:08X} "
                f"{t['stack_size']:<7} {used:<7} {a['verdict']}")
    return "\n".join(lines)
