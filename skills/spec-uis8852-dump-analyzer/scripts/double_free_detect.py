#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 double-free detector for dlmalloc.c:2066 (check_mem_trace downflow).

When osFreeTrace's check_mem_trace fails at dlmalloc.c:2066 (downflow), the
trace.size field no longer holds the original value, so the down position is
computed wrong (down = trace + trace.size + 16) and a random byte is read !=
0x89. This script determines the REAL root cause by checking the victim block's
in-use/FREE state:

  - victim block FREE (next chunk PREV_INUSE=0 + free-list back-pointer)
      => DOUBLE-FREE  (trace.size was overwritten by dlmalloc's fd/bk on the
         first free; dlfree does NOT clear trace.magic so the head-magic check
         still passes)
  - victim block INUSE + trace.size corrupted
      => HEAP OVERFLOW (an overwrite stomped the osMemTrace_t header)

This is the critical distinction the static dump must make for 2066. You CANNOT
tell in-use from free by looking at trace.magic alone.

Usage:
  python double_free_detect.py <dump_dir> <ap.elf> [--ptr 0xPTR] [--pc 0xPC]

If --ptr is omitted, the script reads g_osAssert/g_osException and recovers the
freed pointer from the crash stack frame (osFreeTrace stores its argument in s1).
"""
import os, sys, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PREV_INUSE = 0x1
IS_MMAPPED = 0x2
SIZE_BITS = PREV_INUSE | IS_MMAPPED
MAGIC_UP = 0x1212
DTCM_LO, DTCM_HI = 0x00010000, 0x00014000   # av_ bin headers live here
PSRAM_LO, PSRAM_HI = 0x80000000, 0x81000000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("elf")
    ap.add_argument("--ptr", default=None, help="freed pointer (arg to free/osFreeTrace)")
    ap.add_argument("--pc", default=None, help="crash PC (default: read g_osAssert.pc)")
    args = ap.parse_args()

    mem = Mem(args.dump_dir)
    syms = Symbols(args.elf)
    S = lambda n: syms.lookup(n)[0]

    # ---- 1. crash PC + error log ----
    pc = int(args.pc, 0) if args.pc else None
    errlog = ""
    gOsAssert = S("g_osAssert")
    gOsErrLog = S("g_osErrorLog")
    if pc is None and gOsAssert is not None:
        offs = syms.struct_offsets("osAssert_t") or {}
        struct_ptr = mem.try_u32(gOsAssert)
        if struct_ptr:
            pc = mem.try_u32(struct_ptr + offs.get("pc", 12))
    if gOsErrLog:
        errlog = mem.cstr(gOsErrLog, 128)

    print("=" * 92)
    print(" DOUBLE-FREE DETECTOR  (dlmalloc.c:2066 check_mem_trace downflow)")
    print("=" * 92)
    print("  g_osErrorLog : %s" % errlog)
    print("  crash PC     : 0x%08x" % (pc or 0))

    fn = syms.resolve(pc)[0] if pc else "?"
    is_2066 = ("2066" in errlog) or ("osFreeTrace" in fn) or (fn.lower() == "free")
    if not is_2066:
        print("\n  NOTE: g_osErrorLog / PC 不指向 dlmalloc.c:2066 / osFreeTrace。")
        if not args.ptr:
            print("        非 2066 场景且未指定 --ptr，跳过（本脚本专为 2066 downflow 的 double-free 判定）。")
            print("        若需排查特定指针，用 --ptr 0x.... 显式指定。")
            return
        print("        用户指定了 --ptr，继续判定（仅供参考）。")

    # ---- 2. recover the freed pointer ----
    ptr = int(args.ptr, 0) if args.ptr else None
    if ptr is None:
        # osFreeTrace stores its arg in s1 (mv s1,a0 at entry). The crash
        # stack frame is at g_osException->trace (rt_hw_stack_frame).
        gOsExc = S("g_osException")
        if gOsExc is not None:
            offs_exc = syms.struct_offsets("osException_t") or {}
            exc_ptr = mem.try_u32(gOsExc)
            frame = mem.try_u32(exc_ptr + offs_exc.get("trace", 8)) if exc_ptr else None
            if frame:
                fr_offs = syms.struct_offsets("rt_hw_stack_frame") or {}
                for reg in ("s1", "a0"):   # osFreeTrace keeps arg in s1
                    off = fr_offs.get(reg)
                    if off is None:
                        continue
                    cand = mem.try_u32(frame + off)
                    if cand and PSRAM_LO <= cand < PSRAM_HI:
                        ptr = cand
                        print("\n  从崩溃栈帧 rt_hw_stack_frame.%s 恢复被 free 指针 = 0x%08x" % (reg, ptr))
                        break
    if ptr is None:
        print("\n  ✗ 未能自动恢复被 free 的指针。请用 --ptr 0x.... 指定（osFreeTrace/free 的入参）。")
        return
    print("  被 free 指针 ptr : 0x%08x" % ptr)

    # ---- 3. locate chunk via dlMemToTrace(ptr) ----
    align = mem.try_u16(ptr - 2)
    if align == MAGIC_UP:
        trace = ptr - 16
        note = "*(ptr-2)=0x1212(magic) -> trace = ptr-16"
    elif align is not None:
        trace = ptr - align - 16
        note = "*(ptr-2)=0x%04x(align) -> trace = ptr-align-16" % align
    else:
        print("  ✗ 无法读取 ptr-2"); return
    chunk = trace - 8   # [prev_size 4][size 4][trace 16]
    print("  trace 头        : 0x%08x  (%s)" % (trace, note))
    print("  chunk base      : 0x%08x" % chunk)

    # ---- 4. victim block header ----
    prev_size = mem.try_u32(chunk)
    size_raw = mem.try_u32(chunk + 4)
    if size_raw is None:
        print("  ✗ 无法读取 chunk size"); return
    sz = size_raw & ~SIZE_BITS
    magic = mem.try_u16(trace + 14)
    t_user = mem.try_u32(trace + 0)
    t_size = mem.try_u32(trace + 4)
    print("\n  受害块头: prev_size=0x%08x  size+flags=0x%08x (chunk_size=%d)"
          % (prev_size or 0, size_raw, sz))
    print("            trace.user=0x%08x  trace.size=%d (0x%x)  trace.magic=0x%04x"
          % (t_user or 0, t_size or 0, t_size or 0, magic or 0))

    # ---- 5. THE KEY: next chunk's PREV_INUSE bit ----
    nxt = chunk + sz
    nxt_raw = mem.try_u32(nxt + 4)
    print("\n  ★ 块状态判定（权威方法：下一块 PREV_INUSE 位，不能只看 magic）")
    if nxt_raw is None:
        print("    下一块 @0x%08x size 字段不可读 —— 无法判定" % nxt)
        return
    pi_bit = nxt_raw & 1
    inuse = bool(pi_bit)
    print("    下一块 @0x%08x  size+flags=0x%08x  PREV_INUSE(bit0)=%d"
          % (nxt, nxt_raw, pi_bit))
    print("    => 受害块 %s" % ("INUSE（在用）" if inuse else "FREE（已释放）"))

    # ---- 6. verdict ----
    print("\n" + "=" * 92)
    if not inuse:
        fd = mem.try_u32(chunk + 8)
        bk = mem.try_u32(chunk + 12)
        bin_back = None
        if fd is not None and DTCM_LO <= fd < DTCM_HI:
            for a in range(fd - 8, fd + 24, 4):   # bin header fd/bk window
                if mem.try_u32(a) == chunk:
                    bin_back = a
                    break
        print("  ★★★ VERDICT: DOUBLE-FREE（重复释放）★★★")
        print("  证据链：")
        print("    1. 受害块已是 FREE（下一块 PREV_INUSE=0）")
        print("    2. fd/bk @ chunk+8/+12 = 0x%08x / 0x%08x（第一次 free 时 dlmalloc 写入，指向 DTCM bin 头）"
              % (fd or 0, bk or 0))
        if bin_back is not None:
            print("    3. DTCM @0x%08x 回指受害块 0x%08x（空闲链表双向自洽，不可能由越界写巧合产生）"
                  % (bin_back, chunk))
        print("    4. trace.size 字段(@+4)=0x%08x 实为 bk 指针，被当作 size 算 down = trace+%d+16 -> 位置算飞 -> downflow 断言"
              % (t_size or 0, t_size or 0))
        print("    5. trace.magic=0x%04x 合法是因 dlfree 不清 magic（陈旧值）—— 绝不能据此排除 double-free"
              % (magic or 0))
        print("  根因：被 free 的指针释放后未置 NULL，且某条路径对同一指针再次 free。")
        print("  排查建议：")
        print("    - 审查所有 free(ptr) 点，释放后立即 ptr = NULL；")
        print("    - 定位重复释放路径（C++ 对象重复析构 / 管理表重复持有同一对象 / 异常清理与正常析构竞态）；")
        print("    - 在 free 前打印 ptr 值，复现确认是否对同一地址释放两次。")
    else:
        print("  ★ VERDICT: 堆越界写（HEAP OVERFLOW）踩坏 osMemTrace_t 头 ★")
        print("  证据：受害块 INUSE（下一块 PREV_INUSE=1），但 trace.size=%d (0x%x) 异常（被越界写改）"
              % (t_size or 0, t_size or 0))
        print("  根因：某段代码越界写，踩坏了受害块 osMemTrace_t.size 字段 -> down 位置算飞 -> downflow 断言。")
        print("  排查建议：在 osMemTrace_t 加 CRC32 / read_buf 前后设 guard bytes，复现定位越界源。")
    print("=" * 92)


if __name__ == "__main__":
    main()
