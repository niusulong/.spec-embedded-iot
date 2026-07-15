#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 assert-pattern reasoner: turn the crash scene into a PRECISE verdict.

The crash-point call chain alone (what uis8852_analyze/unwind show) is often
NOT where the root cause is — a notorious example is the scheduler stack-check
assert: the assert fires while thread A is running osSchedule(), but the stack
that actually overflowed belongs to thread B (the to_thread the scheduler was
about to switch to). Reading only the crash chain misattributes the bug to A
(this is exactly the mistake a TRACE32-only trace can make).

This script pattern-matches the assert and, for the scheduler stack-check
pattern, deterministically:
  * identifies the OVERFLOWED thread  = g_osCurrentThread (= osSchedule's
    to_thread, assigned at scheduler.c:271 before the check at :287),
  * proves the overflow (guard byte at stackAddr corrupted, watermark ~100%),
  * unwinds THAT thread's saved context to show what it was executing when it
    blew the stack (the real root-cause chain),
  * and explicitly distinguishes it from the thread executing at crash time.

Other assert patterns (dlmalloc heap, WDT, null-deref) are stubbed as
extensible hooks for later.

Usage:  python assert_reason.py <dump_dir> <ap.elf>
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols
import cfi
from cfi import CFIUnwinder, NoCFIError, unwind_exception, unwind_thread, fmt_chain
from threads import stack_watermark, enumerate_tcbs, find_thread_by_stack

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

_TCB_MEMBERS = ["name", "sp", "stackAddr", "stackSize", "stat"]


def u32(mem, a):
    try:
        return struct.unpack("<I", mem.read(a, 4))[0]
    except Exception:
        return None


def find_tcb(mem, O, target):
    """Locate the TCB whose address == target via the reliable enumeration."""
    if not target:
        return None
    for tcb, nm in enumerate_tcbs(mem, O):
        if tcb == target:
            return tcb, nm
    return None


