#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Shared utilities for spec-asr1603-dump-analyzer scripts.

Consolidates MAP file parsing, ELF section parsing, and binary search address
lookup — previously duplicated across dump_analyzer.py, axf_disasm.py,
stack_analysis.py, ddr_code_compare.py, and map_lookup.py.

All scripts in this package should import from this module:

    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from common import parse_map_file, lookup_address, parse_elf_sections
"""

import struct
import bisect
import re
import os

# ============================================================================
# Constants
# ============================================================================

THREADX_STACK_FILL = 0xEFEFEFEF
STACK_GUARD_DEADBEEF = 0xDEADBEEF

# ============================================================================
# MAP File Parsing — unified from map_lookup.py + dump_analyzer.py
# ============================================================================

def parse_map_file(map_path):
    """Parse ARMCC/RVCT MAP file, return (entries, code_addrs) for binary search.

    entries: sorted list of (addr, name, size, section, is_thumb)
    code_addrs: parallel list of (addr & ~1) for bisect lookups
    """
    entries = []
    if not map_path or not os.path.exists(map_path):
        return entries, []

    with open(map_path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line = line.strip()
            # ARMCC MAP format: symbol_name 0xADDR (Thumb|ARM) Code SIZE obj.o(.text)
            m = re.match(
                r'^(\S+)\s+'
                r'(0x[0-9a-fA-F]+)\s+'
                r'((?:Thumb|ARM)\s+Code)\s+'
                r'(\d+)\s+'
                r'(\S+)',
                line
            )
            if m:
                name = m.group(1)
                addr = int(m.group(2), 16)
                size = int(m.group(4))
                section = m.group(5)
                is_thumb = 'Thumb' in m.group(3)
                entries.append((addr, name, size, section, is_thumb))

    entries.sort(key=lambda x: x[0])
    code_addrs = [e[0] & ~1 for e in entries]
    return entries, code_addrs


def lookup_address(entries, target_addr, code_addrs=None):
    """Binary search for the function containing target_addr.

    Returns (addr, name, size, section, is_thumb) or None.
    """
    if not entries:
        return None

    if code_addrs is not None:
        idx = bisect.bisect_right(code_addrs, target_addr)
        if idx == 0:
            return None

        # Scan backwards a small window to handle overlapping entries
        for i in range(idx - 1, max(idx - 10, 0) - 1, -1):
            addr, name, size, section, is_thumb = entries[i]
            code_addr = code_addrs[i]
            if code_addr <= target_addr < code_addr + size:
                return entries[i]
            if code_addr < (target_addr - 0x1000):
                break

        # Fallback: exact match on code address
        exact_idx = bisect.bisect_left(code_addrs, target_addr & ~1)
        while exact_idx < len(code_addrs) and code_addrs[exact_idx] == (target_addr & ~1):
            return entries[exact_idx]

        return None

    # Fallback linear scan (when code_addrs not provided)
    best = None
    for addr, name, size, section, is_thumb in entries:
        code_addr = addr & ~1
        if code_addr <= target_addr < code_addr + size:
            best = (addr, name, size, section, is_thumb)

    if not best:
        for addr, name, size, section, is_thumb in entries:
            if (addr & ~1) == (target_addr & ~1):
                best = (addr, name, size, section, is_thumb)
                break

    return best


def format_symbol(entries, target_addr, code_addrs=None):
    """Resolve address to a human-readable symbol string, or None."""
    result = lookup_address(entries, target_addr, code_addrs)
    if not result:
        return None

    addr, name, size, section, is_thumb = result
    code_addr = addr & ~1
    offset = target_addr - code_addr

    # Clean section name: obj.o(.text) → obj (.text)
    sec_clean = section.replace('.o(', ' (')

    if offset > 0:
        return "{}+0x{:X} ({})".format(name, offset, sec_clean)
    else:
        return "{} ({})".format(name, sec_clean)


# ============================================================================
# ELF Section Parsing — from axf_disasm.py (most complete: 32/64 bit, LE/BE)
# ============================================================================

def parse_elf_sections(elf_path):
    """Parse ELF section headers. Returns list of (name, vaddr, file_offset, size).

    Supports both 32-bit and 64-bit ELF, little and big endian.
    """
    with open(elf_path, 'rb') as f:
        magic = f.read(4)
        if magic != b'\x7fELF':
            return []

        ei_class = struct.unpack('B', f.read(1))[0]
        is_64 = (ei_class == 2)
        ei_data = struct.unpack('B', f.read(1))[0]
        is_le = (ei_data == 1)
        endian = '<' if is_le else '>'

        f.seek(0)
        if is_64:
            ehdr = f.read(64)
            e_shoff = struct.unpack_from(endian + 'Q', ehdr, 40)[0]
            e_shentsize = struct.unpack_from(endian + 'H', ehdr, 58)[0]
            e_shnum = struct.unpack_from(endian + 'H', ehdr, 60)[0]
            e_shstrndx = struct.unpack_from(endian + 'H', ehdr, 62)[0]
        else:
            ehdr = f.read(52)
            e_shoff = struct.unpack_from(endian + 'I', ehdr, 32)[0]
            e_shentsize = struct.unpack_from(endian + 'H', ehdr, 46)[0]
            e_shnum = struct.unpack_from(endian + 'H', ehdr, 48)[0]
            e_shstrndx = struct.unpack_from(endian + 'H', ehdr, 50)[0]

        # Read section string table
        if e_shstrndx >= e_shnum:
            return []

        if is_64:
            f.seek(e_shoff + e_shstrndx * e_shentsize)
            shstr_hdr = f.read(e_shentsize)
            shstr_offset = struct.unpack_from(endian + 'Q', shstr_hdr, 24)[0]
            shstr_size = struct.unpack_from(endian + 'Q', shstr_hdr, 32)[0]
        else:
            f.seek(e_shoff + e_shstrndx * e_shentsize)
            shstr_hdr = f.read(e_shentsize)
            shstr_offset = struct.unpack_from(endian + 'I', shstr_hdr, 16)[0]
            shstr_size = struct.unpack_from(endian + 'I', shstr_hdr, 20)[0]

        f.seek(shstr_offset)
        shstrtab = f.read(shstr_size)

        sections = []
        for i in range(e_shnum):
            f.seek(e_shoff + i * e_shentsize)
            shdr = f.read(e_shentsize)
            if is_64:
                sh_name = struct.unpack_from(endian + 'I', shdr, 0)[0]
                sh_type = struct.unpack_from(endian + 'I', shdr, 4)[0]
                sh_addr = struct.unpack_from(endian + 'Q', shdr, 16)[0]
                sh_offset = struct.unpack_from(endian + 'Q', shdr, 24)[0]
                sh_size = struct.unpack_from(endian + 'Q', shdr, 32)[0]
            else:
                sh_name = struct.unpack_from(endian + 'I', shdr, 0)[0]
                sh_type = struct.unpack_from(endian + 'I', shdr, 4)[0]
                sh_addr = struct.unpack_from(endian + 'I', shdr, 12)[0]
                sh_offset = struct.unpack_from(endian + 'I', shdr, 16)[0]
                sh_size = struct.unpack_from(endian + 'I', shdr, 20)[0]

            name_end = shstrtab.find(b'\x00', sh_name)
            name = shstrtab[sh_name:name_end].decode('ascii', errors='replace')

            if sh_type in (1, 8, 14):  # PROGBITS, NOBITS, INIT_ARRAY
                sections.append((name, sh_addr, sh_offset, sh_size))

    return sections


def find_elf_section(sections, addr):
    """Find the ELF section containing the given virtual address.

    Returns (name, vaddr, file_offset, size) or None.
    """
    for sec in sections:
        name, sec_addr, sec_offset, sec_size = sec
        if sec_addr <= addr < sec_addr + sec_size:
            return sec
    return None


def read_elf_bytes(elf_path, sections, vaddr, size):
    """Read bytes at a virtual address from an ELF file, using section mapping."""
    sec = find_elf_section(sections, vaddr)
    if not sec:
        return None

    name, sec_addr, sec_offset, sec_size = sec
    offset_in_sec = vaddr - sec_addr
    if offset_in_sec + size > sec_size:
        size = sec_size - offset_in_sec

    with open(elf_path, 'rb') as f:
        f.seek(sec_offset + offset_in_sec)
        return f.read(size)
