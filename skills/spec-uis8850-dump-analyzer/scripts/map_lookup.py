#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 .map symbol lookup — fallback when ELF is stripped/missing.

Parses the linker .map file's symbol table to resolve address -> symbol.
Only gives function name + address (no DWARF struct offsets / source lines /
static functions). Prefer the ELF (uis8850_analyze.py) when available.

Usage:  python map_lookup.py <map_file> <addr1> [addr2 ...]
"""
import os, sys, re

def parse_map(map_path):
    """Return sorted list of (addr, name) from the .map symbol table."""
    syms = []
    pat = re.compile(r"^\s*0x([0-9a-fA-F]+)\s+(\w+)")
    in_symtab = False
    try:
        with open(map_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if "Linker script and memory map" in line:
                    in_symtab = True
                if not in_symtab:
                    continue
                m = pat.match(line)
                if m:
                    addr = int(m.group(1), 16)
                    name = m.group(2)
                    if addr > 0x1000:
                        syms.append((addr, name))
    except Exception as e:
        print("parse error: %s" % e)
    syms.sort()
    return syms


def lookup(syms, addr):
    """Nearest symbol at or below addr."""
    import bisect
    addrs = [s[0] for s in syms]
    i = bisect.bisect_right(addrs, addr) - 1
    if i >= 0:
        return syms[i][1], addr - syms[i][0]
    return "?", 0


def main():
    if len(sys.argv) < 3:
        print("Usage: python map_lookup.py <map_file> <addr1> [addr2 ...]")
        return
    map_path = sys.argv[1]
    addrs = []
    for a in sys.argv[2:]:
        try:
            addrs.append(int(a, 0) & ~1)
        except ValueError:
            print("bad addr: %s" % a)
    syms = parse_map(map_path)
    print("parsed %d symbols from %s" % (len(syms), map_path))
    for a in addrs:
        name, off = lookup(syms, a)
        print("0x%08x -> %s +0x%x" % (a, name, off))


if __name__ == "__main__":
    main()
