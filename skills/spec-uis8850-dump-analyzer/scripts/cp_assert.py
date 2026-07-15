#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 CP (modem) assert analysis — via AP-side IPC records.

The CP core (ARM Cortex-R) asserts, reports its registers via IPC to Ap, which
logs "CP Assert reg:r0..r17" text + "AP PANIC" in PSRAM. This script extracts
those records, reads g_CpMdVersion, and disassembles the CP crash PC (in
aon_iram 0x50800000, ARM Thumb — code is NOT in AP ELF, so raw disasm only).

Usage:  python cp_assert.py <dump_dir> <ap.elf>
"""
import os, sys, re, subprocess
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Mem, Symbols, find_toolchain, objdump_binary, thumb_real,
                    parse_dump_args, load_ctx, PSRAM_BASE, PSRAM_BIN)


# Gap (bytes) beyond which a register hit is treated as a NEW assert snapshot.
# PSRAM may accumulate several CP asserts from prior sessions; we want one
# coherent r0..r17 set, not a union of several.
ASSERT_GROUP_GAP = 0x400


def group_cp_assert_regs(psram, pat):
    """Split 'CP Assert reg:rN 0xV' matches into snapshots by position gap.

    Returns a list of dicts {rn: (val, abs_pos)}, ordered as found. A new group
    starts whenever a hit is more than ASSERT_GROUP_GAP past the previous one."""
    groups = []
    cur = {}
    prev_pos = -1
    for m in pat.finditer(psram):
        rn = int(m.group(1))
        val = int(m.group(2), 16)
        pos = m.start()
        if cur and pos > prev_pos + ASSERT_GROUP_GAP:
            groups.append(cur)
            cur = {}
        cur[rn] = (val, PSRAM_BASE + pos)
        prev_pos = pos
    if cur:
        groups.append(cur)
    return groups


def pick_last_complete_group(groups):
    """Newest snapshot that has the registers we actually use (SP/LR/PC).

    Falls back to the last group if none is complete (e.g. a truncated dump)."""
    for g in reversed(groups):
        if 13 in g and 14 in g and 15 in g:
            return g
    return groups[-1] if groups else {}


def main():
    args = parse_dump_args("UIS8850 CP (modem) assert analyzer")
    mem, syms, addr2line, objdump, tc = load_ctx(args.dump_dir, args.ap_elf)
    psram = open(os.path.join(args.dump_dir, PSRAM_BIN), "rb").read()

    print("=" * 92)
    print(" UIS8850 CP (modem) Assert 分析")
    print("=" * 92)

    # ---- 1. CP 固件版本 ----
    cpv = syms.lookup("g_CpMdVersion")[0]
    if cpv:
        v = mem.try_u32(cpv)
        print("\ng_CpMdVersion @0x%08x = 0x%x (%d)" % (cpv, v or 0, v or 0))
        if v:
            print("  -> CP svn 版本号 = %d (匹配 CP 固件串 svn%d)" % (v, v))

    # ---- 2. 全文搜 CP Assert 寄存器记录 ----
    print("\n--- PSRAM 搜 'CP Assert reg:' 提取 CP 寄存器 ---")
    pat = re.compile(rb"CP Assert reg:r(\d+)\s+0x([0-9a-fA-F]+)")
    groups = group_cp_assert_regs(psram, pat)
    total_hits = sum(len(g) for g in groups)
    print("  共搜到 %d 条 reg 记录, 分成 %d 组 assert 快照" % (total_hits, len(groups)))

    last_group = pick_last_complete_group(groups)
    if last_group:
        print("  最新一次完整 CP Assert 寄存器 (取最后含 SP/LR/PC 的一组):")
        for rn in sorted(last_group):
            val, pos = last_group[rn]
            tag = ""
            if rn == 13: tag = " (SP)"
            elif rn == 14: tag = " (LR)"
            elif rn == 15: tag = " (PC)"
            elif rn == 16: tag = " (CPSR?)"
            elif rn == 17: tag = " (SPSR?)"
            print("    r%-2d = 0x%08x%s" % (rn, val, tag))

        cp_pc = last_group.get(15, (0, 0))[0]
        cp_lr = last_group.get(14, (0, 0))[0]
        cp_sp = last_group.get(13, (0, 0))[0]
        print("\n  CP PC = 0x%08x" % cp_pc)
        print("  CP LR = 0x%08x" % cp_lr)
        print("  CP SP = 0x%08x" % cp_sp)

        # ---- 3. CP 版本串 ----
        print("\n--- CP 固件版本串 ---")
        for m in re.finditer(rb"Version:([^\x00\n]+)", psram):
            s = m.group(1).decode("utf-8", "replace").strip()
            if "ivykit" in s or "modem" in s or "8850" in s:
                print("  @0x%08x : %s" % (PSRAM_BASE + m.start(), s[:90]))

        # ---- 4. CP 崩溃点反汇编 (aon_iram, ARM Thumb) ----
        print("\n--- CP 崩溃点反汇编 (0x%08x, aon_iram, ARM Thumb) ---" % cp_pc)
        cp_pc_real = thumb_real(cp_pc)
        # aon_iram 在 0x50800000
        aon_path = os.path.join(args.dump_dir, "50800000.bin")
        if os.path.exists(aon_path) and 0x50800000 <= cp_pc < 0x50814000:
            aon = open(aon_path, "rb").read()
            base_off = cp_pc_real - 0x50800000
            start = max(0, base_off - 0x30)
            blob = aon[start: base_off + 0x40]
            vma = 0x50800000 + start
            dis = objdump_binary(objdump, blob, vma, arch="arm", force_thumb=True)
            printed = 0
            for line in dis.splitlines():
                s = line.strip()
                if s and s[0] in "0123456789abcdef" and ":" in s:
                    mark = "   <<< CP PC" if ("%08x" % cp_pc_real) in s.lower() else ""
                    print("  " + s + mark)
                    printed += 1
                    if printed > 25:
                        break
            # 识别 CP15 读取 (异常处理特征)
            if "mrc" in dis.lower() and "15" in dis:
                print("\n  >>> CP PC 处在读 CP15 系统寄存器 (SCTLR/TTBR/DFSR/FAR/IFSR/IFAR)")
                print("      => CP 核发生异常(data/prefetch abort)后进入异常处理保存现场")
                print("      => CP 根因需 CP/Modem ELF 解析 aon_iram 代码")
        elif 0x10100000 <= cp_pc < 0x10134000:
            print("  CP PC 在 cp_iram (0x10100000), 反汇编 10100000.bin")
        else:
            print("  CP PC 0x%08x 不在已知代码区, 无法反汇编" % cp_pc)

        # ---- 4b. CP 栈回溯 (从 CP SP 扫 CP 代码地址) ----
        print("\n--- CP 栈回溯 (CP SP=0x%08x, 扫 CP 代码地址) ---" % cp_sp)
        def is_cp_code(v):
            return (0x50800000 <= v < 0x50814000 or   # aon_iram
                    0x10100000 <= v < 0x10134000 or   # cp_iram
                    0x60020000 <= v < 0x6024c000)      # flash
        cp_hits = []
        for i in range(0, 0x400, 4):
            v = mem.try_u32(cp_sp + i)
            if v and is_cp_code(v):
                cp_hits.append((i, v))
                if len(cp_hits) >= 12:
                    break
        aon_path = os.path.join(args.dump_dir, "50800000.bin")
        cp_iram_path = os.path.join(args.dump_dir, "10100000.bin")
        aon = open(aon_path, "rb").read() if os.path.exists(aon_path) else b""
        cp_iram = open(cp_iram_path, "rb").read() if os.path.exists(cp_iram_path) else b""
        for off, v in cp_hits:
            real = thumb_real(v)
            if 0x50800000 <= real < 0x50814000:
                region = "aon_iram"
            elif 0x10100000 <= real < 0x10134000:
                region = "cp_iram"
            else:
                region = "flash"
            print("  cp_sp+0x%-3x = 0x%08x (%s)" % (off, v, region))
            # 反汇编 aon_iram/cp_iram 的 CP 代码地址 (CP 调用链)
            if region in ("aon_iram", "cp_iram"):
                buf = aon if region == "aon_iram" else cp_iram
                base = 0x50800000 if region == "aon_iram" else 0x10100000
                boff = real - base
                if 0 <= boff < len(buf):
                    dis = objdump_binary(objdump, buf[max(0,boff-8):boff+0x14], real-8 if boff>=8 else base)
                    for l in dis.splitlines()[:4]:
                        s = l.strip()
                        if s and s[0] in "0123456789abcdef" and ":" in s:
                            print("      " + s)
        if not cp_hits:
            print("  (CP 栈上未扫到 CP 代码地址)")

        # ---- 5. 上报路径确认 ----
        print("\n--- CP assert 上报路径 (AP 侧) ---")
        for fn in ["ipc_notify_cp_assert", "ipc_show_cp_assert"]:
            a, sz = syms.lookup(fn)
            if a:
                print("  %-24s @0x%08x (drv_md_ipc.c)" % (fn, a))
        print("  AP 收到 CP assert -> drv_md_ipc.c -> 记录 'AP PANIC' -> (可能)触发 AP panic")
    else:
        print("  未搜到 'CP Assert reg:' 记录 -> 本次可能不是 CP assert")

    # ---- 6. AP PANIC 文本位置 ----
    print("\n--- 'AP PANIC' / 'Indication' 文本位置 ---")
    cnt = 0
    for m in re.finditer(rb"Indication:AP PANIC", psram):
        if cnt < 5:
            print("  @0x%08x" % (PSRAM_BASE + m.start()))
        cnt += 1
    print("  共 %d 处" % cnt)


if __name__ == "__main__":
    main()
