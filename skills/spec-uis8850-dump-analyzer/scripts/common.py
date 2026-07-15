#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Common module for UIS8850 (ARM) dump analysis.

Platform/project-agnostic: auto-registers every hex-named .bin in the dump dir
(filename == hex base, size == length). Memory layout is NOT hardcoded — read
symbol addresses from the ELF .symtab, resolve to whichever .bin covers them.

Provides: Mem (memory-region reader), Symbols (symtab + DWARF struct offsets),
ARM toolchain discovery, batch addr2line/objdump, PT_LOAD-based code detection.
"""
import os, struct, bisect, subprocess, argparse, platform
from collections import namedtuple

# ARM Thumb: code addresses have bit0=1. Strip it before resolving.
def thumb_real(addr):
    """Return the even instruction address (strip Thumb bit)."""
    return addr & ~1


# ----------------------------------------------------------------------------
# Shared constants — single source of truth for every analyzer script.

PSRAM_BASE = 0x80000000             # DTools dumps PSRAM starting at this base
PSRAM_BIN = "80000000.bin"          #   ... into this hex-named file
STACK_SCAN_WINDOW = 0x400           # bytes scanned above SP for return addrs
FREERTOS_STACK_MAGIC = 0xa5a5a5a5   # FreeRTOS fills fresh task stacks with this

# FreeRTOS TCB member offsets — FALLBACK ONLY. Always prefer DWARF
# (Symbols.struct_offsets); these defaults cover the 003 build so analysis
# still works when DWARF lacks the struct. Centralized so every script uses
# the same values instead of scattering magic offsets.
TCB_DEFAULT_OFFSETS = {
    "pxTopOfStack": 0x0,
    "uxPriority": 0x2c,
    "pxStack": 0x30,
    "pcTaskName": 0x34,
    "uxTCBNumber": 0x44,
}


def buf_u32(buf, off):
    """Little-endian u32 from a raw bytes buffer; 0 if out of range.

    Use when scanning a pre-read region (e.g. the PSRAM TCB sweep) where we
    want silent bounds handling rather than exceptions."""
    if off < 0 or off + 4 > len(buf):
        return 0
    return struct.unpack("<I", buf[off:off + 4])[0]


def read_psram(dump_dir):
    """Read the PSRAM dump (80000000.bin). Raises FileNotFoundError if absent."""
    with open(os.path.join(dump_dir, PSRAM_BIN), "rb") as f:
        return f.read()


def tcb_offsets(syms):
    """TCB member offsets: DWARF first, per-key named-constant fallback.

    Tries the common FreeRTOS TCB tag names, then falls back to
    TCB_DEFAULT_OFFSETS. Returns a dict usable as offs["pxStack"]."""
    offs = dict(TCB_DEFAULT_OFFSETS)
    for tag in ("tskTCB", "TCB_t", "BaseTCB", "tskTaskControlBlock"):
        d = syms.struct_offsets(tag)
        if d:
            offs.update(d)
            break
    return offs


class Mem:
    """Address-space reader over dumped .bin regions.

    By default registers EVERY hex-named .bin in dump_dir (platform-agnostic).
    Optional `regions` adds explicit (filename, base) pairs first (for aliases
    that don't follow the filename==base convention, e.g. PSRAM 0x40000000 alias).
    """
    def __init__(self, dump_dir, regions=None, scan_all=True):
        self.maps = []          # (base, end, bytearray, fname)
        self._registered_bases = set()
        for fn, base in (regions or []):
            self._register(dump_dir, fn, base)
        if scan_all:
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
        try: return self.u32(addr)
        except Exception: return None

    def try_u8(self, addr):
        try: return self.u8(addr)
        except Exception: return None

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

    def is_mapped(self, addr):
        return self._find(addr) is not None


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
        self._struct_cache = {}
        self._ptload = None

    def close(self):
        try:
            self.f.close()
        except Exception:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def __del__(self):
        # Safety net for scripts that use Symbols without `with`. Harmless if
        # the interpreter is already shutting down.
        self.close()

    def lookup(self, name):
        return self.tab.get(name, (None, None))

    def resolve(self, addr):
        """Nearest function covering addr -> (name, offset)."""
        a = thumb_real(addr)
        i = bisect.bisect_right(self._func_addrs, a) - 1
        if i >= 0:
            fa, fsz, fn = self.funcs[i]
            if fa <= a < fa + fsz:
                return fn, a - fa
            return fn + "(?)", a - fa
        return "??", 0

    def ptload_ranges(self):
        """List of (vaddr, memsz) for PT_LOAD segments (runtime code/data layout)."""
        if self._ptload is None:
            self._ptload = [(seg['p_vaddr'], seg['p_memsz'])
                            for seg in self.ef.iter_segments()
                            if seg['p_type'] == 'PT_LOAD']
        return self._ptload

    def is_exec_code(self, addr):
        """True if addr falls in an executable PT_LOAD segment (flash/iram code).

        Uses ELF program headers — no hardcoded text ranges.
        """
        a = thumb_real(addr)
        for seg in self.ef.iter_segments():
            if seg['p_type'] != 'PT_LOAD':
                continue
            # flags: X=1 (bit0 of p_flags). Executable load segment = code.
            if seg['p_flags'] & 1:
                if seg['p_vaddr'] <= a < seg['p_vaddr'] + seg['p_memsz']:
                    return True
        return False

    def is_load_addr(self, addr):
        """True if addr falls in ANY PT_LOAD segment (code or data)."""
        for v, sz in self.ptload_ranges():
            if v <= addr < v + sz:
                return True
        return False

    def struct_offsets(self, tag):
        """Robust struct member offset lookup (named struct / typedef chain).
        Returns {member_name: offset_bytes}. Cached on disk per-ELF."""
        if tag in self._struct_cache:
            return self._struct_cache[tag]
        result = {}
        if self.dwarf is None:
            self._struct_cache[tag] = result
            return result
        all_offs = self._all_struct_offsets()
        for t in (tag, tag[:-2] if tag.endswith("_t") else None,
                  tag + "_t" if not tag.endswith("_t") else None):
            if t and t in all_offs:
                result = dict(all_offs[t])
                break
        self._struct_cache[tag] = result
        return result

    def _all_struct_offsets(self):
        if hasattr(self, "_all_offs_cache"):
            return self._all_offs_cache
        import hashlib, pickle, tempfile
        st = os.stat(self.f.name)
        key = "%s|%d|%d" % (self.f.name, st.st_size, int(st.st_mtime))
        h = hashlib.sha1(key.encode()).hexdigest()[:16]
        cache_path = os.path.join(tempfile.gettempdir(), "uis8850_dwarf_%s.pkl" % h)
        try:
            with open(cache_path, "rb") as cf:
                self._all_offs_cache = pickle.load(cf)
                return self._all_offs_cache
        except Exception:
            pass

        die_flat = {}
        structs_named = {}
        typedefs_named = {}

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
                off = e["type_off"]
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
# ARM toolchain discovery + addr2line/objdump
def _host_is_windows():
    return platform.system() == "Windows"


def find_toolchain(dump_dir, tool="arm-none-eabi-addr2line"):
    """Walk up from dump_dir to find prebuilts/.../bin/<tool>.

    Prefers a HOST-NATIVE binary: on Windows a dir containing <tool>.exe, on
    Linux/mac a dir containing <tool>. Falls back to the other form only if no
    host-native build is found — so a Windows host won't grab the unusable
    linux toolchain that sits next to the win32 one (alphabetical os.walk would
    otherwise hit 'linux' first)."""
    pref = (tool + ".exe") if _host_is_windows() else tool
    other = tool if _host_is_windows() else tool + ".exe"
    pref_hit = other_hit = None
    cur = os.path.abspath(dump_dir)
    for _ in range(12):
        cand = os.path.join(cur, "prebuilts")
        if os.path.isdir(cand):
            for root, dirs, files in os.walk(cand):
                if pref_hit is None and pref in files:
                    pref_hit = root
                if other_hit is None and other in files:
                    other_hit = root
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return pref_hit or other_hit


def tool_path(toolchain_dir, name):
    """Resolve <name> (without .exe) inside toolchain_dir.

    Prefers the host-native form (Windows: <name>.exe; Linux/mac: <name>) so
    the returned path is actually executable here. Returns '' when no
    toolchain was discovered."""
    if not toolchain_dir:
        return ""
    names = [name + ".exe", name] if _host_is_windows() else [name, name + ".exe"]
    for cand in names:
        p = os.path.join(toolchain_dir, cand)
        if os.path.exists(p):
            return p
    return os.path.join(toolchain_dir, names[0])  # legacy fallback


# ----------------------------------------------------------------------------
# Per-run CLI + context bundle (DRY: every analyzer starts the same way)
_Ctx = namedtuple("Ctx", "mem syms addr2line objdump toolchain")


def parse_dump_args(description, want_elf2=False, want_map=False):
    """Standard analyzer CLI: <dump_dir> <ap_elf> [+ optional --elf2/--map].

    Gives every script uniform --help and friendly missing-arg errors instead
    of the old split between argparse and raw sys.argv."""
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("dump_dir", help="DTools ramdump dir (hex-named .bin files)")
    ap.add_argument("ap_elf", help="AP ELF (with .symtab + DWARF)")
    if want_elf2:
        ap.add_argument("--elf2", default=None,
                        help="second candidate ELF (FOTA version check)")
    if want_map:
        ap.add_argument("--map", default=None, help="linker .map (symbol fallback)")
    return ap.parse_args()


def load_ctx(dump_dir, ap_elf):
    """Build the standard per-run handle bundle: Mem + Symbols + tool paths.

    Returns a Ctx namedtuple; unpack with
        mem, syms, addr2line, objdump, tc = load_ctx(args.dump_dir, args.ap_elf)
    """
    mem = Mem(dump_dir)
    syms = Symbols(ap_elf)
    tc = find_toolchain(dump_dir)
    return _Ctx(mem, syms,
                tool_path(tc, "arm-none-eabi-addr2line"),
                tool_path(tc, "arm-none-eabi-objdump"),
                tc)


def addr2line_batch(addr2line_exe, elf, addrs):
    """Resolve list of ints -> {addr: (func, 'file:line')}. Strips Thumb bit."""
    out = {}
    if not addrs or not addr2line_exe or not os.path.exists(addr2line_exe):
        return out
    uniq = sorted(set(thumb_real(a) for a in addrs if a and a > 0x1000))
    if not uniq:
        return out
    args = [addr2line_exe, "-f", "-i", "-e", elf] + ["0x%x" % a for a in uniq]
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
             "--start-address=0x%x" % thumb_real(start),
             "--stop-address=0x%x" % stop, elf],
            capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:
        return ""


def objdump_binary(objdump_exe, data_bytes, vma, arch="arm", force_thumb=True):
    """Disassemble a raw byte blob (e.g. CP code from aon_iram.bin) at vma."""
    import tempfile
    if not objdump_exe or not os.path.exists(objdump_exe):
        return ""
    tf = tempfile.NamedTemporaryFile(suffix=".bin", delete=False)
    tf.write(data_bytes)
    tf.close()
    args = [objdump_exe, "-D", "-b", "binary", "-m" + arch]
    if force_thumb:
        args.append("-Mforce-thumb")
    args += ["--adjust-vma=0x%x" % vma, tf.name]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30)
        return r.stdout
    except Exception:
        return ""
    finally:
        try: os.unlink(tf.name)
        except OSError: pass


def cpsr_decode(cpsr):
    """Decode ARM CPSR into human-readable fields."""
    if cpsr is None:
        return "?"
    modes = {0x10:"USR",0x11:"FIQ",0x12:"IRQ",0x13:"SVC",0x17:"ABT",
             0x1b:"UND",0x1f:"SYS",0x1a:"HYP"}
    mode = cpsr & 0x1f
    return "mode=%s T=%d I=%d F=%d A=%d V=%d C=%d Z=%d N=%d" % (
        modes.get(mode, "0x%02x" % mode),
        (cpsr>>5)&1, (cpsr>>7)&1, (cpsr>>6)&1, (cpsr>>8)&1,
        (cpsr>>28)&1, (cpsr>>29)&1, (cpsr>>30)&1, (cpsr>>31)&1)