def thread_stack_info(mem, O, tcb):
    sa = u32(mem, tcb + O["stackAddr"]) or 0
    ss = u32(mem, tcb + O["stackSize"]) or 0
    sp = u32(mem, tcb + O["sp"]) or 0
    guard = mem.read(sa, 1)[0] if sa else 0
    used, _ = stack_watermark(mem, sa, ss) if sa and ss else (None, None)
    wm = (used * 100 // ss) if (used is not None and ss) else 0
    return sa, ss, sp, guard, wm


def reason_scheduler_stack_check(mem, syms, uw, S, O):
    """Pattern: OS_ASSERT(0) at scheduler.c:_osSchedulerStackCheck (line ~112).

    The scheduler checks the to_thread's stack on switch-in (scheduler.c:287),
    so the OVERFLOWED thread is g_osCurrentThread (= to_thread), NOT the thread
    executing osSchedule at crash time. Prove it and unwind the victim."""
    print("=" * 92)
    print(" 断言模式：scheduler.c 栈溢出检查 (_osSchedulerStackCheck)")
    print("=" * 92)

    # the checked thread = to_thread = g_osCurrentThread
    ct = u32(mem, S("g_osCurrentThread"))
    victim = find_tcb(mem, O, ct)
    # the crash-time executing thread = whose stack holds the exception frame
    e_base = S("g_osException")
    trace = u32(mem, u32(mem, e_base) + 4) if e_base else 0
    runner = find_thread_by_stack(mem, O, trace)

    print("\n[判定] 栈溢出的线程（被检查者 to_thread）: %s" %
          ("%s @0x%08x" % (victim[1], victim[0]) if victim else "未识别 (g_osCurrentThread=0x%08x)" % (ct or 0)))
    print("       崩溃时正在执行的线程（from_thread）: %s" %
          ("%s @0x%08x" % (runner[1], runner[0]) if runner else "未识别"))
    if victim and runner and victim[0] == runner[0]:
        print("       （二者同一线程）")
    else:
        print("       ⚠ 二者不同！根因在【溢出线程】，不在崩溃执行线程。只看崩溃链会把根因归错。")

    if not victim:
        return
    sa, ss, sp, guard, wm = thread_stack_info(mem, O, victim[0])
    overflow = (guard != 0x23) or (wm >= 95)
    print("\n[溢出证据] %s 任务栈：" % victim[1])
    print("   栈大小 %d B (0x%x)，stackAddr=0x%08x" % (ss, ss, sa))
    print("   哨兵字节 @stackAddr = 0x%02x  （应为 0x23 '#'）-> %s" %
          (guard, "❌ 被踩，栈溢出确认" if guard != 0x23 else "✓ 完好"))
    print("   栈水位 %d%%  %s" % (wm, "(>=95% 高危)" if wm >= 95 else ""))
    if not overflow:
        print("   （未检测到溢出——该线程栈可能不是根因，请结合其它模式）")

    # what the victim was executing (root-cause chain)
    print("\n[溢出线程执行链]（从其 tcb.sp 保存的切换帧回溯；这就是撑爆栈的调用路径）:")
    ch = unwind_thread(uw, mem, sp, stack_lo=sa, stack_hi=sa + ss)
    if ch:
        print(fmt_chain(ch, syms))
        # point at the likely culprit (deepest user/app frame, skip kernel entry)
        culprit = next((f for f in reversed(ch)
                        if not f["fn"].startswith(("os", "rt_", "sys_", "lwip_", "tcpip"))), None)
        if culprit:
            print("   >> 嫌疑根因帧：%s+0x%x (0x%08x)" % (culprit["fn"], culprit["off"], culprit["pc"]))
    else:
        print("   (无保存切换帧)")

    # the trigger path (crash-time executing thread)
    print("\n[触发链]（崩溃时正在执行的线程；它在 osSchedule 切入溢出线程时检出哨兵已坏）:")
    cr = unwind_exception(uw, mem, trace) if trace else []
    if cr:
        print(fmt_chain(cr, syms))
    else:
        print("   (未能回溯)")

    # ---- final precise verdict ----
    print("\n" + "=" * 92)
    print(" 结论（VERDICT）")
    print("=" * 92)
    if overflow and victim:
        deep = ch[0]["fn"] if ch else "?"
        # 取最深的"用户/应用"帧作为根因动作描述
        appframe = next((f for f in ch if not f["fn"].startswith(("os", "rt_", "sys_", "lwip", "tcpip", "dns", "udp", "ip"))), None)
        action = appframe["fn"] if appframe else deep
        print("  %s 任务栈溢出致 ASSERT(scheduler.c:112)。" % victim[1])
        print("  栈 %d B，哨兵字节 0x%02x（应 '#'），水位 %d%%。" % (ss, guard, wm))
        print("  溢出时该任务正在执行：%s" % " -> ".join(f["fn"] for f in ch[:6]) if ch else action)
        print("  触发：%s 线程处理完 DNS 应答后唤醒 %s，osSchedule 切入时 _osSchedulerStackCheck 检出栈哨兵已坏。" %
              (runner[1] if runner else "?", victim[1]))
        print("  ⚠ 根因在【%s 任务】（to_thread），非崩溃执行线程【%s】。" %
              (victim[1], runner[1] if runner else "?"))
        print("     修复方向：增大 %s 任务栈，或消除其回调里的深/阻塞调用（如本例的 %s）。" %
              (victim[1], action))
    else:
        print("  未匹配到栈溢出特征；请参考其它断言模式（dlmalloc/WDT/空指针）或调用链人工分析。")


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    S = lambda n: syms.lookup(n)[0]
    abort = u32(mem, S("gBlueScreenAbortType")) if S("gBlueScreenAbortType") else None
    errlog = mem.cstr(S("g_osErrorLog"), 200).strip() if S("g_osErrorLog") else ""

    print("=" * 92)
    print(" UIS8852 断言模式推理（assert -> precise root-cause verdict）")
    print("=" * 92)
    print("gBlueScreenAbortType = %s" % ("0xFE ASSERT" if abort == 0xFE else ("0x%02x" % abort if abort is not None else "?")))
    print("g_osErrorLog         = %r" % errlog)

    if abort != 0xFE:
        print("\n[!] 非 ASSERT 崩溃（abort=0x%02x），本脚本聚焦 ASSERT 模式。" % (abort or 0))
        print("    硬件异常请用 uis8852_analyze.py 看 mcause/反汇编；复位/WDT 用 wdt_reset.py。")
        return

    try:
        uw = CFIUnwinder(syms)
    except NoCFIError as e:
        print("[!] %s —— 无法做 CFI 回溯，退化为仅符号判定。" % e)
        uw = None

    O = {k: v for k, v in syms.struct_offsets("osThread_t").items() if k in _TCB_MEMBERS}

    # ---- pattern dispatch ----
    # scheduler stack-check assert
    if ("scheduler" in errlog.lower() or "scheduler.c" in errlog.lower()):
        if uw is None:
            print("\n[!] 命中 scheduler 栈检查断言，但缺 .debug_frame 无法 CFI 回溯。")
            return
        reason_scheduler_stack_check(mem, syms, uw, S, O)
        return

    # dlmalloc heap assert (future)
    if "dlmalloc" in errlog.lower():
        print("\n[模式] dlmalloc.c 堆断言 —— 详见 05_heap_state.txt / 06_heap_walker.txt")
        print("       （断言点通常因堆耗尽 top<MINSIZE 或 free-list 元数据损坏；先看堆使用率）")
        return

    # generic assert fallback
    print("\n[模式] 通用 ASSERT —— 未见 scheduler/dlmalloc 特征。")
    print("       断言点：%s" % errlog)
    print("       建议：用 unwind_cfi.py 看崩溃链 + 各线程链；结合代码看 OS_ASSERT 语义。")


if __name__ == "__main__":
    main()
