#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 deterministic DWARF-CFI backtrace (cleaner than unwind.py heuristics).

Produces three sections, all driven by .debug_frame Call Frame Information
(the same mechanism TRACE32/GDB use), so the chains are frame-accurate with no
heuristic noise (no duplicate frames, no stale-residue frames):

  1. Crash-context chain  — the executing thread's call chain at the assert/
     exception (unwound from the saved exception frame g_osException->trace).
  2. Per-thread chains     — every SUSPENDED thread, unwound from its saved
     switch frame (tcb.sp). This is where the REAL root-cause chain usually
     hides (e.g. a timer task running a keepalive callback that does blocking
     DNS) — the crash-point chain alone only shows who tripped the assert.
  3. Cross-reference        — which thread the trap frame belongs to, and which
     thread g_osCurrentThread points at (the scheduler's to_thread).

Falls back gracefully to unwind.py when the ELF has no .debug_frame.

Usage:  python unwind_cfi.py <dump_dir> <ap.elf>
"""
import os
import sys
import struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols
import cfi
from cfi import CFIUnwinder, NoCFIError, unwind_exception, unwind_thread, fmt_chain
from threads import stack_watermark, enumerate_tcbs   # shared reliable TCB scan

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

# osThread_t members we rely on (resolved via DWARF)
_TCB_MEMBERS = ["name", "sp", "stackAddr", "stackSize", "stat", "currentPriority"]


def u32(mem, a):
    try:
        return struct.unpack("<I", mem.read(a, 4))[0]
    except Exception:
        return None


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    S = lambda n: syms.lookup(n)[0]

    print("=" * 92)
    print(" UIS8852 DWARF-CFI deterministic backtrace (frame-accurate, no heuristic noise)")
    print("=" * 92)
    try:
        uw = CFIUnwinder(syms)
    except NoCFIError as e:
        print("[!] %s" % e)
        print("    ELF 缺 .debug_frame，无法做 CFI 回溯。请改用启发式：python unwind.py <dump> <elf>")
        return
    print("已索引 %d 个 FDE (.debug_frame)" % len(uw._fdes))

    O = {k: v for k, v in syms.struct_offsets("osThread_t").items() if k in _TCB_MEMBERS}
    if not all(k in O for k in ("name", "sp", "stackAddr", "stackSize")):
        print("[!] osThread_t 偏移解析不全，无法枚举线程")
        return

    # ---- Section 1: crash context ----
    print("\n" + "-" * 92)
    print(" [1] 崩溃上下文链（异常/断言发生时正在执行的线程；trap 帧 ra 被 ecall 覆盖，CFI 重算真实返回链）")
    print("-" * 92)
    e_base = S("g_osException")
    a_base = S("g_osAssert")
    chain = []
    if e_base:
        e_struct = u32(mem, e_base)
        trace = u32(mem, e_struct + 4)
        mepc = u32(mem, e_struct + 16)
        abort = u32(mem, S("gBlueScreenAbortType")) if S("gBlueScreenAbortType") else None
        if abort == 0xFE and a_base:
            # ASSERT: trap frame's ra is clobbered; CFI from osAssertHandler works
            chain = unwind_exception(uw, mem, trace)
        else:
            # real exception: mepc is the faulting instruction
            chain = unwind_exception(uw, mem, trace)
        print("  mepc/epc=0x%08x  exception-trace=0x%08x  abortType=%s" %
              (mepc or 0, trace, ("0xFE ASSERT" if abort == 0xFE else "0x%02x" % abort if abort is not None else "?")))
        if chain:
            print(fmt_chain(chain, syms))
        else:
            print("  (未能从异常帧回溯)")

    # ---- identify the trap-frame owner + g_osCurrentThread ----
    ct = u32(mem, S("g_osCurrentThread")) if S("g_osCurrentThread") else None
    tcbs = enumerate_tcbs(mem, O)
    trap_owner = None
    if e_base:
        trace = u32(mem, u32(mem, e_base) + 4)
        for tcb, nm in tcbs:
            sa = u32(mem, tcb + O["stackAddr"]) or 0
            ss = u32(mem, tcb + O["stackSize"]) or 0
            if sa <= trace < sa + ss:
                trap_owner = (tcb, nm)
                break

    print("\n  >> 异常帧归属线程（崩溃时正在执行）: %s" %
          ("%s @0x%08x" % (trap_owner[1], trap_owner[0]) if trap_owner else "未识别"))
    cur_nm = None
    if ct:
        for tcb, nm in tcbs:
            if tcb == ct:
                cur_nm = nm
                break
        print("  >> g_osCurrentThread=0x%08x -> %s（调度器 osSchedule 切入前的 to_thread）" %
              (ct, cur_nm or "?"))

    # ---- Section 2: per-thread chains ----
    print("\n" + "-" * 92)
    print(" [2] 各挂起线程的执行链（从 tcb.sp 保存的切换帧回溯；根因常藏在这里）")
    print("-" * 92)
    for tcb, nm in tcbs:
        sa = u32(mem, tcb + O["stackAddr"]) or 0
        ss = u32(mem, tcb + O["stackSize"]) or 0
        sp = u32(mem, tcb + O["sp"]) or 0
        used, magic = stack_watermark(mem, sa, ss)
        wm = (used * 100 // ss) if (used is not None and ss) else 0
        tags = []
        if trap_owner and tcb == trap_owner[0]:
            tags.append("崩溃线程(见[1])")
        if ct and tcb == ct:
            tags.append("to_thread=g_osCurrentThread")
        if wm >= 90:
            tags.append("⚠栈溢出风险 %d%%" % wm)
        tag = ("  [" + ";".join(tags) + "]") if tags else ""
        print("\n  --- %s @0x%08x  栈%dB 水位%d%% %s ---" % (nm, tcb, ss, wm, tag))
        ch = unwind_thread(uw, mem, sp, stack_lo=sa, stack_hi=sa + ss)
        if ch:
            print(fmt_chain(ch, syms))
        else:
            print("    (无保存切换帧 / resume_pc=0：该线程即崩溃线程，其上下文在异常帧，见[1])")


if __name__ == "__main__":
    main()
