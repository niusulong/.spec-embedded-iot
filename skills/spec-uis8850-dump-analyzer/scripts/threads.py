#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 FreeRTOS task list + stack watermarks + magic check.

Scans PSRAM heap for TCBs (FreeRTOS task control blocks), reports each task's
name, priority, stack base/top, watermark, and whether the stack-bottom magic
(0xa5a5a5a5) is intact. Highlights the stack-overflow task.

Usage:  python threads.py <dump_dir> <ap.elf>
"""
import os, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (addr2line_batch, thumb_real, buf_u32, tcb_offsets,
                    parse_dump_args, load_ctx, read_psram,
                    PSRAM_BASE, FREERTOS_STACK_MAGIC)

MAGIC = FREERTOS_STACK_MAGIC   # readable local alias for the hot scan loop


def is_ascii_name(b):
    if not b or b[0] == 0:
        return False
    n = 0
    for c in b:
        if c == 0:
            break
        if c < 0x20 or c > 0x7e:
            return False
        n += 1
    return 2 <= n <= 16


def main():
    args = parse_dump_args("UIS8850 FreeRTOS task list + stack watermarks")
    mem, syms, addr2line, objdump, tc = load_ctx(args.dump_dir, args.ap_elf)
    elf = args.ap_elf
    psram = read_psram(args.dump_dir)

    # TCB 偏移: DWARF 优先, 命名常量降级 (与 uis8850_analyze.py 同源)
    tcb = tcb_offsets(syms)
    off_top = tcb["pxTopOfStack"]
    off_prio = tcb["uxPriority"]
    off_pstack = tcb["pxStack"]
    off_name = tcb["pcTaskName"]
    off_tcbnum = tcb["uxTCBNumber"]
    print("(TCB 偏移: pxTopOfStack=0x%x uxPriority=0x%x pxStack=0x%x pcTaskName=0x%x)"
          % (off_top, off_prio, off_pstack, off_name))

    pxc_addr = syms.lookup("pxCurrentTCB")[0]
    cur_tcb = mem.try_u32(pxc_addr) if pxc_addr else None
    print("pxCurrentTCB -> 0x%08x\n" % (cur_tcb or 0))

    # 堆区扫描范围: .bss 之后到 PSRAM 末尾
    # 从 ELF 读 __heap_start / __heap_end, 否则用估计
    heap_lo, heap_hi = syms.lookup("__heap_start")[0], syms.lookup("__heap_end")[0]
    if not heap_lo:
        heap_lo = 0x803c0000
    if not heap_hi:
        heap_hi = 0x80800000
    print("扫描堆区 [0x%08x, 0x%08x) 找 TCB...\n" % (heap_lo, heap_hi))

    lo = heap_lo - PSRAM_BASE
    hi = heap_hi - PSRAM_BASE
    tasks = []
    a = lo
    while a + 0x60 < hi:
        top = buf_u32(psram, a + off_top)
        pstack = buf_u32(psram, a + off_pstack)
        if not top or not pstack:
            a += 4
            continue
        # pxTopOfStack 和 pxStack 都在 PSRAM 堆区
        if not (heap_lo <= top < heap_hi and heap_lo <= pstack < heap_hi):
            a += 4
            continue
        # 任务名 ASCII
        name_b = psram[a + off_name: a + off_name + 16]
        if not is_ascii_name(name_b):
            a += 4
            continue
        # 去重: 同一 pstack 只保留一个
        name = name_b.split(b"\x00")[0].decode("utf-8", "replace")
        prio = buf_u32(psram, a + off_prio)
        if prio > 255:   # 优先级 < configMAX_PRIORITIES(通常<256)
            a += 4
            continue
        tcbnum = buf_u32(psram, a + off_tcbnum)
        if tcbnum > 255:   # uxTCBNumber 是递增小整数, 过滤堆区假 TCB
            a += 4
            continue
        tasks.append((PSRAM_BASE + a, name, prio, top, pstack, tcbnum))
        a += 0x50  # TCB 之间有间隔, 跳过避免重复命中

    # 去重 (按 TCB 地址)
    seen = set()
    uniq = []
    for t in tasks:
        if t[0] not in seen:
            seen.add(t[0])
            uniq.append(t)
    tasks = uniq

    print("=" * 118)
    print(" %-10s %-14s %-5s %-12s %-12s %-5s %-7s %s" % (
        "TCB", "TaskName", "Prio", "pxTopOfStack", "pxStack", "TCB#", "剩栈B", "栈底magic"))
    print("-" * 118)
    overflow_tasks = []
    highwater_tasks = []
    for tcb_addr, name, prio, top, pstack, tcbnum in tasks:
        # 栈底 magic 检查: pxStack 处前几个字
        magic_ok = 0
        magic_bad = 0
        bad_vals = []
        for i in range(0, 0x20, 4):
            off = pstack - PSRAM_BASE
            if 0 <= off + i + 4 <= len(psram):
                v = buf_u32(psram, off + i)
                if v == MAGIC:
                    magic_ok += 1
                else:
                    magic_bad += 1
                    if len(bad_vals) < 2:
                        bad_vals.append("0x%08x" % v)
        # 栈水位 (历史最低剩余, FreeRTOS uxTaskGetStackHighWaterMark 算法):
        # 从 pxStack(栈底) 向上数连续 0xa5a5a5a5 字数 = 历史最深栈顶离栈底的距离 = 剩余栈
        wm_words = 0
        off_wm = pstack - PSRAM_BASE
        while (off_wm + 4 <= len(psram) and wm_words < 0x2000
               and buf_u32(psram, off_wm) == MAGIC):
            wm_words += 1
            off_wm += 4
        remaining = wm_words * 4   # 剩余栈字节数 (越少越危险; 溢出时栈底被破坏=0)
        # 栈向下增长, 合法范围 [pxStack, pxStack+size). 若 top < pstack -> 溢出
        overflow = top < pstack
        cur_mark = " *CUR*" if tcb_addr == cur_tcb else ""
        ovf_mark = " <==栈溢出!" if overflow else ""
        hi_mark = " ⚠高水位" if (not overflow and remaining < 512) else ""
        if overflow:
            overflow_tasks.append((name, tcb_addr, pstack - top))
        if not overflow and remaining < 512:
            highwater_tasks.append((name, tcb_addr, remaining))
        mk_str = "OK(%d)" % magic_ok if magic_bad == 0 else "破坏(%d/%d:%s)" % (
            magic_ok, magic_ok + magic_bad, ",".join(bad_vals))
        print(" 0x%08x %-14s %-5d 0x%08x 0x%08x %-5d %-7d %s%s%s%s" % (
            tcb_addr, name[:14], prio, top, pstack, tcbnum or 0, remaining,
            mk_str, cur_mark, ovf_mark, hi_mark))
    print("=" * 100)
    print("共 %d 个任务" % len(tasks))

    if overflow_tasks:
        print("\n>>> 栈溢出任务 (pxTopOfStack < pxStack):")
        for name, addr, depth in overflow_tasks:
            print("    %s (TCB 0x%08x) 溢出约 %d 字节" % (name, addr, depth))
    if highwater_tasks:
        print("\n>>> 栈高水位任务 (剩余 < 512B, 接近溢出 — 下一个潜在溢出者):")
        for name, addr, rem in sorted(highwater_tasks, key=lambda x: x[2]):
            print("    %-16s (TCB 0x%08x) 剩余仅 %d 字节" % (name, addr, rem))

    # 当前任务栈底破坏详情
    if cur_tcb:
        print("\n--- 当前任务栈底 magic 破坏详情 ---")
        cur = [t for t in tasks if t[0] == cur_tcb]
        if cur:
            _, name, _, top, pstack, _ = cur[0]
            print("任务 %r, pxStack=0x%08x, pxTopOfStack=0x%08x" % (name, pstack, top))
            print("栈底前 0x40 字节 (应全为 0x%08x, 破坏值反查破坏源):" % MAGIC)
            bad_code = []
            for i in range(0, 0x40, 4):
                off = pstack - PSRAM_BASE
                v = buf_u32(psram, off + i) if 0 <= off + i + 4 <= len(psram) else 0
                mark = " magic OK" if v == MAGIC else ""
                if v != MAGIC and v and syms.is_exec_code(v):
                    fn = addr2line_batch(addr2line, elf, [v]).get(thumb_real(v), ("?", "?"))[0]
                    mark = " <- CODE: %s (破坏源嫌疑)" % fn[:38]
                    bad_code.append((i, v, fn))
                print("  [pxStack+0x%-2x] = 0x%08x%s" % (i, v, mark))
            if bad_code:
                print("  >>> 栈底破坏值含代码地址, 指向栈溢出破坏源调用链:")
                for i, v, fn in bad_code:
                    print("      +0x%-2x 0x%08x -> %s" % (i, v, fn[:58]))


if __name__ == "__main__":
    main()
