#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 ELF 符号表 + DWARF 行号读取（替代 arm-none-eabi-nm / addr2line）。

QCX216 工具链无 ARM binutils，本机也没有 capstone。但崩溃固件 ELF 自带完整的
.symtab（符号表）和 .debug_line（DWARF 行号程序），用 pyelftools 即可实现：
  - 地址 -> 符号 + 偏移   （替代 nm -S + addr2line 符号段）
  - 地址 -> 源文件:行号    （替代 addr2line -f -e）

所有能力已用 RamDumpData_20260629_103016.bin + ap_at_command.elf 验证。
"""
import bisect
import re
from dataclasses import dataclass

from elftools.elf.elffile import ELFFile

# ELF section flags（用数值常量，避免不同 pyelftools 版本导入路径差异）
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4


@dataclass
class Symbol:
    name: str
    addr: int
    size: int
    is_func: bool = False    # STT_FUNC（可执行函数）

    def contains(self, addr: int) -> bool:
        # size==0 的符号按最多 4KB 容差估算所属区间
        end = self.addr + (self.size if self.size else 0x1000)
        return self.addr <= addr < end


class ElfReader:
    """ELF 符号 / DWARF 行号查询。

    构建时一次性把 .symtab 与 .debug_line 解析进内存，后续查询为 O(logN)。
    大型固件（.debug_line 几十万条）首次构建约 1~2 秒，可接受。
    """

    # 平台关键符号名（用于自动推导 dump 配置，缺失时优雅降级）
    PLATFORM_SYMBOLS = [
        "pxCurrentTCB", "ucHeap", "xTickCount", "xSchedulerRunning",
        "__StackTop", "__StackLimit", "excepInfoStore", "excepStep",
        "excepDumpEndFlag", "excepCompMaxLen", "apExceptCheckPoint",
        "cpExceptCheckPoint", "gTotalHeapSize", "Mcu_HardFault_Handler",
        "Mcu_Default_Handler", "Idle_Stack",
    ]

    def __init__(self, elf_path: str, verbose: bool = False):
        self.path = elf_path
        self._fh = open(elf_path, "rb")
        self._elf = ELFFile(self._fh)
        self.arch = self._elf.header["e_machine"]      # EM_ARM
        self.entry = self._elf.header["e_entry"]
        self._syms: list[Symbol] = []
        self._sym_by_name: dict[str, Symbol] = {}
        self._sym_addrs_sorted: list[int] = []
        self._line_map: dict[int, tuple[str, int]] = {}
        self._line_keys_sorted: list[int] = []
        self.code_ranges: list[tuple[int, int]] = []   # [(start,end)] 可执行 section
        self.ram_ranges: list[tuple[int, int]] = []     # [(start,end)] 数据 section
        self._collect_section_ranges()
        self._build_symbols()
        # DWARF 行号表懒加载：构建 40w+ 条目约 15~20s，仅在实际查询源码行时才构建，
        # 这样 disasm/resolve 等只需符号的场景可秒级返回。
        self._line_built = False
        self._sec_cache = None   # (start, end, data) ELF section 定位缓存（反汇编超 dump 段用）

    # ---------- section 范围（区分代码/数据地址）----------
    def _collect_section_ranges(self):
        """收集所有 SHF_ALLOC section，按是否可执行分到 code/ram 两组。

        QCX216 代码分散在 0x00000xxx（底层驱动）和 0x0041xxxx（OSA/应用）两段，
        必须用 section header 判断一个地址是代码还是数据，不能只看地址前缀。
        """
        for sec in self._elf.iter_sections():
            flags = sec["sh_flags"]
            addr = sec["sh_addr"]
            size = sec["sh_size"]
            if not (flags & SHF_ALLOC) or addr == 0 or size == 0:
                continue
            rng = (addr, addr + size)
            if flags & SHF_EXECINSTR:
                self.code_ranges.append(rng)
            else:
                self.ram_ranges.append(rng)
        self.code_ranges.sort()
        self.ram_ranges.sort()

    def is_code(self, addr: int) -> bool:
        # Cortex-M Thumb 地址最低位是 1（Thumb 标志），判断时先抹掉
        a = addr & ~1
        for start, end in self.code_ranges:
            if start <= a < end:
                return True
        return False

    def is_ram(self, addr: int) -> bool:
        for start, end in self.ram_ranges:
            if start <= addr < end:
                return True
        return False

    # ---------- 按地址读代码字节（反汇编超 dump 段用，如 0x8Cxxxx 协议栈代码）----------
    def _section_for(self, addr: int):
        """定位 addr 所在 SHF_ALLOC section，带缓存（连续地址访问 O(1) 命中）。"""
        c = self._sec_cache
        if c and c[0] <= addr < c[1]:
            return c
        for sec in self._elf.iter_sections():
            a = sec["sh_addr"]; sz = sec["sh_size"]
            if (sec["sh_flags"] & SHF_ALLOC) and a <= addr < a + sz:
                self._sec_cache = (a, a + sz, bytes(sec.data()))
                return self._sec_cache
        return None

    def read_u8(self, addr: int):
        """从 ELF section 读单字节；不在任何 section 返回 None。"""
        s = self._section_for(addr)
        if not s:
            return None
        off = addr - s[0]
        return s[2][off] if off < len(s[2]) else None

    def read_code(self, addr: int, size: int) -> bytes:
        """从 ELF section 读 size 字节（跨 section 只返回首段内的部分）。"""
        s = self._section_for(addr)
        if not s:
            return b""
        off = addr - s[0]
        return s[2][off:off + size]

    # ---------- 符号 ----------
    def _build_symbols(self):
        st = self._elf.get_section_by_name(".symtab")
        if st is None:
            return
        for s in st.iter_symbols():
            name, val, sz = s.name, s["st_value"], s["st_size"] or 0
            if not name or val == 0:
                continue
            is_func = s["st_info"]["type"] == "STT_FUNC"
            # 函数符号的 value 带 Thumb bit（奇数），抹掉以便按偶数地址匹配 BL 目标
            if is_func:
                val = val & ~1
            sym = Symbol(name, val, sz, is_func=is_func)
            self._syms.append(sym)
            # 同名符号保留地址最小的一个（去重 ARM mapping symbol 等）
            cur = self._sym_by_name.get(name)
            if cur is None or val < cur.addr:
                self._sym_by_name[name] = sym
        self._syms.sort(key=lambda x: x.addr)
        self._sym_addrs_sorted = [s.addr for s in self._syms]

    def find_symbol(self, name: str):
        """精确名查找，返回 Symbol 或 None。"""
        return self._sym_by_name.get(name)

    def find_symbols(self, pattern: str):
        """正则模糊查找，返回 [Symbol]。"""
        rx = re.compile(pattern, re.IGNORECASE)
        # 去重：同名只保留一个
        seen, out = set(), []
        for s in self._syms:
            if rx.search(s.name) and s.name not in seen:
                seen.add(s.name)
                out.append(s)
        return out

    def sym_at(self, addr: int):
        """地址 -> 包含该地址的 Symbol（优先 size 区间命中，否则最近前驱符号）。"""
        idx = bisect.bisect_right(self._sym_addrs_sorted, addr) - 1
        for i in range(idx, max(idx - 8, -1), -1):
            s = self._syms[i]
            if s.size and s.contains(addr):
                return s
        if idx >= 0:
            return self._syms[idx]
        return None

    # ---------- DWARF 行号 ----------
    def _ensure_line_map(self):
        """首次需要源码行时才构建（构建成本高，约 15~20s）。"""
        if self._line_built:
            return
        self._build_line_map()
        self._line_built = True

    def _build_line_map(self, verbose: bool = False):
        if not self._elf.has_dwarf_info():
            return
        di = self._elf.get_dwarf_info()
        for CU in di.iter_CUs():
            lp = di.line_program_for_CU(CU)
            if lp is None:
                continue
            file_entry = lp["file_entry"]
            inc_dir = lp["include_directory"]
            for entry in lp.get_entries():
                st = entry.state
                if st is None or st.end_sequence:
                    continue
                addr = st.address
                if addr == 0 or addr in self._line_map:
                    continue
                fname = "?"
                if 0 < st.file <= len(file_entry):
                    fe = file_entry[st.file - 1]
                    nm = fe.name.decode(errors="replace")
                    di_idx = getattr(fe, "dir_index", 0)
                    d = ""
                    if 0 < di_idx <= len(inc_dir):
                        d = inc_dir[di_idx - 1].decode(errors="replace")
                    fname = (d + "/" + nm) if d else nm
                self._line_map[addr] = (fname, st.line)
        self._line_keys_sorted = sorted(self._line_map)

    def line_at(self, addr: int):
        """地址 -> (file, line)。落在行号边界上或向前取最近的行号条目。"""
        self._ensure_line_map()
        if not self._line_keys_sorted:
            return None
        i = bisect.bisect_right(self._line_keys_sorted, addr) - 1
        if i < 0:
            return None
        base = self._line_keys_sorted[i]
        if addr - base > 0x4000:   # 距离过远，认为不在任何源码行内（可能是数据区）
            return None
        return self._line_map[base]

    # ---------- 综合 ----------
    def locate(self, addr: int) -> dict:
        """综合符号 + DWARF 行号，给出地址的完整定位信息。"""
        sym = self.sym_at(addr)
        line = self.line_at(addr)
        return {
            "addr": addr,
            "symbol": sym.name if sym else None,
            "sym_base": sym.addr if sym else None,
            "sym_offset": (addr - sym.addr) if sym else None,
            "is_func": sym.is_func if sym else False,
            "is_code": self.is_code(addr),
            "file": self._short_path(line[0]) if line else None,
            "line": line[1] if line else None,
        }

    @staticmethod
    def _short_path(path: str) -> str:
        # 压缩冗长的相对路径前缀（../../../../../../../PLAT/... -> PLAT/...）
        for marker in ("/PLAT/", "/nwy_code/", "/middleware/"):
            idx = path.replace("\\", "/").find(marker)
            if idx >= 0:
                return path[idx + 1:]
        return path

    # ---------- 平台配置 ----------
    def platform_config(self) -> dict:
        """从符号表推导平台关键地址，缺失项为 None。"""
        cfg = {}
        for name in self.PLATFORM_SYMBOLS:
            s = self._sym_by_name.get(name)
            cfg[name] = s.addr if s else None
        return cfg

    def close(self):
        self._fh.close()
