#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 arbitrary memory peek — forensic word dump with annotations.

Why this exists
---------------
Each canned script does its OWN specialized reading (heap_walker walks chunks,
system_stack reads the watermark), but there is no general "dump this region and
annotate each word" command. So byte-level forensics — spotting that a polluted
global's words match adjacent stack-frame locals, or that a crash value like 0xc9
is actually a dlmalloc chunk-size (0xc8|PREV_INUSE), or that a stack region's
background switches from 0x24242424 (fill) to 0 (BSS) across a boundary — means
hand-writing struct.unpack loops every time. This wraps Mem + Symbols/addr2line
into one command and auto-marks code pointers, known symbols, and fill patterns.

Usage:
  python peek.py <dump_dir> <addr> [len=0x40] [ap.elf]
Examples:
  python peek.py DUMP/ 0x136b8 0x40 ap.elf      # 64B at the polluted table slot, annotate
  python peek.py DUMP/ 0x136a0 0x90             # no ELF → hex words only
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ADDR = sys.argv[2] if len(sys.argv) > 2 else None
LEN = int(sys.argv[3], 0) if len(sys.argv) > 3 else 0x40
ELF = sys.argv[4] if len(sys.argv) > 4 else None

FILL_WORDS = (0x24242424, 0xA5A5A5A5, 0xCCCCCCCC, 0xDEADBEEF, 0x23232323)
SMALL_INT_MAX = 0x1000
WORDS_PER_ROW = 4


def main():
    if ADDR is None:
        sys.exit("usage: python peek.py <dump_dir> <addr> [len=0x40] [elf]")
    start = int(ADDR, 0) & ~3          # align down to a word boundary
    nwords = ((int(LEN, 0) if isinstance(LEN, str) else LEN) + 3) // 4
    n = nwords * 4
    mem = Mem(DUMP)

    syms = None
    sym_by_addr = {}
    if ELF and os.path.exists(ELF):
        try:
            syms = Symbols(ELF)
            for nm, (a, _sz) in syms.tab.items():
                if a:
                    sym_by_addr.setdefault(a, nm)
        except Exception as e:
            print("# (ELF annotation unavailable: %s)" % e)

    def annotate(v):
        if v is None:
            return ""
        if v == 0:
            return "0"
        if v in FILL_WORDS:
            return "填充(0x%08x)" % v
        if v in sym_by_addr:
            return "= %s" % sym_by_addr[v]
        if syms is not None:
            fc = syms.func_containing(v)
            if fc:
                return "代码→%s+0x%x" % (fc[0], v - fc[1])
            if 0 < v <= SMALL_INT_MAX:
                return "小整数"
        return ""

    region = mem.region_name(start)
    print("# peek [0x%08x, 0x%08x) %d bytes  region=%s%s" %
          (start, start + n, n, region, "  ELF=%s" % ELF if syms else "  (no ELF → hex only)"))
    print()
    for r in range(0, nwords, WORDS_PER_ROW):
        row_base = start + r * 4
        cells, anns = [], []
        for c in range(WORDS_PER_ROW):
            wi = r + c
            if wi >= nwords:
                cells.append("        ")
                continue
            off = wi * 4
            v = mem.try_u32(start + off)
            cells.append("%08x" % v if v is not None else "--------")
            anns.append((off, annotate(v)))
        print("  %08x: %s" % (row_base, " ".join(cells)))
        apart = ["+0x%02x %s" % (o, t) for o, t in anns if t]
        if apart:
            print("          %s" % "  ".join(apart))


if __name__ == "__main__":
    main()
