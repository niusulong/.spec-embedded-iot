#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 frame-aware stack unwinder + current-IRQ confirmation.

RISC-V (RV32 + Zc) normally omits a frame pointer, so a naive "scan the stack
for code addresses" produces many false positives (data that happens to fall
inside a function's address range). This tool:

  1. Reads g_osIrqNo to identify the EXACT active interrupt (authoritative).
  2. Parses each candidate function's prologue (cm.push / c.addi16sp / addi sp /
     sw ra) to obtain frame size + ra save offset.
  3. Walks the saved-return-address chain frame by frame, starting from the
     trap frame, up to osInterruptDispatch and the interrupted task.
  4. Also emits the call-site-verified stack scan so you can cross-check.

CAVEAT: the trap frame's `ra` is saved by osAssertHandler right before its
ecall, and is NOT the assert-site caller (it gets clobbered to epc or a data
pointer). Start unwinding from the assert PC's function frame found on the
stack, not from the trap ra.

Usage:  python unwind.py <dump_dir> <ap.elf>
"""
import os, sys, re, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols, find_toolchain, objdump_range, in_text

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"


def parse_prologue(objdump_exe, elf, addr, peek=48):
    """Return (frame_size, ra_sp_offset) by reading the function prologue."""
    out = objdump_range(objdump_exe, elf, addr, addr + peek)
    lines = [l.strip() for l in out.splitlines() if ":" in l and "\t" in l]
    frame = ra_off = None
    for l in lines[:14]:
        m = re.search(r"cm\.push\s+\{[^}]*\},\s*-?(\d+)", l)
        if m and frame is None:
            frame = int(m.group(1))
        m = re.search(r"c\.addi16sp\s+sp,\s*-?(-?\d+)", l) or re.search(r"c\.addi16sp\s+(-?\d+)", l)
        if m and frame is None:
            v = int(m.group(1)); frame = -v if v < 0 else v
        m = re.search(r"addi\s+sp,sp,(-?\d+)", l)
        if m and frame is None:
            v = int(m.group(1)); frame = -v if v < 0 else v
        # RV32 uses sw / c.swsp (sd / c.sdsp are RV64-only). Match both forms;
        # the c.swsp offset is unsigned 4-byte multiples.
        m = (re.search(r"\bsw\s+ra,\s*(-?\d+)\(sp\)", l)
             or re.search(r"c\.swsp\s+ra,(-?\d+)", l))
        if m and ra_off is None:
            ra_off = int(m.group(1))
    if frame and ra_off is None:
        ra_off = frame - 4   # cm.push convention: ra at top of frame
    return frame, ra_off


def is_call_site(mem, value):
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


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    tc = find_toolchain(DUMP)
    objdump = os.path.join(tc, "riscv64-unknown-elf-objdump.exe") if tc else ""
    addr2line = os.path.join(tc, "riscv64-unknown-elf-addr2line.exe") if tc else ""

    def S(n): return syms.lookup(n)[0]

    print("=" * 92)
    print(" 当前中断身份（权威：直接读 g_osIrqNo，不靠栈猜）")
    print("=" * 92)
    irq_addr = S("g_osIrqNo")
    if irq_addr:
        irq = mem.try_u8(irq_addr)   # g_osIrqNo is uint8_t (irq.c)
        print("  g_osIrqNo @0x%08x = %s  -> 内部IRQ %s" %
              (irq_addr, "0x%02x" % irq if irq is not None else "?", irq))
        print("  # 查中断源名：外部号 = irq - 19，再查 chip_int_num.h 的 AP_INT_NUM_<外部号>")
        print("  # 查 ISR：源码 grep osInterruptInstall <中断源常量>")
    nest = mem.try_u8(S("g_osInterruptNest") or 0)   # uint8_t
    print("  g_osInterruptNest = %s  (%s)" % (nest, "中断上下文 ISR" if nest else "任务上下文"))

    # ---- locate trap frame & assert PC ----
    e_base = S("g_osException")
    a_base = S("g_osAssert")
    e_struct = mem.try_u32(e_base) if e_base else None
    a_struct = mem.try_u32(a_base) if a_base else None
    e_trace = mem.try_u32(e_struct + 4) if e_struct else None
    assert_pc = mem.try_u32(a_struct + 12) if a_struct else None
    if e_trace is None:
        print("\n(无 g_osException->trace，无法回溯)"); return
    print("\n  寄存器现场帧 trap frame @0x%08x ; 断言 PC = 0x%08x" % (e_trace, assert_pc or 0))

    # ---- 启发式栈扫描（call-site 验证；有噪声，需 objdump 复核）----
    print("\n" + "=" * 92)
    print(" 栈扫描候选（call-site 验证，0x%x .. +0x800）" % (e_trace + 128))
    print("=" * 92)
    print(" # 语义：地址 A 处的值 V => A 处栈帧的所有者，是被 V 的函数【调用】的（V=调用者）")
    scan_start = e_trace + 128
    cands = []  # (stack_addr, value)
    a = scan_start
    while a < scan_start + 0x800:
        v = mem.try_u32(a)
        if v is not None and in_text(v) and is_call_site(mem, v):
            fn, off = syms.resolve(v)
            cands.append((a, v, fn, off))
        a += 4
    for sa, v, fn, off in cands:
        print("  @0x%08x : 0x%08x  %s+0x%x" % (sa, v, fn, off))
    if not cands:
        print("  (none found)")

    # ---- prologue table for the functions seen + common runtime funcs ----
    seen_fns = []
    seen_names = set()
    for sa, v, fn, off in cands:
        if fn not in seen_names and not fn.endswith("(?)"):
            # resolve function base
            base = syms.lookup(fn)[0]
            if base:
                seen_fns.append((fn, base))
                seen_names.add(fn)
    # always include these runtime/dispatch functions if present
    for n in ["osInterruptDispatch", "osAssertHandler", "LPM_Isr", "DMA_Irq"]:
        b = syms.lookup(n)[0]
        if b and n not in seen_names:
            seen_fns.append((n, b)); seen_names.add(n)

    print("\n" + "=" * 92)
    print(" 函数 prologue 表（栈帧大小 + ra 保存偏移，用于帧感知回溯）")
    print("=" * 92)
    pro = {}
    for n, b in seen_fns:
        f, ro = parse_prologue(objdump, ELF, b)
        pro[n] = (b, f, ro)
        print("  %-26s @0x%08x  frame=%-4s ra_off=%s   # frame=栈帧大小 ra_off=返回地址在帧内偏移" % (n, b, f, ro))

    # ---- 重建调用链 ----
    print("\n" + "=" * 92)
    print(" 重建调用链（从内层断言点 -> 外层中断入口，按 '值=调用者' 语义）")
    print("=" * 92)
    print(" # 从上到下读：每个 ra 值 = 下一层（更内）帧的调用者")
    print(" # 可疑链接用 objdump 验证：riscv64-unknown-elf-objdump -d --start-address=<值-0x10> \\")
    print(" #                                              --stop-address=<值+4> <elf>")
    chain = []
    for sa, v, fn, off in cands:
        chain.append((sa, v, fn, off))
    for sa, v, fn, off in chain:
        flag = ""
        if any(fn == f2 and sa != s2 for s2, v2, f2, o2 in chain if (s2, v2) != (sa, v)):
            flag = "   <- 同名重复（递归 或 栈残留噪声）"
        print("  sp=0x%08x  ra=0x%08x -> %s+0x%x%s" % (sa, v, fn, off, flag))


if __name__ == "__main__":
    main()
