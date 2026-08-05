#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QCX216 代码完整性检查：ELF 代码段 vs dump 内存。

HardFault（BusFault/UsageFault 取指错误、非法指令）或 ASSERT/崩溃点落在代码段时，
需排除"代码被损坏"（RAM 代码段被堆越界踩、Flash 代码被异常写）这一根因。

QCX216 代码分散在两段（区别于 UIS8852 PSRAM/IRAM）：
  - Flash 代码（0x00000xxx / 0x00005xxx 段）：ROM/Flash，运行时只读不变，正常应 INTACT。
  - RAM 代码（0x004xxxxx 段，如 .load_apos：OSA/应用代码 load 到 RAM 执行）：**可能被堆
    越界写 / use-after-free 踩坏**，是本检查的重点。
  - 协议栈代码（0x8Cxxxx~0x9Axxxx）：常超 dump 范围（dump 仅 0x0~0x540000），NOT in dump 属正常。

判定：
  - INTACT     : ELF 代码与 dump 一致（代码完好，崩溃是 logic/指针/堆）
  - CORRUPTED  : 局部不一致（部分字节被覆盖 —— 定位破坏点）
  - NOT LOADED : 整段不一致（XIP/load 段未填充，或超 dump 未抓取）

用法: python qcx216_code_compare.py <dump> <elf> [--pc 0xADDR]
"""
import os
import sys
import struct
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from qcx216_common import DumpReader  # noqa: E402
from elftools.elf.elffile import ELFFile  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHF_WRITE = 0x1


def _is_in_ram_region(addr, ram_lo, ram_hi):
    """addr 是否落在 RAM 数据区（动态求的 ELF 非代码 alloc section 地址范围）。
    RAM 代码段与 RAM 数据同段，靠此判代码段是否可能被堆越界写踩坏（Flash 只读不会）。"""
    return ram_lo is not None and ram_lo <= addr < ram_hi


def main(argv=None):
    ap = argparse.ArgumentParser(description="QCX216 代码完整性：ELF 代码段 vs dump")
    ap.add_argument("dump", help="RamDumpData_*.bin 路径")
    ap.add_argument("elf", help="崩溃固件 ap_*.elf 路径")
    ap.add_argument("--pc", default=None, help="崩溃 PC，spotlight 该指令 ELF vs dump")
    args = ap.parse_args(argv)

    dr = DumpReader(args.dump)
    pc = int(args.pc, 16) if args.pc else None

    print("=" * 88)
    print(" 代码完整性检查（ELF 代码段 vs dump 内存）")
    print("=" * 88)
    print(" # 用途：HardFault(取指错误/非法指令) 或崩溃点在代码段时，排除代码被损坏")
    print(" # 仅在 ELF 非零字节处比较（排除段首 padding/BSS 的正常差异），每 16 字节采样")
    print(" # 重点：RAM 代码段(0x004xxxxx) 可能被堆越界写踩坏；Flash 段只读应 INTACT")

    any_corrupt = False
    n_checked = 0
    with open(args.elf, "rb") as f:
        ef = ELFFile(f)
        # 动态求 RAM 数据区（可写 alloc section 地址范围：data/bss），判代码段是否 RAM 代码。
        # 用 SHF_WRITE 过滤：Flash rodata 不可写，不延伸进 RAM 区（避免 0x8xxxxx 误判）
        ram_lo = ram_hi = None
        for sec in ef.iter_sections():
            if (sec["sh_flags"] & SHF_ALLOC) and not (sec["sh_flags"] & SHF_EXECINSTR) \
                    and (sec["sh_flags"] & SHF_WRITE):
                a, sz = sec["sh_addr"], sec["sh_size"]
                if sz == 0:
                    continue
                if ram_lo is None or a < ram_lo:
                    ram_lo = a
                if ram_hi is None or a + sz > ram_hi:
                    ram_hi = a + sz
        for sec in ef.iter_sections():
            flags = sec["sh_flags"]
            addr = sec["sh_addr"]
            size = sec["sh_size"]
            if not (flags & SHF_ALLOC and flags & SHF_EXECINSTR) or size == 0 or addr == 0:
                continue
            if size < 0x40:  # 跳过极小段（向量桩等）
                continue
            n_checked += 1
            tag = "RAM" if _is_in_ram_region(addr, ram_lo, ram_hi) else "Flash"
            try:
                elf_data = sec.data()
            except Exception as e:
                print("\n%-28s @0x%08x size=0x%x [%s]: ELF read failed (%s)"
                      % (sec.name, addr, size, tag, e))
                continue
            dump_data = dr.read(addr, size)
            if len(dump_data) < size:
                print("\n%-28s @0x%08x size=0x%x [%s]: NOT in dump (超 dump 范围，未抓取)"
                      % (sec.name, addr, size, tag))
                continue

            # 仅在 ELF 非零字节处比较（padding/BSS 在 ELF 为 0 但 dump 是运行时数据，非损坏）
            mism = 0
            first = last = None
            nonzero_samples = 0
            step = 16
            for i in range(0, size, step):
                if elf_data[i] != 0:
                    nonzero_samples += 1
                    if elf_data[i] != dump_data[i]:
                        mism += 1
                        if first is None:
                            first = i
                        last = i
            pct = 100.0 * mism / max(1, nonzero_samples)

            if mism == 0:
                verdict = "完整 INTACT（代码与 ELF 一致，无损坏）"
            elif pct > 90:
                verdict = "*** 未加载 NOT LOADED（整段不一致 — load/XIP 段未填充）"
                any_corrupt = True
            else:
                verdict = "*** 损坏 CORRUPTED（约 %.1f%% 字节不一致；首@0x%08x 末@0x%08x）" \
                          % (pct, addr + first, addr + last)
                any_corrupt = True
                lo = max(0, first - 8)
                hi = min(size, first + 24)
                print("    首个不一致点附近逐字节对比:")
                for j in range(lo, hi, 4):
                    e = struct.unpack_from("<I", elf_data, j)[0]
                    d = struct.unpack_from("<I", dump_data, j)[0]
                    mk = "  <-- 不一致" if e != d else ""
                    print("      +0x%04x (0x%08x): ELF=0x%08x dump=0x%08x%s"
                          % (j, addr + j, e, d, mk))

            mark = "  ★重点" if _is_in_ram_region(addr, ram_lo, ram_hi) else ""
            print("\n%-28s @0x%08x size=0x%x [%s]%s : %s"
                  % (sec.name, addr, size, tag, mark, verdict))

            # spotlight 崩溃 PC
            if pc is not None and addr <= pc < addr + size:
                off = pc - addr
                e = struct.unpack_from("<I", elf_data, off)[0] if off + 4 <= size else None
                d = struct.unpack_from("<I", dump_data, off)[0]
                print("    >> 崩溃 PC 0x%08x 落在本段（偏移 0x%x）:" % (pc, off))
                print("       ELF  指令 = 0x%08x" % (e or 0))
                tag2 = "(DIFFERS — CPU 执行了损坏代码!)" if (e is not None and e != d) else "(matches)"
                print("       dump 指令 = 0x%08x %s" % (d, tag2))

    print("\n" + "=" * 88)
    print(" 共检查 %d 个可执行段" % n_checked)
    if any_corrupt:
        print(" 结论：检测到代码损坏 — 请分析上方不一致区间（多为 RAM 代码段被堆越界写）")
    else:
        print(" 结论：所有在 dump 范围内的代码段完整 INTACT — 崩溃不是代码损坏导致")
        print("   HardFault：查崩溃指令/寄存器（空指针/野指针/链表损坏/栈）")
        print("   ASSERT  ：代码路径有效，根因是逻辑/数据（堆/栈/参数）")


if __name__ == "__main__":
    main()
