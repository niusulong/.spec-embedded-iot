#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 异常存储（excepInfoStore）解析。

Unisoc QCX216 的异常转储结构与 EC/ASR 都不同，已从 RamDumpData_20260629_103016.bin
逆向得到关键布局（excepInfoStore @0x004232f4）：

  +0x00  magic1 = 0xEC112013        有效异常转储标志
  +0x04  magic2 = 0xAA010129
  +0x08 .. +0xC0  header / 寄存器快照 / 内存布局描述
  +0xC0..         ASCII assert 字符串区，格式：
                  "interrupt  Func:OsaCreateFastSignal\r\n  Line:146\r\n  Val:0xc,0x0,0x0\r\n"

寄存器快照区里散落着异常发生时刻的调用地址（Thumb 代码指针），例如本例：
  +0x34 = 0x00003306 -> MPDMA_interruptHandler+0x27   （中断入口，对应 "interrupt"）
  +0x48 = 0x0041bb05 -> OsaCreateFastSignal+0x60       （assert 触发点，对应 Func:）

由于精确的栈帧偏移会随固件版本变化，本解析器采用「结构无关」的稳健策略：
  1. ASCII 扫描提取 assert 文本（最可靠的 ASSERT 依据）
  2. 全区扫描落在 ELF 可执行 section 内的 4 字节值作为调用栈候选
  3. 用 ELF 符号 + DWARF 行号解码每个候选地址

