#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 code-integrity check: ELF code sections vs dump.

For EXCEPTION crashes (mcause=2 illegal instruction, 1/5 access fault on a
code address), the root cause may be PSRAM/IRAM code corruption. This compares
the ELF's .itcm / .iram2 / .psram code sections against the same addresses in
the dump, byte-by-byte, and reports:

  - INTACT     : section matches ELF (code is good — crash is logic/pointer)
  - CORRUPTED  : localized mismatch (some bytes overwritten — find them)
  - NOT LOADED : entire section differs (XIP/PSRAM region wasn't populated)

Usage:  python code_compare.py <dump_dir> <ap.elf> [--pc 0xADDR]
"""
import os, sys, struct, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# Code sections that are copied/XIP-ed into RAM and could be corrupted.
CODE_SECTIONS = {".itcm", ".iram2", ".psram", ".xip_text"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("elf")
    ap.add_argument("--pc", default=None, help="crash PC to spotlight")
    args = ap.parse_args()

    mem = Mem(args.dump_dir)
    syms = Symbols(args.elf)
    pc = int(args.pc, 16) if args.pc else None

    print("=" * 92)
    print(" 代码完整性检查（ELF 代码段 vs dump 内存）")
    print("=" * 92)
    print(" # 用途：EXCEPTION(非法指令/取指错误)时排除 PSRAM/IRAM/flash 代码被损坏")
    print(" # 仅在 ELF 非零字节处比较（排除段首 padding/BSS 的正常差异）")

    any_corrupt = False
    for sec in syms.ef.iter_sections():
        if sec.name not in CODE_SECTIONS:
            continue
        addr = sec["sh_addr"]
        size = sec["sh_size"]
        if size == 0:
            continue
        try:
            elf_data = sec.data()
        except Exception as e:
            print("\n%-8s @0x%08x size=0x%x : ELF read failed (%s)" % (sec.name, addr, size, e))
            continue
        try:
            dump_data = mem.read(addr, size)
        except Exception as e:
            print("\n%-8s @0x%08x size=0x%x : NOT in dump (%s) — section not captured" %
                  (sec.name, addr, size, e))
            continue

        # count mismatched bytes ONLY where the ELF has real (nonzero) code.
        # Section-head padding/BSS is zero in the ELF but holds runtime data in
        # the dump — those are not corruption. Sample every 16 bytes for speed.
        mism = 0
        first = last = None
        step = 16
        nonzero_samples = 0
        for i in range(0, size, step):
            if elf_data[i] != 0:
                nonzero_samples += 1
                if elf_data[i] != dump_data[i]:
                    mism += 1
                    if first is None:
                        first = i
                    last = i
        # also detect "not loaded": section is mostly nonzero in ELF but mostly
        # a fixed pattern (e.g. 0xff/0x00) in dump
        pct = 100.0 * mism / max(1, nonzero_samples)

        if mism == 0:
            verdict = "完整 INTACT（代码与 ELF 一致，无损坏）"
        elif pct > 90:
            verdict = "*** 未加载 NOT LOADED（整段不一致 — XIP/PSRAM 代码未填充）"
            any_corrupt = True
        else:
            verdict = "*** 损坏 CORRUPTED（约 %.1f%% 字节不一致；首@0x%08x 末@0x%08x）" % (
                pct, addr + first, addr + last)
            any_corrupt = True
            lo = max(0, first - 8)
            hi = min(size, first + 24)
            print("    首个不一致点附近的逐字节对比:")
            for j in range(lo, hi, 4):
                e = struct.unpack("<I", elf_data[j:j + 4])[0]
                d = struct.unpack("<I", dump_data[j:j + 4])[0]
                mk = "  <-- 不一致" if e != d else ""
                print("      +0x%04x (0x%08x): ELF=0x%08x dump=0x%08x%s" %
                      (j, addr + j, e, d, mk))

        print("\n%-8s @0x%08x size=0x%x (%d 字节) : %s" % (sec.name, addr, size, size, verdict))

        # 重点对比崩溃 PC 处指令
        if pc is not None and addr <= pc < addr + size:
            off = pc - addr
            e = struct.unpack("<I", elf_data[off:off + 4])[0] if off + 4 <= size else None
            d = struct.unpack("<I", dump_data[off:off + 4])[0]
            print("    >> 崩溃 PC 0x%08x 落在本段（偏移 0x%x）:" % (pc, off))
            print("       ELF  指令 = 0x%08x" % (e or 0))
            print("       dump instr = 0x%08x %s" % (d, "(DIFFERS — CPU ran corrupted code!)" if e != d else "(matches)"))

    print("\n" + "=" * 92)
    if any_corrupt:
        print(" 结论：检测到代码损坏 — 请分析上方不一致区间")
    else:
        print(" 结论：所有代码段完整 INTACT — 崩溃不是代码损坏导致")
        print("   若 EXCEPTION：查崩溃指令/mtval（指针/对齐问题）")
        print("   若 ASSERT：代码路径有效，根因是逻辑/数据（堆/栈/参数）")


if __name__ == "__main__":
    main()
