#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 ARM Thumb stack unwind — find osiPanic's caller + full call chain.

From gBlueScreenRegs.SP, osiPanic's `push {r3,lr}` puts the caller's LR at
sp+4. Walk up the stack, validate each candidate return address by checking
that V-4/V-2 is a bl/blr/b instruction (ARM Thumb call site).

Usage:  python unwind.py <dump_dir> <ap.elf>
"""
import os, sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (addr2line_batch, objdump_range, thumb_real,
                    parse_dump_args, load_ctx, STACK_SCAN_WINDOW)


def is_call_site(objdump, elf, addr):
    """True if the instruction just before addr is a call (bl/blr/b/bx).
    ARM Thumb: bl is 32-bit (occupies V-4..V-2), b/blx 16-bit (V-2)."""
    if not objdump or not addr:
        return False
    real = thumb_real(addr)
    dis = objdump_range(objdump, elf, real - 4, real + 2)
    for line in dis.splitlines():
        s = line.strip()
        if ":" not in s:
            continue
        insn = s.split(":", 1)[1].strip().lower() if ":" in s else ""
        # bl, blx, b.w, b, bx — call/branch instructions
        if re.match(r"(bl|blx|b\.w|b|bx)\b", insn):
            return True
    return False


def main():
    args = parse_dump_args("UIS8850 ARM Thumb stack unwind")
    mem, syms, addr2line, objdump, tc = load_ctx(args.dump_dir, args.ap_elf)
    elf = args.ap_elf
    print("(toolchain: %s)" % tc)

    bsr_addr = syms.lookup("gBlueScreenRegs")[0]
    if not bsr_addr:
        print("gBlueScreenRegs 符号不存在, 无法回溯")
        return
    # r[] 数组偏移从 DWARF 读 (默认 0); sp=r13, pc=r15
    bsr = syms.struct_offsets("gBlueScreenRegs") or {}
    r_off = bsr.get("r", 0)
    sp = mem.try_u32(bsr_addr + r_off + 13 * 4)   # r13 = sp
    pc = mem.try_u32(bsr_addr + r_off + 15 * 4)   # r15 = pc
    print("=" * 92)
    print(" UIS8850 ARM Thumb 栈回溯")
    print("=" * 92)
    print("gBlueScreenRegs.SP = 0x%08x, PC = 0x%08x" % (sp or 0, pc or 0))

    osiPanic = syms.lookup("osiPanic")[0]
    if osiPanic and pc and thumb_real(osiPanic) <= thumb_real(pc) < thumb_real(osiPanic) + 0x30:
        print("AP PC 在 osiPanic 内 -> osiPanic push {r3,lr}, 调用者 LR 在 sp+4")

    # ---- 1. osiPanic 直接调用者 (sp+4) ----
    print("\n--- osiPanic 直接调用者 (sp+4 = push 的 lr) ---")
    caller = mem.try_u32((sp or 0) + 4)
    if caller:
        info = addr2line_batch(addr2line, elf, [caller]).get(thumb_real(caller), ("?", "?"))
        cs_ok = is_call_site(objdump, elf, caller)
        print("  sp+4 = 0x%08x -> %s  %s" % (caller, info[1], info[0]))
        print("  call site 验证 (V-4 处应为 bl/blr): %s" % ("通过 ✓" if cs_ok else "未确认(可能 tail-call 中间层)"))

    # ---- 2. 全栈代码地址扫描 + call site 验证 ----
    print("\n--- 栈上代码地址 (sp ~ sp+0x%x, 含 call site 验证) ---" % STACK_SCAN_WINDOW)
    seen = set()
    code_list = []
    for i in range(0, STACK_SCAN_WINDOW, 4):
        v = mem.try_u32((sp or 0) + i)
        if v and syms.is_exec_code(v) and v not in seen:
            seen.add(v)
            code_list.append((i, v))
    addrs = [v for _, v in code_list]
    r = addr2line_batch(addr2line, elf, addrs)
    print(" %-8s %-12s %-45s %s" % ("栈偏移", "地址", "函数 / 源码", "call site"))
    print("-" * 100)
    for off, v in code_list:
        info = r.get(thumb_real(v), ("?", "?"))
        cs = is_call_site(objdump, elf, v)
        # 只标注看起来像返回地址的(call site 通过)
        cs_mark = "✓" if cs else ""
        src = "%s  %s" % (info[1], info[0])
        print(" sp+0x%-4x 0x%08x %-45s %s" % (off, v, src[:45], cs_mark))

    # ---- 3. 调用链总结 ----
    print("\n--- 调用链总结 (从栈底向上推断) ---")
    # 找关键函数: vTaskSwitchContext, StackOverflowHook, osiPanic, 业务函数
    key_names = []
    for off, v in code_list:
        info = r.get(thumb_real(v), ("?", "?"))
        fn = info[0]
        if fn and fn not in ("?", "??"):
            key_names.append((off, fn, info[1]))
    # 去重保序
    seen_fn = set()
    chain = []
    for off, fn, src in key_names:
        if fn not in seen_fn:
            seen_fn.add(fn)
            chain.append((off, fn, src))
    for off, fn, src in chain:
        print("  sp+0x%-4x %s  (%s)" % (off, fn, src))


if __name__ == "__main__":
    main()
