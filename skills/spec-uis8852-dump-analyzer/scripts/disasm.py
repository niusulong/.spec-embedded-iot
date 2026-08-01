#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 one-shot disassembler — verify crash instructions / call sites / prologues.

Why this exists
---------------
The canned analysis scripts (uis8852_analyze / unwind / ...) resolve addresses to
function names via addr2line, but none exposes a way to disassemble an arbitrary
range or symbol on demand. When a conclusion must be verified by hand — "is the
crash instruction really `lbu a4,10(a0)`?", "does switch_irq_sp really reset SP to
__stack_end?", "what is do_check_chunk's frame size (cm.push)?" — you otherwise
have to locate the toolchain and hand-assemble an objdump command. This wraps
find_toolchain + Symbols.lookup + objdump_range into one command.

Uses the PROJECT toolchain (auto-discovered via find_toolchain) and never bundles
its own — the objdump must match the ELF, because it has to know RISC-V Zcmp
encodings (cm.push / cm.popret etc.); a generic/older objdump disassembles those
as garbage.

Usage:
  python disasm.py <dump_dir> <ap.elf> <symbol|addr> [+len | len]
Examples:
  python disasm.py DUMP/ ap.elf Pdcp_SendPdu2Rlc +0x60
  python disasm.py DUMP/ ap.elf 0xc0273b2a 8
  python disasm.py DUMP/ ap.elf Rlc_CheckRbNode
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Symbols, get_tool, objdump_range

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"
TARGET = sys.argv[3] if len(sys.argv) > 3 else None
LEN_ARG = sys.argv[4] if len(sys.argv) > 4 else None

DEFAULT_LEN = 0x40


def parse_target(syms, tok):
    """Return (start_addr, default_len_or_None).

    tok may be: a hex/decimal address; a bare symbol name; or 'symbol+0xoff'.
    """
    t = tok.strip()
    base, off = t, 0
    # split an attached +offset suffix on a symbol (e.g. Pdcp_SendPdu2Rlc+0x20)
    if "+" in t:
        left, right = t.split("+", 1)
        try:
            off = int(right.strip(), 0)
            base = left.strip()
        except ValueError:
            pass
    # numeric address?
    try:
        return int(base, 0), None
    except ValueError:
        pass
    a, sz = syms.lookup(base)
    if a is None:
        sys.exit("symbol not found in ELF .symtab: %s" % base)
    return a + off, sz


def main():
    if not TARGET:
        sys.exit("usage: python disasm.py <dump_dir> <ap.elf> <symbol|addr> [+len | len]")
    syms = Symbols(ELF)
    start, sym_sz = parse_target(syms, TARGET)

    length = DEFAULT_LEN
    if LEN_ARG:
        try:
            length = int(LEN_ARG, 0)
        except ValueError:
            sys.exit("bad len: %s" % LEN_ARG)
    elif sym_sz and sym_sz > 0:
        length = sym_sz

    objdump_exe = get_tool(DUMP, "riscv64-unknown-elf-objdump.exe")
    if not objdump_exe:
        sys.exit("toolchain not found: cannot locate idh.code/prebuilts/.../riscv64-unknown-elf-objdump.exe "
                 "(walked up from %s)" % DUMP)

    fn, off = syms.resolve(start)
    print("# %s @ 0x%08x  len=0x%x  (resolved: %s+0x%x)" % (TARGET, start, length, fn, off))
    print("# objdump: %s" % objdump_exe)
    out = objdump_range(objdump_exe, ELF, start, start + length)
    if not out.strip():
        sys.exit("objdump produced no output — address 0x%08x not in a code section?" % start)
    for ln in out.splitlines():
        if "file format" in ln:
            continue   # drop the one noisy header line, keep section + disassembly
        print(ln)


if __name__ == "__main__":
    main()
