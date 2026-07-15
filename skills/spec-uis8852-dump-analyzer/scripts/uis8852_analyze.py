#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 AP crash-dump entry-point analyzer.

Reconstructs the panic context from a DTools ramdump + AP ELF:
  - build revision, gIsPanic, abort type, g_osErrorLog
  - g_osAssert / g_osException (dereferenced — these are POINTERS)
  - rt_hw_stack_frame (32 saved regs)
  - current IRQ (g_osIrqNo), interrupt nest, current thread
  - heuristic stack-scan backtrace + addr2line source resolution

Usage:  python uis8852_analyze.py <dump_dir> <ap.elf>
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Mem, Symbols, find_toolchain, addr2line_batch, in_text)

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

FRAME_FIELDS = ["epc", "ra", "mstatus", "gp", "tp", "t0", "t1", "t2", "s0_fp", "s1",
                "a0", "a1", "a2", "a3", "a4", "a5", "a6", "a7", "s2", "s3", "s4", "s5",
                "s6", "s7", "s8", "s9", "s10", "s11", "t3", "t4", "t5", "t6"]


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    tc = find_toolchain(DUMP)
    addr2line = os.path.join(tc, "riscv64-unknown-elf-addr2line.exe") if tc else ""
    print("(toolchain: %s)" % tc)

    def S(n):
        return syms.lookup(n)[0]

    print("=" * 92)
    print(" UIS8852 / N706C  AP 死机现场分析（crash-dump analysis）")
    print("=" * 92)

    # ---- 版本 ----
    rev_addr = S("gBuildRevision")
    if rev_addr:
        print("固件版本 Build revision : %s" % mem.cstr(rev_addr, 80).strip())

    # ---- panic 标志 ----
    ispanic = mem.try_u32(S("gIsPanic") or 0)
    abort_u8 = mem.u8(S("gBlueScreenAbortType")) if S("gBlueScreenAbortType") else None
    print("\n--- 死机类型 ---")
    print("gIsPanic       : %s   # =1 表示设备进入了 panic（蓝屏抓 dump 的状态）" % ispanic)
    if abort_u8 is not None:
        at = abort_u8
        if at == 0xFE:
            kind = "ASSERT（软件断言：代码里 OS_ASSERT 条件不成立，主动abort；非硬件异常）"
        else:
            kind = "EXCEPTION（硬件异常，值=mcause 异常码 0x%02x）" % at
        print("AbortType      : 0x%02x  -> %s" % (at, kind))

    # ---- 错误日志（蓝屏字符串，最直接的崩溃摘要）----
    errlog_addr = S("g_osErrorLog")
    if errlog_addr:
        print("g_osErrorLog   : %r   # 蓝屏错误摘要（含 assert 文件/行/PC）" % mem.cstr(errlog_addr, 200).strip())

    # ---- g_osAssert（断言结构，注意是指针需二次解引用）----
    a_base = S("g_osAssert")
    if a_base:
        a_struct = mem.u32(a_base)  # 二次解引用（指向 IRAM 中的结构）
        if a_struct:
            print("\n--- g_osAssert 断言结构 @0x%08x -> 实际结构 @0x%08x ---" % (a_base, a_struct))
            a_core = mem.try_u32(a_struct + 0)
            a_file_ptr = mem.try_u32(a_struct + 4)
            a_line = mem.try_u32(a_struct + 8)
            a_pc = mem.try_u32(a_struct + 12)
            a_file = mem.cstr(a_file_ptr, 64) if a_file_ptr else "?"
            print("  core = %s (%s)   # 哪个核断言" % (a_core, "AP" if a_core == 1 else ("CP" if a_core == 2 else "?")))
            print("  file = %s.c   (ptr 0x%08x)   # 断言所在源文件" % (a_file, a_file_ptr))
            print("  line = %s   # 断言所在行号（__LINE__）" % a_line)
            print("  pc   = 0x%08x   # 断言调用点的返回地址（指向断言所在函数）" % a_pc)

    # ---- g_osException（异常结构）----
    e_base = S("g_osException")
    if e_base:
        e_struct = mem.u32(e_base)
        if e_struct:
            e_core = mem.try_u32(e_struct + 0)
            e_trace = mem.try_u32(e_struct + 4)
            e_mcause = mem.try_u32(e_struct + 8)
            e_mdcause = mem.try_u32(e_struct + 12)
            e_mepc = mem.try_u32(e_struct + 16)
            e_mtval = mem.try_u32(e_struct + 20)
            print("\n--- g_osException 异常结构 @0x%08x -> 实际结构 @0x%08x ---" % (e_base, e_struct))
            print("  core     = %s (%s)" % (e_core, "AP" if e_core == 1 else ("CP" if e_core == 2 else "?")))
            print("  trace    = 0x%08x  (-> rt_hw_stack_frame 保存的寄存器现场)" % e_trace)
            mc = e_mcause or 0
            is_int = bool(mc & 0x80000000)
            code = mc & 0x1f
            mcause_cn = {0:"指令地址未对齐",1:"取指访问错误",2:"非法指令",3:"断点",
                         4:"load 未对齐",5:"load 访问错误(常为空指针读)",6:"store 未对齐",
                         7:"store 访问错误(常为空指针写)",11:"ECALL(osAssertHandler 主动陷入=其实是断言)"}.get(code, "?")
            print("  mcause   = 0x%08x  (%s code=%d)  # 异常原因：%s" % (mc, "中断INT" if is_int else "异常EXC", code, mcause_cn))
            print("  mdcause  = 0x%08x  (code=%d)     # 平台扩展损坏原因(1=PMP违例 2=总线错误)" % (e_mdcause, (e_mdcause or 0) & 0x3))
            print("  mepc     = 0x%08x     # 触发异常的指令地址（ASSERT 时=osAssertHandler 的 ecall）" % e_mepc)
            print("  mtval    = 0x%08x     # 异常附加信息（访问错误时=非法访问地址）" % e_mtval)

            # ---- rt_hw_stack_frame（ecall 时刻的 32 个寄存器现场）----
            if e_trace:
                print("\n--- rt_hw_stack_frame 寄存器现场 @0x%08x  (ecall 瞬间 32 个寄存器) ---" % e_trace)
                print("  说明：ra 常被 osAssertHandler 覆盖（不可信）；s0_fp 常等于断言 PC；要看调用者请用 unwind.py")
                regs = {}
                for i, fld in enumerate(FRAME_FIELDS):
                    regs[fld] = mem.try_u32(e_trace + i * 4)
                for fld in FRAME_FIELDS:
                    v = regs[fld]
                    print("  %-6s = %s" % (fld, "0x%08x" % v if v is not None else "?"))

    # ---- 执行上下文：哪个中断、嵌套、被中断的任务 ----
    print("\n--- 执行上下文（崩溃发生在哪）---")
    irq_addr = S("g_osIrqNo")
    nest_addr = S("g_osInterruptNest")
    ct_addr = S("g_osCurrentThread")
    if irq_addr:
        irq = mem.try_u8(irq_addr)      # g_osIrqNo is uint8_t (irq.c)
        print("  g_osIrqNo          @0x%08x = %s (内部IRQ %s)  # 当前中断号；查源=irq-19 再查 AP_INT_NUM_*"
              % (irq_addr, "0x%02x" % irq if irq is not None else "?", irq if irq is not None else "?"))
    if nest_addr:
        nest = mem.try_u8(nest_addr)    # g_osInterruptNest is uint8_t (irq.c)
        ctx = "中断上下文(ISR)" if nest else "任务上下文"
        print("  g_osInterruptNest  @0x%08x = %s   # 中断嵌套深度（>=1=%s）" % (nest_addr, nest, ctx))
    if ct_addr:
        ct = mem.try_u32(ct_addr)
        print("  g_osCurrentThread  @0x%08x -> 0x%08x  # 当前任务（ISR 时=被中断的任务）" % (ct_addr, ct or 0))

    # ---- 启发式栈回溯（call-site 验证；可能含噪声，重要链用 unwind.py 复核）----
    print("\n" + "=" * 92)
    print(" 调用栈回溯 BACKTRACE（启发式扫描，可能含噪声；精确链请用 unwind.py / objdump 复核）")
    print("=" * 92)

    def is_call_site(value):
        """True if instr right before `value` is a RISC-V call (jal/jalr/c.jal/c.jalr)."""
        try:
            b1 = mem.read(value - 4, 1)[0]
        except Exception:
            return False
        if b1 in (0xef, 0xe7, 0x67):
            return True
        try:
            h2 = struct.unpack("<H", mem.read(value - 2, 2))[0]
        except Exception:
            return False
        op = h2 & 0x3
        if op == 1 and (h2 & 0x1FFC) != 0 and (h2 & 0xE000) == 0x2000:
            return True
        if op == 2 and (h2 & 0x0F80) != 0 and (h2 & 0xE000) == 0x8000:
            return True
        return False

    # scan anchors: frame sp (= trace + 128) and current thread sp
    anchors = []
    if e_trace:
        anchors.append(("frame_sp", e_trace + 128))
    addrs = []
    for label, start in anchors:
        print("\n[%s] scanning from 0x%08x :" % (label, start))
        a = start
        cnt = 0
        while a < start + 0x1000:
            v = mem.try_u32(a)
            if v is None:
                a += 4
                continue
            if in_text(v) and is_call_site(v):
                fn, off = syms.resolve(v)
                print("   0x%08x -> 0x%08x  %s+0x%x" % (a, v, fn, off))
                addrs.append(v)
                cnt += 1
                if cnt >= 32:
                    break
            a += 4
        if cnt == 0:
            print("   (no call-site return addresses found in this range)")

    # ---- addr2line（栈上代码地址 → 源码函数/行号）----
    if addrs:
        print("\n" + "=" * 92)
        print(" 源码定位 SOURCE RESOLUTION（栈上代码地址 → 函数名:源文件:行号）")
        print("=" * 92)
        print("  # 格式：代码地址  函数名  源文件:行号；最底一行=崩溃点，往上=调用者")
        res = addr2line_batch(addr2line, ELF, addrs)
        seen = set()
        for a in addrs:
            if a in seen:
                continue
            seen.add(a)
            fn, fl = res.get(a, (None, None))
            if not fn:
                fn2, off = syms.resolve(a)
                fn, fl = fn2, "%s+0x%x (no addr2line)" % (fn2, off)
            print("  0x%08x  %-30s  %s" % (a, (fn or "")[:30], fl))

    print("\n" + "=" * 92)
    print(" 深入分析（更干净准确的调用链 + 精准结论）")
    print("=" * 92)
    print("  上面的调用链是【启发式扫描】，RISC-V 无帧指针时常含噪声。优先看：")
    print("  → 09_unwind_cfi.txt   DWARF .debug_frame CFI 确定性逐帧回溯（崩溃链 + 全任务逐线程链）")
    print("  → 10_assert_reason.txt 断言模式推理：scheduler 栈检查断言自动判定【哪个线程栈溢出】+ 根因链")
    print("     （仅当 ELF 含 .debug_frame；缺失时回退到上面的启发式 02_unwind.txt）")


if __name__ == "__main__":
    main()
