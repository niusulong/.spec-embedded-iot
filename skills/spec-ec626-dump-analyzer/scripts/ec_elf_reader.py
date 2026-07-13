#!/usr/bin/env python3
"""ELF32 file reading utilities for EC platform dump analysis.

Provides:
  - Section content reading at virtual addresses
  - LWIP memp_desc struct reading
  - Symbol table extraction (.symtab via pyelftools)
  - DWARF source line mapping (.debug_line via pyelftools)
"""

import struct
import os

# ELF32 constants
ELF_MAGIC = b'\x7fELF'
SHT_PROGBITS = 1
HAS_PYELFTOOLS = False
try:
    from elftools.elf.elffile import ELFFile
    HAS_PYELFTOOLS = True
except ImportError:
    pass


def has_elftools():
    """Check if pyelftools is available."""
    return HAS_PYELFTOOLS


def read_elf_bytes(elf_path, vaddr, size):
    """Read bytes from ELF file at a virtual address.

    Finds the section containing vaddr and computes the file offset.
    Returns bytes of requested size, or empty bytes on error.
    """
    try:
        with open(elf_path, 'rb') as f:
            f.seek(0)
            hdr = f.read(52)
            if len(hdr) < 52 or hdr[:4] != ELF_MAGIC:
                return b''

            e_shoff = struct.unpack_from('<I', hdr, 32)[0]
            e_shentsize = struct.unpack_from('<H', hdr, 46)[0]
            e_shnum = struct.unpack_from('<H', hdr, 48)[0]

            for i in range(e_shnum):
                f.seek(e_shoff + i * e_shentsize)
                sh = f.read(e_shentsize) if e_shentsize >= 40 else f.read(40)
                if len(sh) < 40:
                    continue

                # ELF32 Section Header: sh_name(0), sh_type(4), sh_flags(8),
                # sh_addr(12), sh_offset(16), sh_size(20)
                sh_type = struct.unpack_from('<I', sh, 4)[0]
                sh_addr = struct.unpack_from('<I', sh, 12)[0]
                sh_offset = struct.unpack_from('<I', sh, 16)[0]
                sh_size = struct.unpack_from('<I', sh, 20)[0]

                if sh_type == SHT_PROGBITS and sh_addr <= vaddr < sh_addr + sh_size:
                    file_off = sh_offset + (vaddr - sh_addr)
                    f.seek(file_off)
                    return f.read(size)

        return b''
    except (OSError, struct.error):
        return b''


def read_memp_desc_from_elf(elf_path, desc_addr):
    """Read LWIP memp_desc struct from ELF at virtual address.

    Production build layout (verified from EC626 ELF):
      offset  0: const char *desc   (4 bytes)
      offset  4: u16_t size         (2 bytes, element size)
      offset  6: u16_t num          (2 bytes, number of elements)
      offset  8: u8_t *base         (4 bytes, pool memory base)
      offset 12: struct memp **tab  (4 bytes, free list head pointer)

    Returns dict with desc_ptr, size, num, base, tab, or None on error.
    """
    raw = read_elf_bytes(elf_path, desc_addr, 16)
    if len(raw) < 16:
        return None

    desc_ptr = struct.unpack_from('<I', raw, 0)[0]
    elem_size = struct.unpack_from('<H', raw, 4)[0]
    elem_num = struct.unpack_from('<H', raw, 6)[0]
    base_addr = struct.unpack_from('<I', raw, 8)[0]
    tab_addr = struct.unpack_from('<I', raw, 12)[0]

    if elem_size == 0 or elem_num == 0:
        return None

    return {
        'desc_ptr': desc_ptr,
        'size': elem_size,
        'num': elem_num,
        'base': base_addr,
        'tab': tab_addr,
    }


# ── ELF symbol table (.symtab) via pyelftools ──────────────────────────

def read_elf_symbols(elf_path):
    """Read all symbols from ELF .symtab section (cached).

    Returns list of (addr, size, type_char, name) tuples.
    type_char: 'T'=text, 't'=local text, 'D'=data, 'B'=bss, etc.
    Returns empty list if pyelftools not available.
    """
    abs_path = os.path.abspath(elf_path)
    if abs_path in _symbol_cache:
        return _symbol_cache[abs_path]

    result = _read_elf_symbols_impl(elf_path)
    _symbol_cache[abs_path] = result
    return result


