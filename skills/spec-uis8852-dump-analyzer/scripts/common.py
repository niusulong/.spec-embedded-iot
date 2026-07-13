#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Common module for UIS8852 dump analysis.

Provides: memory-region access (.bin files), ELF symbol table + DWARF struct
offsets, RISC-V toolchain auto-discovery, batch addr2line.
All symbol addresses are read dynamically from the ELF — nothing is hardcoded.
"""
import os, struct, bisect, subprocess

# ----------------------------------------------------------------------------
# Memory regions: (filename, base_addr). Aliases are listed so either address
# form resolves. Filenames are the DTools convention (filename == hex base).
UIS8852_REGIONS = [
    ("80000000.bin", 0x80000000),   # PSRAM (also aliased @0x40000000)
    ("80000000.bin", 0x40000000),   # PSRAM alias
    ("c0200000.bin", 0xc0200000),   # IRAM (also aliased @0x10200000)
    ("c0200000.bin", 0x10200000),   # IRAM alias
    ("00008000.bin", 0x00008000),   # AP ITCM
    ("00010000.bin", 0x00010000),   # AP DTCM
    ("c8000000.bin", 0xc8000000),   # SPI flash XIP
]

# Text regions for AP core (used by backtrace call-site heuristics)
UIS8852_TEXT_REGIONS = [
    (0x00008000, 0x0000c000),   # AP ITCM
    (0x00010000, 0x00014000),   # AP DTCM (data, but global code ptrs live here)
    (0xc0200000, 0xc0280000),   # IRAM (code)
    (0x10200000, 0x10280000),   # IRAM alias
    (0x80000000, 0x80400000),   # PSRAM (XIP code may live here)
    (0x40000000, 0x40400000),   # PSRAM alias
    (0xc8000000, 0xcc000000),   # SPI flash XIP
]


class Mem:
    """Address-space reader over the dumped .bin regions."""
    def __init__(self, dump_dir, regions=UIS8852_REGIONS, scan_all_peripherals=False):
        self.maps = []  # (base, end, bytearray, fname)
        self._registered_bases = set()
        for fn, base in regions:
            self._register(dump_dir, fn, base)
        if scan_all_peripherals:
            # Auto-register every hex-named .bin in the dump dir so peripheral
            # register snapshots (e0002000.bin = WDT, f1005000.bin = PCU, ...)
            # are readable too. DTools names each .bin by its hex base address.
            try:
                files = os.listdir(dump_dir)
            except Exception:
                files = []
            for fn in files:
                if not fn.endswith(".bin"):
                    continue
                stem = fn[:-4]
                if len(stem) != 8:
                    continue
                try:
                    base = int(stem, 16)
                except ValueError:
                    continue
                if base in self._registered_bases or not (0x1000 <= base < 0x100000000):
                    continue
                self._register(dump_dir, fn, base)
        if not self.maps:
            raise FileNotFoundError("no .bin dump files found in %s" % dump_dir)
        self.maps.sort()
        self.bases = [m[0] for m in self.maps]

    def _register(self, dump_dir, fn, base):
        p = os.path.join(dump_dir, fn)
        if not os.path.exists(p) or base in self._registered_bases:
            return
        with open(p, "rb") as f:
            data = f.read()
        self.maps.append((base, base + len(data), data, fn))
        self._registered_bases.add(base)

    def _find(self, addr):
        i = bisect.bisect_right(self.bases, addr) - 1
        if i < 0:
            return None
        b, e, d, fn = self.maps[i]
        return (b, e, d, fn) if b <= addr < e else None

    def read(self, addr, n):
        r = self._find(addr)
        if r is None:
            raise ValueError("address 0x%08x not in any dumped region" % addr)
        b, e, d, fn = r
        off = addr - b
        if off + n > len(d):
            raise ValueError("read 0x%08x + %d past end of %s" % (addr, n, fn))
        return d[off:off + n]

    def u32(self, addr):
        return struct.unpack("<I", self.read(addr, 4))[0]

    def u16(self, addr):
        return struct.unpack("<H", self.read(addr, 2))[0]

    def u8(self, addr):
        return self.read(addr, 1)[0]

    def try_u32(self, addr):
        try:
            return self.u32(addr)
        except Exception:
            return None

    def try_u16(self, addr):
        try:
            return self.u16(addr)
        except Exception:
            return None

    def try_u8(self, addr):
        try:
            return self.u8(addr)
        except Exception:
            return None

    def cstr(self, addr, maxlen=256):
        try:
            raw = self.read(addr, maxlen)
        except Exception:
            return "<unreadable @0x%08x>" % addr
        z = raw.find(b"\x00")
        if z >= 0:
            raw = raw[:z]
        try:
            return raw.decode("utf-8", "replace")
        except Exception:
            return repr(raw)

    def region_name(self, addr):
        r = self._find(addr)
        return r[3] if r else "??"


def in_text(addr, regions=UIS8852_TEXT_REGIONS):
    """True if addr looks like AP code (for backtrace heuristics)."""
    for lo, hi in regions:
        if lo <= addr < hi:
            return True
    return False


# ----------------------------------------------------------------------------
# ELF symbols + DWARF
from elftools.elf.elffile import ELFFile


class Symbols:
    def __init__(self, elf_path):
        self.f = open(elf_path, "rb")
        self.ef = ELFFile(self.f)
        self.tab = {}          # name -> (addr, size)
        self.funcs = []        # (addr, size, name) for STT_FUNC
        st = self.ef.get_section_by_name(".symtab")
        if st is None:
            raise RuntimeError("ELF has no .symtab: %s" % elf_path)
        for s in st.iter_symbols():
            self.tab[s.name] = (s["st_value"], s["st_size"])
            if s["st_info"]["type"] == "STT_FUNC" and s["st_size"] > 0:
                self.funcs.append((s["st_value"], s["st_size"], s.name))
        self.funcs.sort()
        self._func_addrs = [f[0] for f in self.funcs]
        self.dwarf = None
        try:
            if self.ef.has_dwarf_info():
                self.dwarf = self.ef.get_dwarf_info()
        except Exception:
            self.dwarf = None
        # struct offset cache: tag -> {member: (offset, type_die_offset)}
        self._struct_cache = {}

    def lookup(self, name):
        return self.tab.get(name, (None, None))

    def resolve(self, addr):
        """Nearest function covering addr -> (name, offset)."""
        i = bisect.bisect_right(self._func_addrs, addr) - 1
        if i >= 0:
            fa, fsz, fn = self.funcs[i]
            if fa <= addr < fa + fsz:
                return fn, addr - fa
            return fn + "(?)", addr - fa
        return "??", 0

    def struct_offsets(self, tag):
        """Robust struct member offset lookup with on-disk DWARF cache.

        Handles named struct, typedef-to-struct (anonymous or named), and
        multi-hop typedef chains via a global DIE offset map. The full
        name->{member:offset} table is built once per ELF and pickled to disk,
        so repeat runs (and sibling scripts in the same analysis) skip the
        expensive 117MB DWARF traversal. Returns {member_name: offset_bytes}.
        """
        if tag in self._struct_cache:
            return self._struct_cache[tag]
        result = {}
        if self.dwarf is None:
            self._struct_cache[tag] = result
            return result

        all_offs = self._all_struct_offsets()
        # try exact, then without/with _t suffix
        for t in (tag, tag[:-2] if tag.endswith("_t") else None, tag + "_t" if not tag.endswith("_t") else None):
            if t and t in all_offs:
                result = dict(all_offs[t])
                break
        self._struct_cache[tag] = result
        return result

    def _all_struct_offsets(self):
        """Return {struct_or_typedef_name: {member: offset}}, cached on disk."""
        if hasattr(self, "_all_offs_cache"):
            return self._all_offs_cache
        import hashlib, pickle, tempfile, os
        st = os.stat(self.f.name)
        key = "%s|%d|%d" % (self.f.name, st.st_size, int(st.st_mtime))
        h = hashlib.sha1(key.encode()).hexdigest()[:16]
        cache_path = os.path.join(tempfile.gettempdir(), "uis8852_dwarf_%s.pkl" % h)
        try:
            with open(cache_path, "rb") as cf:
                self._all_offs_cache = pickle.load(cf)
                return self._all_offs_cache
        except Exception:
            pass

        # Build flat index: offset -> {tag, name, members[], type_off}
        die_flat = {}
        structs_named = {}   # name -> offset
        typedefs_named = {}  # name -> offset

        def _nm(die):
            a = die.attributes.get("DW_AT_name")
            if a is None:
                return None
            v = a.value
            return v.decode("utf-8", "replace") if isinstance(v, (bytes, bytearray)) else v

        for CU in self.dwarf.iter_CUs():
            cu_off = CU.cu_offset

            def walk(die):
                entry = {"tag": die.tag, "name": _nm(die), "members": [], "type_off": None}
                ty = die.attributes.get("DW_AT_type")
                if ty is not None:
                    entry["type_off"] = ty.value + cu_off
                if die.tag == "DW_TAG_structure_type":
                    for c in die.iter_children():
                        if c.tag != "DW_TAG_member":
                            continue
                        cn = c.attributes.get("DW_AT_name")
                        loc = c.attributes.get("DW_AT_data_member_location")
                        if cn and loc is not None:
                            mn = cn.value
                            if isinstance(mn, (bytes, bytearray)):
                                mn = mn.decode("utf-8", "replace")
                            entry["members"].append((mn, loc.value))
                die_flat[die.offset] = entry
                nm = entry["name"]
                if nm:
                    if die.tag == "DW_TAG_structure_type":
                        structs_named.setdefault(nm, die.offset)
                    elif die.tag == "DW_TAG_typedef":
                        typedefs_named.setdefault(nm, die.offset)
                for c in die.iter_children():
                    walk(c)
            walk(CU.get_top_DIE())

        # Resolve every struct + typedef chain to {member:offset}
        all_offs = {}

        def members_of_struct(offset):
            e = die_flat.get(offset)
            return dict(e["members"]) if e and e["tag"] == "DW_TAG_structure_type" else {}

        def resolve(name):
            off = structs_named.get(name)
            if off is not None:
                return members_of_struct(off)
            off = typedefs_named.get(name)
            seen = set()
            while off is not None and off not in seen:
                seen.add(off)
                e = die_flat.get(off)
                if e is None:
                    return {}
                if e["tag"] == "DW_TAG_structure_type":
                    return dict(e["members"])
                off = e["type_off"]   # follow typedef→target
            return {}

        for nm in structs_named:
            all_offs[nm] = members_of_struct(structs_named[nm])
        for nm in typedefs_named:
            all_offs[nm] = resolve(nm)

        try:
            with open(cache_path, "wb") as cf:
                pickle.dump(all_offs, cf, protocol=pickle.HIGHEST_PROTOCOL)
        except Exception:
            pass
        self._all_offs_cache = all_offs
        return all_offs


# ----------------------------------------------------------------------------
# Toolchain discovery + addr2line/objdump
def find_toolchain(dump_dir, tool="riscv64-unknown-elf-addr2line.exe"):
    """Walk up from dump_dir to find idh.code/prebuilts/<toolchain>/<tool>."""
    cur = os.path.abspath(dump_dir)
    for _ in range(12):
        cand = os.path.join(cur, "idh.code", "prebuilts")
        if os.path.isdir(cand):
            for root, dirs, files in os.walk(cand):
                if tool in files:
                    return root
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return None


def addr2line_batch(addr2line_exe, elf, addrs):
    """Resolve list of ints -> {addr: (func, 'file:line')}. Empty on failure."""
    out = {}
    if not addrs or not addr2line_exe or not os.path.exists(addr2line_exe):
        return out
    uniq = sorted(set(a for a in addrs if a))
    if not uniq:
        return out
    args = [addr2line_exe, "-f", "-e", elf] + ["0x%x" % a for a in uniq]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=120)
        lines = r.stdout.splitlines()
    except Exception:
        return out
    res = {}
    it = iter(lines)
    for a in uniq:
        try:
            fn = next(it).strip()
            fl = next(it).strip()
        except StopIteration:
            break
        res[a] = (fn, fl)
    return res


def objdump_range(objdump_exe, elf, start, stop):
    """Disassemble [start, stop). Returns objdump stdout text."""
    if not objdump_exe or not os.path.exists(objdump_exe):
        return ""
    try:
        r = subprocess.run(
            [objdump_exe, "-d", "-C",
             "--start-address=0x%x" % start,
             "--stop-address=0x%x" % stop, elf],
            capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:
        return ""