这样既能稳定识别 ASSERT，也为 HardFault 的栈帧/调用链提供线索。
"""
import re

from qcx216_common import u32, EXCEP_MAGIC1, EXCEP_MAGIC2

# 扫描窗口：assert 字符串在 store+0xC0 附近，留足余量
SCAN_SIZE = 0x800

# ASSERT 文本特征关键字（大小写不敏感匹配）
ASSERT_KEYWORDS = ("func:", "line:", "val:", "assert", "!!!ap")


def _scan_assert_text(region: bytes) -> str:
    """以关键字为锚点精确提取 assert 文本。

    excepInfoStore 的 assert 串形如：
      "...\\0\\0interrupt  Func:OsaCreateFastSignal\\r\\n  Line:146\\r\\n  Val:0xc,0x0,0x0\\r\\n\\0\\0"
    特点：Func/Line/Val 各字段间是 \\r\\n，串首尾有 null 填充。
    若整段扫描会把后面的 RAM 噪音也拼进来，因此以 'Func:'/'Line:' 为锚点，
    向两侧扩展直到遇到「连续 >=2 个不可打印字节」为止。
    """
    low = region.lower()
    anchor = low.find(b"func:")
    if anchor < 0:
        anchor = low.find(b"line:")
    if anchor < 0:
        anchor = low.find(b"assert")
    if anchor < 0:
        return ""

    def printable(b: int) -> bool:
        return (32 <= b < 127) or b in (0x0D, 0x0A, 0x09)

    # 向后扩展：连续 2 个不可打印字节即视为文本结束
    end, gap = anchor, 0
    i = anchor
    while i < len(region):
        if printable(region[i]):
            gap = 0
            end = i + 1
        else:
            gap += 1
            if gap >= 2:
                break
        i += 1

    # 向前回溯：拿到 "interrupt" 等前缀
    start, gap = anchor, 0
    i = anchor - 1
    while i >= 0:
        if printable(region[i]):
            gap = 0
            start = i
        else:
            gap += 1
            if gap >= 2:
                break
        i -= 1

    out = []
    for b in region[start:end]:
        if 32 <= b < 127:
            out.append(chr(b))
        elif out and out[-1] != " ":
            out.append(" ")
    return "".join(out).strip()


# store 头部是异常时刻的寄存器快照区（真正的调用上下文）；
# 后段(+0x300起)多为中断向量转储，+0x400/+0x600 起是函数指针表（USB/ccio），属于噪音。
SNAPSHOT_MAX_OFF = 0x140
# 过滤这些「看着像代码地址、实则为数据/链接器符号」的噪音
_NOISE_SYM = ("$", "Image$", "__sf", "_fake")


def _scan_code_addrs(region: bytes, elf, max_off: int = SNAPSHOT_MAX_OFF):
    """扫描 store 头部寄存器快照区的 Flash 代码地址，返回 [(store_off, addr)]。"""
    out, seen = [], set()
    limit = min(max_off, len(region) - 3)
    for off in range(0, limit, 4):
        v = u32(region, off)
        if v is None or v < 0x10:
            continue
        if not elf.is_code(v):
            continue
        sym = elf.sym_at(v)
        if sym and any(sym.name.startswith(p) or p in sym.name for p in _NOISE_SYM):
            continue
        if v not in seen:
            seen.add(v)
            out.append((off, v))
    return out


def _parse_assert(text: str) -> dict:
    """从 assert 文本提取结构化字段。"""
    low = text.lower()
    out = {}

    m = re.search(r"func\s*[:=]\s*(\S+)", text, re.I)
    out["func"] = m.group(1) if m else None

    m = re.search(r"line\s*[:=]\s*(\d+)", text, re.I)
    out["line"] = int(m.group(1)) if m else None

    m = re.search(r"val\s*[:=]\s*(\S+)", text, re.I)
    out["val"] = m.group(1) if m else None

    # 触发上下文：interrupt（中断）/ 任务名
    if "interrupt" in low or "isr" in low:
        out["context"] = "interrupt"
    else:
        m = re.search(r"task\s*[:=]\s*([^\r\n]+)", text, re.I)
        out["context"] = m.group(1).strip() if m else None
    return out


def _extract_sp_candidates(region: bytes, elf, store_base: int):
    """提取 store 头部里落在已知栈范围的值，作为异常时刻的 SP 候选。

    excepInfoStore 头部（+0x00~+0x60）散落多个 SP 快照（异常处理各阶段）。
    用 __StackLimit/__StackTop（MSP 范围）判定，返回 [(store_off, sp_value, kind)]。
    """
    cands = []
    top = elf.find_symbol("__StackTop")
    lim = elf.find_symbol("__StackLimit")
    msp_lo = lim.addr if lim else 0
    msp_hi = top.addr if top else 0xFFFFFFFF
    for off in range(0, min(len(region), 0x80), 4):
        v = u32(region, off)
        if v is None:
            continue
        if msp_lo <= v <= msp_hi:
            cands.append((off, v, "MSP"))
        elif elf.is_ram(v):
            cands.append((off, v, "RAM"))
    return cands


def parse_excep(data: bytes, elf, store_addr: int, scan_size: int = SCAN_SIZE) -> dict:
    """解析 excepInfoStore。

    Args:
        data: dump 原始字节（base=0x0 时物理地址即偏移）
        elf:  ElfReader 实例
        store_addr: excepInfoStore 物理地址
    """
    region = data[store_addr:store_addr + scan_size]
    if len(region) < 8:
        return {"valid": False, "type": "NoStore", "store_addr": store_addr}

    m1 = u32(region, 0)
    m2 = u32(region, 4)
    valid = (m1 == EXCEP_MAGIC1)

    assert_str = _scan_assert_text(region)
    low = assert_str.lower()
    has_assert_kw = any(k in low for k in ASSERT_KEYWORDS)

    code_addrs = _scan_code_addrs(region, elf)
    sp_cands = _extract_sp_candidates(region, elf, store_addr)

    a = _parse_assert(assert_str) if assert_str else None
    if a is not None:
        # "interrupt" 前缀常被若干 null 与 "Func:" 隔开，单独在前缀窗口里检测
        low_region = region.lower()
        func_off = low_region.find(b"func:")
        pre = low_region[:func_off] if func_off >= 0 else low_region[:0x100]
        if b"interrupt" in pre or b"isr" in pre:
            a["context"] = "interrupt"

    # 类型判定：assert 文本是 ASSERT 的金标准；否则 magic 有效按 HardFault 处理
    if has_assert_kw:
        etype = "ASSERT"
    elif valid:
        etype = "HardFault"
    else:
        etype = "Unknown"

    return {
        "store_addr": store_addr,
        "magic1": m1, "magic2": m2, "valid": valid,
        "type": etype,
        "assert_str": assert_str,
        "assert": a,
        "code_addrs": code_addrs,
        "sp_candidates": sp_cands,
        "region": region,   # 供字段解读
    }


def format_excep(res: dict, elf) -> str:
    """格式化异常解析结果为可读文本。"""
    lines = []
    sa = res["store_addr"]
    lines.append(f"## Exception Store @0x{sa:08X}")
    lines.append(f"  magic1 = 0x{res['magic1']:08X}  "
                 f"{'(VALID)' if res['valid'] else '(mismatch)'}   "
                 f"magic2 = 0x{res['magic2']:08X}")
    lines.append(f"  >> Exception Type: {res['type']}")

    a = res.get("assert")
    if a and (a.get("func") or a.get("line")):
        lines.append("")
        lines.append("  ### ASSERT info")
        lines.append(f"    Func    : {a.get('func')}")
        lines.append(f"    Line    : {a.get('line')}")
        lines.append(f"    Val     : {a.get('val')}")
        lines.append(f"    Context : {a.get('context')}")
        if res["assert_str"]:
            lines.append("    --- raw assert text ---")
            for ln in res["assert_str"].splitlines():
                if ln.strip():
                    lines.append(f"    | {ln.strip()}")

    # 异常时刻的 SP 候选（供栈回溯定位调用链）
    if res.get("sp_candidates"):
        lines.append("")
        lines.append("  ### SP candidates (exception-time stack pointers)")
        lines.append(f"    {'store+off':<10} {'SP':<11} kind")
        for off, sp, kind in res["sp_candidates"]:
            lines.append(f"    +0x{off:04X}     0x{sp:08X}  {kind}")

    # 调用栈候选：把寄存器快照区里的代码地址解码出来
    if res["code_addrs"]:
        lines.append("")
        lines.append("  ### Code addresses captured in exception store (call-chain candidates)")
        lines.append(f"    {'store+off':<10} {'addr':<10} symbol")
        for off, addr in res["code_addrs"]:
            loc = elf.locate(addr)
            sym = loc["symbol"] or "?"
            disp = f"{sym}+0x{loc['sym_offset']:X}" if loc["sym_offset"] is not None else sym
            extra = f"  [{loc['file']}:{loc['line']}]" if loc["file"] else ""
            lines.append(f"    +0x{off:04X}     0x{addr:08X}  {disp}{extra}")
    return "\n".join(lines)
