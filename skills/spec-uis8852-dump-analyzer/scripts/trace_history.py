#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 scheduler / IRQ trace history — reconstruct what ran before the crash.

Reads the kernel ring buffers (kservice.c):
  - osThreadSwapArray[100] {osThread_t thread, uint32 time}  — every thread switch-in
  - osIrqSwapArray[100]    {uint32 irq, uint32 in, uint32 out} — every IRQ entry/exit

This is the key evidence for crashes with a "frozen" static snapshot:
  - WDT timeout:        find the task that ran longest (biggest time gap), or an
                        IRQ storm (one IRQ repeating densely).
  - Deadlock/starvation: see which task held the CPU, and which never got scheduled.
  - Crash context:      the last switch-in == g_osCurrentThread (sanity check).

Usage:  python trace_history.py <dump_dir> <ap.elf>
"""
import os, sys
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

OS_THREAD_TRACE_NUM = 100
OS_IRQ_TRACE_NUM = 100


def thread_name(mem, tcb, o_name, cache):
    if tcb in cache:
        return cache[tcb]
    if not tcb or tcb < 0x1000:
        cache[tcb] = "0x%08x" % (tcb or 0)
        return cache[tcb]
    name_p = mem.try_u32(tcb + o_name)
    s = None
    if name_p and 0x00008000 <= name_p < 0x100000000:
        try:
            s = mem.cstr(name_p, 20)
        except Exception:
            s = None
    if s and len(s) >= 2 and all(33 <= ord(c) < 127 for c in s) and "." not in s and "/" not in s:
        cache[tcb] = s
    else:
        cache[tcb] = "0x%08x" % tcb
    return cache[tcb]


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    S = lambda n: syms.lookup(n)[0]
    o_thread = syms.struct_offsets("osThread_t")
    o_name = o_thread.get("name", 4)

    print("=" * 92)
    print(" 任务调度 / 中断 切换历史（崩溃前发生了什么）")
    print("=" * 92)
    print(" # 用途：静态快照看不出问题时，靠时序还原。WDT超时/死锁/饿死/中断风暴的关键证据")

    cache = {}
    ct = mem.try_u32(S("g_osCurrentThread") or 0)

    # ---------------- thread switch history ----------------
    arr = S("osThreadSwapArray"); off = S("osThreadSwapOffset")
    if not (arr and off):
        print("\n[thread trace] OS_USING_THREAD_TRACE not enabled (symbols absent).")
    else:
        pos = mem.try_u16(off) or 0
        # Read all 100 entries; (slot, tcb, time). Empty slots (tcb==0) skipped.
        entries = []
        for i in range(OS_THREAD_TRACE_NUM):
            tcb = mem.try_u32(arr + i * 8)
            t = mem.try_u32(arr + i * 8 + 4)
            if tcb:
                entries.append((i, tcb, t))
        # Order by write sequence: the ring writes 0,1,...,pos-1, then wraps.
        # If pos < count of non-zero and array not yet wrapped, order is by slot.
        # Robust: order by time (monotonic osGetSysTimeCnt). Detect wrap by slot.
        ordered = sorted(entries, key=lambda e: e[2])  # by time
        # If time wrapped (32-bit), slot order is safer; use slot order if times non-monotonic
        slot_ordered = sorted(entries, key=lambda e: e[0])

        # Decide wrapped: if last-written slot index >= pos-1 sequence is contiguous
        wrapped = len(entries) == OS_THREAD_TRACE_NUM
        seq = slot_ordered if wrapped else [e for e in slot_ordered if e[0] < pos]
        # for wrapped, the write order is: pos, pos+1, ..., 99, 0, 1, ..., pos-1
        if wrapped:
            seq = sorted(entries, key=lambda e: ((e[0] - pos) % OS_THREAD_TRACE_NUM))

        print("\n[任务切换历史] osThreadSwapOffset=%d, 已记录=%d, %s" %
              (pos, len(entries), "环形已满(WRAPPED)" if wrapped else "(环形未满)"))

        # 展示最近 N 次切换（最新的在最后）
        N = min(30, len(seq))
        recent = seq[-N:] if N else []
        prev_t = None
        print("  最近 %d 次任务切换(旧->新；gap=距上次切换的 tick 数=运行时长):" % N)
        for slot, tcb, t in recent:
            nm = thread_name(mem, tcb, o_name, cache)
            gap = ("+%-9d" % (t - prev_t)) if (prev_t is not None and t >= prev_t) else ("?         ")
            mark = "  <- 当前任务" if tcb == ct else ""
            print("  slot[%3d] t=%-11d %s %s%s" % (slot, t, gap, nm[:24], mark))
            prev_t = t

        # 任务被调度频率（谁占 CPU 最多）
        cnt = Counter(thread_name(mem, tcb, o_name, cache) for _, tcb, _ in entries)
        print("\n  切换频率(全部 %d 条；频率高=占CPU多，idle 高=正常空闲):" % len(entries))
        for nm, c in cnt.most_common(10):
            print("    %5d  (%4.1f%%)  %s" % (c, 100.0 * c / len(entries), nm[:30]))

        # last switch-in should be current thread
        if seq:
            last_tcb = seq[-1][1]
            if last_tcb != ct:
                print("\n  NOTE: last switch-in (%s) != g_osCurrentThread (%s) — crash may"
                      "\n        have hit mid-scheduling or current thread is the ISR victim."
                      % (thread_name(mem, last_tcb, o_name, cache),
                         thread_name(mem, ct, o_name, cache)))

    # ---------------- IRQ history ----------------
    iarr = S("osIrqSwapArray"); ioff = S("osIrqSwapOffset")
    if not (iarr and ioff):
        print("\n[irq trace] OS_USING_IRQ_TRACE not enabled (symbols absent).")
        return
    ipos = mem.try_u16(ioff) or 0
    irq_entries = []
    for i in range(OS_IRQ_TRACE_NUM):
        irq = mem.try_u32(iarr + i * 12)
        tin = mem.try_u32(iarr + i * 12 + 4)
        tout = mem.try_u32(iarr + i * 12 + 8)
        if irq or tin or tout:
            irq_entries.append((i, irq, tin, tout))
    wrapped = len(irq_entries) == OS_IRQ_TRACE_NUM
    if wrapped:
        seqi = sorted(irq_entries, key=lambda e: ((e[0] - ipos) % OS_IRQ_TRACE_NUM))
    else:
        seqi = [e for e in sorted(irq_entries, key=lambda e: e[0]) if e[0] < ipos]

    print("\n[中断历史] osIrqSwapOffset=%d, 已记录=%d, %s" %
          (ipos, len(irq_entries), "WRAPPED" if wrapped else "(未满)"))
    Ni = min(20, len(seqi))
    print("  最近 %d 次中断(旧->新; ext=外部中断号=irq-19; dur=耗时 tick):" % Ni)
    for slot, irq, tin, tout in seqi[-Ni:] if Ni else []:
        dur = (tout - tin) if (tout is not None and tin is not None and tout >= tin) else None
        ds = ("%d" % dur) if dur is not None else "?"
        ext = (irq - 19) if irq is not None else None
        print("  slot[%3d] irq=%-3d (ext=%-3d) in=%-11d out=%-11d dur=%s" %
              (slot, irq or 0, ext if ext is not None else -1, tin or 0, tout or 0, ds))

    if irq_entries:
        icnt = Counter(e[1] for e in irq_entries)
        print("\n  中断频率(全部 %d 条；某中断 >30%% = 疑似中断风暴):" % len(irq_entries))
        for irq, c in icnt.most_common(10):
            ext = (irq - 19) if irq else -1
            storm = "  <-- 疑似中断风暴 IRQ storm" if c > 30 else ""
            print("    %5d  irq=%-3d (外部号ext=%-3d)%s" % (c, irq, ext, storm))


if __name__ == "__main__":
    main()
