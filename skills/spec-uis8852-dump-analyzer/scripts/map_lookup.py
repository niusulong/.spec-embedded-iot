#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Minimal .map symbol resolver — fallback when no ELF is available.

The main analyzer scripts REQUIRE an ELF with .symtab + DWARF (they read
struct member offsets, which only DWARF provides). When a dump ships with
only a linker .map (stripped ELF, or no ELF at all), this tool at least lets
you resolve code addresses to function names — enough for a first-look at a
crash PC / stack addresses.

Limitations vs ELF: NO struct offsets (threads/heap analyzers unusable),
NO addr2line source-line mapping (function name only).

Usage:
  python map_lookup.py <dump_dir> <map_file> 0xADDR [0xADDR ...]
  python map_lookup.py <dump_dir> <map_file> --backtrace 0x<sp-region>
"""
import os, sys, re, bisect, argparse

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def parse_map(map_path):
    """Parse GNU ld map -> sorted (addr, size, name) list.

    GNU ld symbol-definition lines look like (address FIRST, then name, no size):
        '                0xc026edf2                Ps_LpmCallback'
    Symbol size isn't in these lines, so we approximate size[i] = addr[i+1]-addr[i].
    """
    addrs = []   # (addr, name)
    # symbol line: leading ws, 0xADDR, ws, C-symbol name, end of line
    sym_re = re.compile(r"^\s+0x([0-9a-fA-F]+)\s+([A-Za-z_][A-Za-z0-9_]*)\s*$")
    for line in open(map_path, encoding="utf-8", errors="replace"):
        m = sym_re.match(line)
        if m:
            addr = int(m.group(1), 16)
            name = m.group(2)
            if 0x8000 <= addr < 0xd0000000:
                addrs.append((addr, name))
    addrs.sort()
    # approximate size from gap to next symbol (cap to avoid huge spans)
    funcs = []
    for i, (a, n) in enumerate(addrs):
        nxt = addrs[i + 1][0] if i + 1 < len(addrs) else a + 0x1000
        size = min(nxt - a, 0x100000)
        funcs.append((a, max(size, 1), n))
    return funcs


def resolve(funcs, addr):
    """Nearest function whose [addr, addr+size) covers (or precedes) addr."""
    addrs = [f[0] for f in funcs]
    i = bisect.bisect_right(addrs, addr) - 1
    if i < 0:
        return None
    a, sz, n = funcs[i]
    if a <= addr < a + sz:
        return n, addr - a
    return n + "(?)", addr - a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("map_file")
    ap.add_argument("addrs", nargs="*", help="addresses like 0xc026cb94")
    args = ap.parse_args()

    funcs = parse_map(args.map_file)
    print("parsed %d function symbols from %s" % (len(funcs), args.map_file))
    if not funcs:
        print("no symbols parsed — check map format."); return
    for a in args.addrs:
        try:
            ai = int(a, 16)
        except ValueError:
            print("  %s: (bad address)" % a); continue
        r = resolve(funcs, ai)
        if r:
            n, off = r
            print("  0x%08x -> %s+0x%x" % (ai, n, off))
        else:
            print("  0x%08x -> ??" % ai)


if __name__ == "__main__":
    main()
