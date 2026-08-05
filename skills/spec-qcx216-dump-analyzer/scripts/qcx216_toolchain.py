#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QCX216 ARM 工具链封装（arm-none-eabi-objdump / nm / readelf）。

QCX216 仓库自带 GCC 10 工具链（PLAT/tools/gcc/arm-none-eabi/bin），本模块封装
反汇编/符号查询，作为纯 Python 反汇编器（qcx216_disasm）与 pyelftools 的权威增强：

  - objdump -d -C -l   反汇编 + 自带源码行（权威 Thumb-2，正确解 ITE/MSR/宽指令，
                        无 addr2line 时用 -l 的 file:line 替代）
  - nm -S -C           符号表 + 大小（交叉验证 ElfReader）
  - readelf -S         section 布局（备用）

工具链查找顺序：① 环境变量 QCX216_ARM_TOOLCHAIN；② <repo>/PLAT/tools/gcc/arm-none-eabi/bin
（从多个起点向上找含 PLAT/ 的仓库根）；③ PATH（shutil.which）。找不到返回 {}，调用方降级。
"""
import os
import re
import shutil
import subprocess

# 工具候选名（按优先级）。仓库自带工具链文件名是 objdump.exe / nm.exe / readelf.exe
# （不带 arm-none-eabi- 前缀；objdump 反汇编不依赖文件名，读 ELF 头识别架构）。
# PATH 回退时优先标准前缀名 arm-none-eabi-*，再试通用名。
_TOOLS = {"objdump": ["objdump", "arm-none-eabi-objdump"],
          "nm": ["nm", "arm-none-eabi-nm"],
          "readelf": ["readelf", "arm-none-eabi-readelf"]}

_cache = None  # find_toolchain 结果缓存（dict 或 {}）


def _exe(name: str) -> str:
    return name + (".exe" if os.name == "nt" else "")


def _scan_bin_dir(bin_dir: str) -> dict:
    found = {}
    if not os.path.isdir(bin_dir):
        return found
    for key, names in _TOOLS.items():
        for base in names:
            p = os.path.join(bin_dir, _exe(base))
            if os.path.isfile(p):
                found[key] = p
                break
    return found


def _candidate_start_points(start_dir):
    """收集查找起点：start_dir（可单/多）+ cwd + 本脚本目录。"""
    pts = []
    if start_dir is None:
        start_dir = []
    if isinstance(start_dir, str):
        start_dir = [start_dir]
    pts.extend(start_dir)
    try:
        pts.append(os.getcwd())
    except Exception:
        pass
    pts.append(os.path.dirname(os.path.abspath(__file__)))  # 技能 scripts 目录
    # 去重保序
    seen, out = set(), []
    for p in pts:
        if not p:
            continue
        ap = os.path.abspath(p)
        if ap not in seen:
            seen.add(ap)
            out.append(ap)
    return out


def find_toolchain(start_dir=None, refresh: bool = False) -> dict:
    """定位 arm-none-eabi 工具链。返回 {objdump, nm, readelf} 路径（可能部分），未找到返回 {}。

    start_dir: 查找起点（路径或路径列表），向上查找含 PLAT/tools/gcc/arm-none-eabi/bin 的仓库根。
    """
    global _cache
    if _cache is not None and not refresh:
        return _cache

    found = {}

    # ① 环境变量 QCX216_ARM_TOOLCHAIN（指向 bin 目录或其父）
    env = os.environ.get("QCX216_ARM_TOOLCHAIN")
    if env:
        cands = [env, os.path.join(env, "bin")]
        for c in cands:
            f = _scan_bin_dir(c)
            if f:
                found.update(f)
                break

    # ② <repo>/PLAT/tools/gcc/arm-none-eabi/bin —— 从多起点向上找含 PLAT/ 的根
    if not found:
        for start in _candidate_start_points(start_dir):
            cur = start
            for _ in range(14):
                cand = os.path.join(cur, "PLAT", "tools", "gcc", "arm-none-eabi", "bin")
                f = _scan_bin_dir(cand)
                if f:
                    found.update(f)
                    break
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
            if found:
                break

    # ③ PATH（优先标准前缀名，再通用名）
    if not found:
        for key, names in _TOOLS.items():
            for base in names:
                p = shutil.which(_exe(base)) or shutil.which(base)
                if p:
                    found[key] = p
                    break

    _cache = found
    return found


def has_objdump() -> bool:
    return bool(find_toolchain().get("objdump"))


def reset_cache():
    global _cache
    _cache = None


# ----------------------------------------------------------------------------
# objdump 反汇编（带源码行）
def _short_path(path: str) -> str:
    p = path.replace("\\", "/")
    for marker in ("/PLAT/", "/nwy_code/", "/middleware/"):
        idx = p.find(marker)
        if idx >= 0:
            return p[idx + 1:]
    return os.path.basename(path)


def _parse_objdump(text: str):
    """解析 objdump -d [-l] 输出 → [(addr, mnemonic, operands, file_line_or_None)]。

    objdump -d -l --no-show-raw-inss 典型输出：
        000050e4 <vListInsert>:
        vListInsert():
        E:/.../PLAT/os/freertos/src/list.c:104
            50e4:\tpush\t{r4, r5, lr}
        E:/.../list.c:106
            50e6:\tldr\tr4, [r1, #0]
    指令行: '    <hex>:\\t<mnemonic>\\t<operands>'；源码行: '<path>:<lineno>'。
    """
    out = []
    cur_fileline = None
    # 指令行：前导空格 + hex 地址 + ':' + tab + 助记符...
    ins_re = re.compile(r"^\s*([0-9a-fA-F]+):\t(.+)$")
    # 源码行：路径:行号（可选 (discriminator N)）。排除函数标签 '<func>:'（无结尾数字）
    src_re = re.compile(r"^(.+?):(\d+)(?:\s+\(discriminator\s+\d+\))?\s*$")
    for line in text.splitlines():
        if not line.strip():
            continue
        m = ins_re.match(line)
        if m:
            addr = int(m.group(1), 16)
            rest = m.group(2).strip()
            mm = re.match(r"([A-Za-z][A-Za-z0-9.]*)\s*(.*)", rest)
            if mm:
                mnem, ops = mm.group(1), mm.group(2).strip()
            else:
                mnem, ops = rest, ""
            out.append((addr, mnem, ops, cur_fileline))
            cur_fileline = None  # 一条指令消费当前源码行
            continue
        m = src_re.match(line)
        if m and "<" not in line[: line.find(":")]:  # 跳过 '<func>:' 标签
            short = _short_path(m.group(1))
            cur_fileline = "%s:%s" % (short, m.group(2))
    return out


def objdump_disasm(elf: str, start: int, stop: int, with_line: bool = True,
                   objdump_exe: str = None):
    """objdump -d 反汇编 [start, stop)。返回 [(addr, mnem, ops, file_line|None)]，失败 None。"""
    tc = find_toolchain()
    exe = objdump_exe or tc.get("objdump")
    if not exe or not os.path.isfile(exe) or not os.path.isfile(elf):
        return None
    args = [exe, "-d", "-C", "--no-show-raw-insn",
            "--start-address=0x%x" % start, "--stop-address=0x%x" % stop, elf]
    if with_line:
        args.append("-l")
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=30,
                           errors="replace")
    except Exception:
        return None
    if r.returncode != 0 or not r.stdout:
        return None
    return _parse_objdump(r.stdout)


def objdump_addr2line_batch(elf: str, addrs: list, with_line: bool = True,
                            objdump_exe: str = None) -> dict:
    """批量地址→源码行（无 addr2line，用 objdump -d -l 的 file:line 替代）。

    地址分散时按 gap 分段合并（gap>0x200 分段），减少子进程次数。
    返回 {addr: "path:line"}，失败/无源码行为空。
    """
    tc = find_toolchain()
    exe = objdump_exe or tc.get("objdump")
    if not exe or not os.path.isfile(exe) or not os.path.isfile(elf):
        return {}
    uniq = sorted({a & ~1 for a in addrs if a})
    if not uniq:
        return {}
    # 分段：相邻 gap <= 0x200 合并，否则分段（避免中间大段反汇编）
    segs, cur = [], [uniq[0]]
    for a in uniq[1:]:
        if a - cur[-1] <= 0x200:
            cur.append(a)
        else:
            segs.append(cur)
            cur = [a]
    segs.append(cur)

    result = {}
    for seg in segs:
        start = max(0, seg[0] - 2)
        stop = seg[-1] + 4
        ins = objdump_disasm(elf, start, stop, with_line=with_line, objdump_exe=exe)
        if not ins:
            continue
        ins_map = {a: fl for a, _m, _o, fl in ins if fl}
        for a in seg:
            for probe in (a, a | 1):   # 兼容 Thumb 位
                if probe in ins_map:
                    result[a] = ins_map[probe]
                    break
    return result


def objdump_func(elf: str, elf_reader, addr: int, pad_before: int = 0,
                 pad_after: int = 0):
    """反汇编 addr 所在整个函数（用 ElfReader 定函数范围 + 可选 padding）。

    elf_reader: ElfReader 实例（qcx216_elf.ElfReader），用于定位函数范围。
    返回 (start, stop, instructions) 或 None。
    """
    exe = find_toolchain().get("objdump")
    if not exe:
        return None
    s = elf_reader.sym_at(addr & ~1)
    if not s or not s.addr:
        return None
    start = max(0, s.addr - pad_before)
    stop = s.addr + (s.size or 0x40) + pad_after
    ins = objdump_disasm(elf, start, stop, with_line=True, objdump_exe=exe)
    if ins is None:
        return None
    return (start, stop, ins)


# ----------------------------------------------------------------------------
# nm 符号（交叉验证 pyelftools）
def nm_symbols(elf: str, nm_exe: str = None):
    """nm -S -C → [(addr, size, type, name)]。失败 None。"""
    tc = find_toolchain()
    exe = nm_exe or tc.get("nm")
    if not exe or not os.path.isfile(exe) or not os.path.isfile(elf):
        return None
    try:
        r = subprocess.run([exe, "-S", "-C", elf], capture_output=True,
                           text=True, timeout=120, errors="replace")
    except Exception:
        return None
    if r.returncode != 0:
        return None
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        # "0042077e 0000003c t prvInsertTimerInActiveList"
        if len(parts) >= 4 and re.fullmatch(r"[0-9a-fA-F]+", parts[0]) \
                and re.fullmatch(r"[0-9a-fA-F]+", parts[1]):
            out.append((int(parts[0], 16), int(parts[1], 16), parts[2],
                        " ".join(parts[3:])))
    return out


def toolchain_status() -> str:
    """人类可读的工具链状态（用于 full-analyze 头部 / 调试）。"""
    tc = find_toolchain()
    if not tc:
        return "(未找到 arm-none-eabi 工具链；将降级 capstone/纯Python 反汇编)"
    parts = []
    for key in ("objdump", "nm", "readelf"):
        if tc.get(key):
            parts.append("%s=OK" % key)
        else:
            parts.append("%s=MISSING" % key)
    return "arm-none-eabi: " + ", ".join(parts) + " @ " + os.path.dirname(
        next(iter(tc.values())))