def _read_elf_symbols_impl(elf_path):
    if not HAS_PYELFTOOLS:
        return []

    symbols = []
    try:
        with open(elf_path, 'rb') as f:
            elf = ELFFile(f)
            symtab = elf.get_section_by_name('.symtab')
            if not symtab:
                return []
            for sym in symtab.iter_symbols():
                if sym['st_info']['type'] == 'STT_FUNC' or sym['st_info']['type'] == 'STT_OBJECT':
                    addr = sym['st_value']
                    size = sym['st_size']
                    name = sym.name
                    # Map ELF type to nm-style type char
                    bind = sym['st_info']['bind']
                    shndx = sym['st_shndx']
                    if shndx == 'SHN_UNDEF':
                        continue
                    stype = sym['st_info']['type']
                    sect = elf.get_section(shndx) if isinstance(shndx, int) else None
                    tchar = _sym_type_char(bind, stype, sect)
                    if name and addr > 0:
                        symbols.append((addr, size, tchar, name))
    except (OSError, Exception):
        pass
    return symbols


def _sym_type_char(bind, stype, section):
    """Map ELF symbol type to nm-style single char."""
    if stype == 'STT_FUNC':
        return 'T' if bind == 'STB_GLOBAL' else 't'
    if stype == 'STT_OBJECT':
        if section is None:
            return 'D'
        flags = section['sh_flags']
        if flags & 0x2:  # SHF_ALLOC
            if section['sh_type'] in (8, 'SHT_NOBITS'):
                return 'B' if bind == 'STB_GLOBAL' else 'b'
            return 'D' if bind == 'STB_GLOBAL' else 'd'
    return 'T' if bind == 'STB_GLOBAL' else 't'


# ── DWARF source line mapping (.debug_line) via pyelftools ─────────────

_line_cache = {}
_symbol_cache = {}


def read_elf_line_info(elf_path):
    """Read DWARF .debug_line info to map addresses to source file:line.

    Returns a sorted list of (addr, filepath, line_number) tuples.
    Cached after first call.
    """
    if not HAS_PYELFTOOLS:
        return []

    abs_path = os.path.abspath(elf_path)
    if abs_path in _line_cache:
        return _line_cache[abs_path]

    entries = []
    try:
        with open(elf_path, 'rb') as f:
            elf = ELFFile(f)
            if not elf.has_dwarf_info():
                _line_cache[abs_path] = entries
                return entries

            dwarf = elf.get_dwarf_info()
            for cu in dwarf.iter_CUs():
                line_prog = dwarf.line_program_for_CU(cu)
                if not line_prog:
                    continue

                # Build file table
                file_table = line_prog['file_entry']
                dir_table = line_prog['include_directory']

                for entry in line_prog.get_entries():
                    if entry.state and not entry.state.end_sequence:
                        addr = entry.state.address
                        fidx = entry.state.file - 1  # 1-based
                        filepath = _resolve_file(fidx, file_table, dir_table)
                        line = entry.state.line
                        entries.append((addr, filepath, line))

    except (OSError, Exception):
        pass

    entries.sort(key=lambda x: x[0])
    _line_cache[abs_path] = entries
    return entries


def _resolve_file(fidx, file_table, dir_table):
    """Resolve DWARF file entry to a human-readable path."""
    if fidx < 0 or fidx >= len(file_table):
        return '<unknown>'
    fentry = file_table[fidx]
    fname = fentry.name
    if isinstance(fname, bytes):
        fname = fname.decode('utf-8', errors='replace')
    didx = fentry.dir_index - 1  # 1-based, 0 means CU dir
    if didx >= 0 and didx < len(dir_table):
        dname = dir_table[didx]
        if isinstance(dname, bytes):
            dname = dname.decode('utf-8', errors='replace')
        return os.path.join(dname, fname)
    return fname


def addr_to_source(elf_path, target_addr):
    """Map a single address to source file:line.

    Returns 'filepath:line' string, or None if not resolvable.
    """
    entries = read_elf_line_info(elf_path)
    if not entries:
        return None

    # Binary search for the last entry with addr <= target_addr
    lo, hi = 0, len(entries)
    while lo < hi:
        mid = (lo + hi) // 2
        if entries[mid][0] <= target_addr:
            lo = mid + 1
        else:
            hi = mid

    if lo == 0:
        return None

    addr, filepath, line = entries[lo - 1]
    # Sanity: entry should be within ~4KB of target (same function)
    if target_addr - addr > 0x1000:
        return None

    # Trim to just filename for readability
    basename = os.path.basename(filepath)
    return f'{basename}:{line}'
