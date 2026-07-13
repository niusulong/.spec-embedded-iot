#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
ARM Cortex-R Crash Dump Analyzer
=================================
分析 TRACE32 导出的 crash dump 文件，定位嵌入式系统死机原因。

功能:
  1. 解析 .cmm/.xdb 脚本提取寄存器和异常信息
  2. 自动确定 DDR dump 基地址
  3. 栈使用分析（0xEF 填充检测，栈溢出判定）
  4. Map 文件符号解析
  5. 栈帧调用链还原
  6. 生成分析报告

用法:
  # 解析 .cmm 文件
  python dump_analyzer.py parse-cmm <cmm_file>

  # 确定 DDR 基地址
  python dump_analyzer.py ddr-base --ddr <ddr.bin> --cmm <cmm_file>

  # 栈使用分析
  python dump_analyzer.py stack-analysis --ddr <ddr.bin> --base <base_addr> \
      --stack-bottom <addr> --stack-top <addr> --sp <addr> --map <map_file>

  # 解析 map 文件中的地址
  python dump_analyzer.py resolve --map <map_file> <addr1> <addr2> ...

  # 一键完整分析
  python dump_analyzer.py full-analyze --dump-dir <dir> --map <map_file>
"""

import struct
import re
import sys
import os
import argparse
from collections import OrderedDict

# Import shared utilities
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_map_file, lookup_address, format_symbol, find_elf_section, read_elf_bytes, parse_elf_sections

# ============================================================================
# ThreadX 常量
# ============================================================================
THREADX_STACK_FILL = 0xEFEFEFEF  # ThreadX 栈未使用填充模式
STACK_GUARD_DEADBEEF = 0xDEADBEEF  # 常见的栈保护标记


# ============================================================================
# 代码地址范围推断
# ============================================================================

def infer_code_range(symbols_data):
    """从 MAP 文件符号数据推断代码地址范围。

    遍历所有符号地址，取最小和最大值并扩展到 16MB 边界。
    如果无法推断（无符号或数据异常），返回 (0, 0xFFFFFFFF) 全范围。

    Args:
        symbols_data: load_map_symbols() 返回的数据（tuple 或 list）

    Returns:
        (code_min, code_max) 地址范围元组
    """
    entries = symbols_data[0] if isinstance(symbols_data, tuple) else symbols_data
    if not entries:
        return (0, 0xFFFFFFFF)

    addrs = []
    for e in entries:
        addr = e[0] if isinstance(e, (list, tuple)) else e
        if isinstance(addr, int) and addr > 0:
            addrs.append(addr)

    if len(addrs) < 10:
        return (0, 0xFFFFFFFF)

    # 取第 5 和第 95 百分位避免极端值
    addrs.sort()
    lo = addrs[max(0, len(addrs) // 20)]
    hi = addrs[min(len(addrs) - 1, len(addrs) * 19 // 20)]

    # 扩展到 16MB 边界
    mask = 0x01000000 - 1
    code_min = lo & ~mask
    code_max = (hi + mask) & ~mask

    # 安全限制：范围不应超过 4GB
    if code_max - code_min > 0x100000000:
        return (0, 0xFFFFFFFF)

    return (code_min, code_max)

# ============================================================================
# .cmm / .xdb 解析器
# ============================================================================

def parse_cmm(filepath):
    """解析 TRACE32 .cmm 或 .xdb 脚本文件"""
    result = {
        'registers': {},
        'stack_dump': OrderedDict(),
        'fault_info': {},
        'prints': [],
    }

    with open(filepath, 'r', errors='replace') as f:
        content = f.read()

    for line in content.split('\n'):
        line = line.strip()

        # 解析寄存器 (CMM格式: set reg r0=0x... 或 XDB格式: register.set r0 0x...)
        m = re.match(r'(?:set reg|register\.set)\s+(\w+)\s*=\s*(0x[0-9a-fA-F]+)', line)
        if not m:
            m = re.match(r'(?:set reg|register\.set)\s+(\w+)\s+(0x[0-9a-fA-F]+)', line)
        if m:
            reg_name = m.group(1).lower()
            reg_val = int(m.group(2), 16)
            result['registers'][reg_name] = reg_val
            continue

        # 解析栈帧数据 (CMM: set value /size=long addr=val, XDB: data.set addr %long val)
        m = re.match(r'set value /size=long\s+(0x[0-9a-fA-F]+)\s*=\s*(0x[0-9a-fA-F]+)', line)
        if not m:
            m = re.match(r'data\.set\s+(0x[0-9a-fA-F]+)\s+%long\s+(0x[0-9a-fA-F]+)', line)
        if m:
            addr = int(m.group(1), 16)
            val = int(m.group(2), 16)
            result['stack_dump'][addr] = val
            continue

        # 解析 FAULT_STATUS (XDB格式)
        m = re.match(r'SET SYSREGISTER /COPROCESSOR FAULT_STATUS=(0x[0-9a-fA-F]+)', line, re.I)
        if m:
            result['fault_info']['FAULT_STATUS'] = int(m.group(1), 16)
            continue

        # 解析 PRINT 行 (异常描述信息)
        m = re.match(r'PRINT\s+"(.+)"', line)
        if m:
            text = m.group(1)
            result['prints'].append(text)

            # 提取关键信息
            fm = re.search(r'DataAbort.*?AT\s+0?x?([0-9a-fA-F]+)', text, re.I)
            if fm:
                result['fault_info']['exception_type'] = 'DataAbort'
                result['fault_info']['exception_pc'] = int(fm.group(1), 16)

            fm = re.search(r'Error address.*?:\s*0?x?([0-9a-fA-F]{4,})', text, re.I)
            if fm:
                result['fault_info']['FAULT_ADDRESS'] = int(fm.group(1), 16)

            fm = re.search(r'Error thread:\s*(\w+)', text, re.I)
            if fm:
                result['fault_info']['task_name'] = fm.group(1)

            fm = re.search(r'stack range\s*\((0x[0-9a-fA-F]+)\.\.(0x[0-9a-fA-F]+)\)', text, re.I)
            if fm:
                result['fault_info']['stack_bottom'] = int(fm.group(1), 16)
                result['fault_info']['stack_top'] = int(fm.group(2), 16)

    # 从 XDB 文件中提取额外的 FAULT_STATUS
    for line in content.split('\n'):
        m = re.match(r'PRINT\s+"FAULT_STATUS=(0x[0-9a-fA-F]+)"', line.strip(), re.I)
        if m:
            result['fault_info']['FAULT_STATUS'] = int(m.group(1), 16)
        m = re.match(r'PRINT\s+"FAULT_ADDRESS=(0x[0-9a-fA-F]+)"', line.strip(), re.I)
        if m:
            result['fault_info']['FAULT_ADDRESS'] = int(m.group(1), 16)

    return result


def print_cmm_summary(parsed):
    """打印 .cmm 解析摘要"""
    regs = parsed['registers']
    fault = parsed['fault_info']

    print("=" * 60)
    print("  TRACE32 Dump Summary")
    print("=" * 60)

    # 寄存器
    # ARM 寄存器别名映射: r13=SP, r14=LR
    reg_aliases = {'r13': 'sp', 'r14': 'lr'}
    for alias_from, alias_to in reg_aliases.items():
        if alias_from in regs and alias_to not in regs:
            regs[alias_to] = regs[alias_from]

    print("\n--- Key Registers ---")
    key_regs = ['pc', 'lr', 'sp', 'cpsr', 'r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7']
    for reg in key_regs:
        if reg in regs:
            print("  %-6s = 0x%08X" % (reg.upper(), regs[reg]))

    # 异常信息
    if fault:
        print("\n--- Fault Info ---")
        for key, val in fault.items():
            if isinstance(val, bool):
                print("  %-20s = %s" % (key, val))
            elif isinstance(val, int):
                print("  %-20s = 0x%08X" % (key, val))
            else:
                print("  %-20s = %s" % (key, val))

    # PRINT 信息
    if parsed['prints']:
        print("\n--- Debug Prints ---")
        for p in parsed['prints']:
            print("  %s" % p)

    # 栈帧数据
    if parsed['stack_dump']:
        print("\n--- Stack Dump (%d entries) ---" % len(parsed['stack_dump']))
        for addr, val in parsed['stack_dump'].items():
            marker = ""
            if val == THREADX_STACK_FILL:
                marker = " (unused fill)"
            elif val == STACK_GUARD_DEADBEEF:
                marker = " (guard)"
            print("  0x%08X: 0x%08X%s" % (addr, val, marker))

    return parsed


# ============================================================================
# DDR Dump 分析
# ============================================================================

def find_ddr_base(ddr_path, parsed_data):
    """
    自动确定 DDR dump 基地址。
    策略: 从栈帧或寄存器中取已知地址-值对，在 DDR dump 中搜索匹配位置。
    支持两种来源: stack_dump (优先) 或 registers (回退，用于 WDT/SYS_RESET 场景)。
    """
    # 尝试从栈帧数据获取已知地址-值对
    search_pairs = []
    stack_dump = parsed_data.get('stack_dump', {})
    if stack_dump:
        for addr, val in list(stack_dump.items())[:20]:
            if val != 0 and val != THREADX_STACK_FILL and val != STACK_GUARD_DEADBEEF:
                search_pairs.append((addr, val))
                if len(search_pairs) >= 5:
                    break

    # 回退: 使用寄存器值作为搜索锚点
    # SP 指向栈上的某个地址，该地址处应有一个已知的栈值
    # 对于 WDT/SYS_RESET，stack_dump 为空但 registers 可能有值
    if not search_pairs:
        regs = parsed_data.get('registers', {})
        fault = parsed_data.get('fault_info', {})
        # 用 SP 地址处的值作为搜索锚点
        # 但我们不知道 SP 处的值，所以尝试用已知常量:
        # stack_bottom 和 stack_top 存储在 fault_info 中，但它们是地址不是值
        # 最可靠的回退: 在 DDR dump 中搜索 TX_THREAD_ID magic
        # 此时不自动检测，需要用户手动指定
        print("WARNING: No stack dump data available (common for WDT/SYS_RESET)")
        print("  Cannot auto-detect DDR base without stack frame data.")
        print("  Use --base to specify DDR base address manually.")
        return None

    # 用第一个值搜索候选位置
    target_addr, target_val = search_pairs[0]
    target_bytes = struct.pack('<I', target_val)

    print("Searching for 0x%08X (expected at addr 0x%08X)..." % (target_val, target_addr))

    candidates = []
    with open(ddr_path, 'rb') as f:
        chunk_size = 4 * 1024 * 1024  # 4MB
        overlap = len(target_bytes) - 1
        offset = 0
        prev_tail = b''

        while True:
            data = f.read(chunk_size)
            if not data:
                break

            search_data = prev_tail + data
            idx = 0
            while True:
                pos = search_data.find(target_bytes, idx)
                if pos == -1:
                    break
                abs_offset = offset - len(prev_tail) + pos
                candidate_base = target_addr - abs_offset
                candidates.append(candidate_base)
                idx = pos + 1

            prev_tail = data[-overlap:] if len(data) >= overlap else data
            offset += len(data)

    if not candidates:
        print("ERROR: Value 0x%08X not found in DDR dump" % target_val)
        return None

    print("Found %d candidate(s)" % len(candidates))

    # 用其他已知值交叉验证
    verified_base = None
    for base in candidates:
        all_match = True
        with open(ddr_path, 'rb') as f:
            for addr, val in search_pairs[1:]:
                file_offset = addr - base
                if file_offset < 0:
                    all_match = False
                    break
                f.seek(file_offset)
                data = f.read(4)
                if len(data) < 4:
                    all_match = False
                    break
                actual = struct.unpack('<I', data)[0]
                if actual != val:
                    all_match = False
                    break

        if all_match:
            verified_base = base
            break

    if verified_base is not None:
        print("VERIFIED DDR base address: 0x%08X" % verified_base)
    else:
        # 使用第一个候选并警告
        verified_base = candidates[0]
        print("WARNING: Could not cross-verify, using first candidate: 0x%08X" % verified_base)

    return verified_base


def _detect_base_by_thread_scan(ddr_path):
    """
    当没有 stack_dump 数据时，通过搜索 TX_THREAD 结构中的已知模式来推导 DDR base。
    策略: 搜索 TX_THREAD_ID (0x54485244)，读取其 stack_start 字段，
    然后用 stack_start 附近的 0xEFEFEFEF 填充来验证候选 base。
    """
    target = struct.pack('<I', TX_THREAD_ID)
    chunk_size = 4 * 1024 * 1024
    overlap = 3

    # 收集候选 base 地址
    candidate_bases = []

    with open(ddr_path, 'rb') as f:
        offset = 0
        prev_tail = b''
        found_count = 0

        while True:
            data = f.read(chunk_size)
            if not data:
                break

            search_data = prev_tail + data
            idx = 0
            while True:
                pos = search_data.find(target, idx)
                if pos == -1:
                    break

                abs_offset = offset - len(prev_tail) + pos

                # 读取 stack_start (+0x0C) 和 stack_size (+0x14)
                if pos + 0x18 <= len(search_data):
                    ss = struct.unpack_from('<I', search_data, pos + 0x0C)[0]
                    sz = struct.unpack_from('<I', search_data, pos + 0x14)[0]

                    # 验证合理性: ss 应该是合理的高位地址（非零高位字节），
                    # sz 应该是合理的栈大小
                    if ss > 0x10000000 and 0 < sz < 0x100000:
                        # 候选 base: stack_start 应该在 DDR dump 的某个偏移处
                        # stack_start 的文件偏移大约等于 abs_offset 附近的某个值
                        # 尝试: base = ss - (某个合理偏移)
                        # 由于不知道偏移，先收集 (ss, abs_offset) 对
                        candidate_bases.append((ss, abs_offset, sz))

                idx = pos + 1
                found_count += 1
                if found_count > 50:  # 限制搜索数量
                    break

            prev_tail = data[-overlap:] if len(data) >= overlap else data
            offset += len(data)

            if found_count > 50:
                break

    if not candidate_bases:
        print("  No TX_THREAD structures found for base detection")
        return None

    # 策略: 对于每个候选 (stack_start, file_offset, size)，
    # 检查 stack_start 处是否有 0xEFEFEFEF 填充
    # 这要求 base = stack_start - file_offset_of_stack_start
    # 但我们只有 TX_THREAD 的 file_offset，不知道 stack_start 的确切 file_offset

    # 简单启发式: 取第一个候选，假设 DDR dump 覆盖了从 base 开始的区域
    # base ≈ stack_start - estimated_file_offset
    # 对于大多数 ASR 平台，DDR dump 从 ~0x7E200000 开始
    # 所以 base ≈ 0x7E20xxxx 或 0x7E21xxxx
    # 但这个假设太强了。更可靠的方法: 用多个 TX_THREAD 交叉验证

    if len(candidate_bases) >= 2:
        # 两个 TX_THREAD 的 file_offset 差应等于它们的虚拟地址差
        ss1, off1, _ = candidate_bases[0]
        ss2, off2, _ = candidate_bases[1]
        addr_diff = ss1 - ss2
        file_diff = off1 - off2
        if file_diff != 0 and addr_diff != 0:
            base1 = ss1 - off1
            base2 = ss2 - off2
            # 但 TX_THREAD 的 stack_start 不是 TX_THREAD 自身的地址
            # 需要用 TX_THREAD 结构自身的地址
            # 实际上 TX_THREAD 结构的地址 = ddr_base + file_offset
            # 所以 ddr_base = tx_thread_addr - file_offset... 但我们不知道 tx_thread_addr

            # 换个思路: 用 stack_start 值检查 base 候选
            # 如果 base = ss - offset_to_stack_start_in_file
            # 但 stack_start 的文件偏移 = ss - base
            # 我们不知道 stack_start 在文件中的位置

            pass

    # 最终回退: 用第一个 TX_THREAD 的 stack_start
    # 估算 base = stack_start - 第一个 0xEF 填充的文件偏移
    # 搜索 DDR dump 中 stack_start 附近的 0xEFEFEFEF
    ss, _, sz = candidate_bases[0]
    ef_pattern = struct.pack('<I', THREADX_STACK_FILL)

    with open(ddr_path, 'rb') as f:
        # 搜索 0xEFEFEFEF 在文件前 2MB 中的位置
        scan_limit = min(2 * 1024 * 1024, os.path.getsize(ddr_path))
        data = f.read(scan_limit)

        # 找到连续 0xEF 填充区域（ThreadX 栈底标记）
        consecutive = 0
        max_consecutive = 0
        ef_start = -1
        for i in range(0, len(data) - 3, 4):
            val = struct.unpack_from('<I', data, i)[0]
            if val == THREADX_STACK_FILL:
                if consecutive == 0:
                    ef_start = i
                consecutive += 4
                if consecutive > max_consecutive:
                    max_consecutive = consecutive
            else:
                consecutive = 0

        if max_consecutive >= 32:  # 至少 8 个连续 0xEF
            # 这是某个线程栈底的 0xEF 填充
            # 尝试匹配: 某个 TX_THREAD 的 stack_start 对应这个偏移
            for ss2, off2, sz2 in candidate_bases:
                base_candidate = ss2 - ef_start
                if base_candidate > 0x10000000:
                    # 验证: TX_THREAD 文件偏移处的结构应该有效
                    with open(ddr_path, 'rb') as f2:
                        f2.seek(off2)
                        magic = f2.read(4)
                        if magic == target:
                            print("  Detected DDR base via TX_THREAD + 0xEF pattern: 0x%08X" % base_candidate)
                            return base_candidate

    print("  Could not determine DDR base automatically")
    return None



    """
    从 DDR dump 中提取栈内容并分析栈使用情况。
    """
    stack_size = stack_top - stack_bottom + 1
    symbols = load_map_symbols(map_file) if map_file else ([], [])

    with open(ddr_path, 'rb') as f:
        f.seek(stack_bottom - ddr_base)
        stack_data = f.read(stack_size)

    if len(stack_data) < stack_size:
        print("WARNING: Could not read full stack (got %d of %d bytes)" % (len(stack_data), stack_size))

    print("=" * 60)
    print("  Stack Usage Analysis")
    print("=" * 60)
    print("  Range: 0x%08X .. 0x%08X (%d bytes)" % (stack_bottom, stack_top, stack_size))
    print("  SP:    0x%08X" % sp)

    # 1. 扫描 0xEF 填充模式
    ef_from_bottom = 0
    ef_from_top = 0

    # 从栈底向上扫描（找第一个非 0xEF）
    all_unused = True
    for i in range(0, len(stack_data), 4):
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        if val != THREADX_STACK_FILL:
            ef_from_bottom = i
            all_unused = False
            break
    if all_unused:
        ef_from_bottom = len(stack_data)

    # 从栈顶向下扫描（找第一个非 0xEF）
    all_unused_top = True
    for i in range(len(stack_data) - 4, -1, -4):
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        if val != THREADX_STACK_FILL:
            ef_from_top = len(stack_data) - i - 4
            all_unused_top = False
            break
    if all_unused_top:
        ef_from_top = len(stack_data)

    deepest_used_addr = stack_bottom + ef_from_bottom
    if all_unused:
        peak_usage = 0
    else:
        peak_usage = stack_top - deepest_used_addr + 1
    free_at_bottom = ef_from_bottom
    free_at_top = ef_from_top

    print("\n--- ThreadX Stack Fill (0xEFEFEFEF) Analysis ---")
    print("  Unused from bottom: %d bytes (0x%X)" % (free_at_bottom, free_at_bottom))
    print("  Unused from top:    %d bytes (0x%X)" % (free_at_top, free_at_top))
    print("  Deepest used addr:  0x%08X" % deepest_used_addr)
    print("  Peak stack usage:   %d / %d bytes (%.1f%%)" % (
        peak_usage, stack_size, 100.0 * peak_usage / stack_size))
    print("  Remaining at peak:  %d bytes" % (stack_size - peak_usage))

    # SP 相对分析
    sp_offset = sp - stack_bottom
    if sp_offset < 0 or sp_offset >= stack_size:
        print("\n  WARNING: SP 0x%08X is OUTSIDE stack range!" % sp)
    else:
        space_below_sp = sp_offset  # SP 以下（更深）的空间
        space_above_sp = stack_size - sp_offset  # SP 以上（栈顶方向）的空间
        print("\n--- SP Position ---")
        print("  Below SP (deeper):  %d bytes" % space_below_sp)
        print("  Above SP (top):     %d bytes" % space_above_sp)

    # 栈溢出判定
    print("\n--- Stack Overflow Verdict ---")
    overflow = False
    if free_at_bottom == 0:
        print("  [OVERFLOW] Stack bottom fill pattern is OVERWRITTEN!")
        overflow = True
    elif peak_usage > stack_size * 0.95:
        print("  [HIGH RISK] Peak usage %.1f%% exceeds 95%% threshold" % (
            100.0 * peak_usage / stack_size))
        overflow = True
    elif peak_usage > stack_size * 0.80:
        print("  [WARNING] Peak usage %.1f%% is high (>80%%)" % (
            100.0 * peak_usage / stack_size))
    else:
        print("  [OK] Peak usage %.1f%%, stack overflow RULED OUT" % (
            100.0 * peak_usage / stack_size))

    # 2. 识别栈中的代码地址（调用链还原）
    print("\n--- Call Chain from Stack ---")
    call_chain = []

    # 从 MAP 文件推断代码地址范围，避免硬编码平台特定地址
    code_range_min, code_range_max = infer_code_range(symbols)

    for i in range(0, len(stack_data), 4):
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        if code_range_min <= val <= code_range_max:
            addr = stack_bottom + i
            sym_name = resolve_symbol(symbols, val)
            if sym_name:
                offset_from_sp = addr - sp
                call_chain.append((addr, val, sym_name, offset_from_sp))
                print("  0x%08X: 0x%08X -> %s (SP%+d)" % (
                    addr, val, sym_name, offset_from_sp))

    # 3. 栈帧数据摘要
    print("\n--- Stack Frame Data ---")
    # SP 附近的数据
    sp_idx = sp - stack_bottom
    start = max(0, sp_idx - 48)
    end = min(len(stack_data), sp_idx + 80)
    for i in range(start, end, 4):
        addr = stack_bottom + i
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        marker = ""
        if addr == sp:
            marker = " <-- SP"
        if val == THREADX_STACK_FILL:
            marker += " (unused)"
        elif val == STACK_GUARD_DEADBEEF:
            marker += " (guard)"
        sym = resolve_symbol(symbols, val)
        if sym:
            marker += " [%s]" % sym
        print("  0x%08X: 0x%08X%s" % (addr, val, marker))

    # 栈底区域（溢出检查）
    print("\n--- Stack Bottom (overflow check) ---")
    for i in range(0, min(64, len(stack_data)), 4):
        addr = stack_bottom + i
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        marker = " (unused)" if val == THREADX_STACK_FILL else ""
        if val == STACK_GUARD_DEADBEEF:
            marker = " (guard)"
        print("  0x%08X: 0x%08X%s" % (addr, val, marker))

    return {
        'stack_size': stack_size,
        'peak_usage': peak_usage,
        'free_at_bottom': free_at_bottom,
        'overflow': overflow,
        'call_chain': call_chain,
    }


# ============================================================================
# DDR 栈使用分析
# ============================================================================

def analyze_stack(ddr_path, ddr_base, stack_bottom, stack_top, sp, map_file=None):
    """
    从 DDR dump 中提取栈内容并分析栈使用情况。
    """
    stack_size = stack_top - stack_bottom + 1
    symbols = load_map_symbols(map_file) if map_file else ([], [])

    with open(ddr_path, 'rb') as f:
        f.seek(stack_bottom - ddr_base)
        stack_data = f.read(stack_size)

    if len(stack_data) < stack_size:
        print("WARNING: Could not read full stack (got %d of %d bytes)" % (len(stack_data), stack_size))

    print("=" * 60)
    print("  Stack Usage Analysis")
    print("=" * 60)
    print("  Range: 0x%08X .. 0x%08X (%d bytes)" % (stack_bottom, stack_top, stack_size))
    print("  SP:    0x%08X" % sp)

    # 1. 扫描 0xEF 填充模式
    ef_from_bottom = 0
    ef_from_top = 0

    # 从栈底向上扫描（找第一个非 0xEF）
    all_unused = True
    for i in range(0, len(stack_data), 4):
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        if val != THREADX_STACK_FILL:
            ef_from_bottom = i
            all_unused = False
            break
    if all_unused:
        ef_from_bottom = len(stack_data)

    # 从栈顶向下扫描（找第一个非 0xEF）
    all_unused_top = True
    for i in range(len(stack_data) - 4, -1, -4):
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        if val != THREADX_STACK_FILL:
            ef_from_top = len(stack_data) - i - 4
            all_unused_top = False
            break
    if all_unused_top:
        ef_from_top = len(stack_data)

    deepest_used_addr = stack_bottom + ef_from_bottom
    if all_unused:
        peak_usage = 0
    else:
        peak_usage = stack_top - deepest_used_addr + 1
    free_at_bottom = ef_from_bottom
    free_at_top = ef_from_top

    print("\n--- ThreadX Stack Fill (0xEFEFEFEF) Analysis ---")
    print("  Unused from bottom: %d bytes (0x%X)" % (free_at_bottom, free_at_bottom))
    print("  Unused from top:    %d bytes (0x%X)" % (free_at_top, free_at_top))
    print("  Deepest used addr:  0x%08X" % deepest_used_addr)
    print("  Peak stack usage:   %d / %d bytes (%.1f%%)" % (
        peak_usage, stack_size, 100.0 * peak_usage / stack_size))
    print("  Remaining at peak:  %d bytes" % (stack_size - peak_usage))

    # SP 相对分析
    sp_offset = sp - stack_bottom
    if sp_offset < 0 or sp_offset >= stack_size:
        print("\n  WARNING: SP 0x%08X is OUTSIDE stack range!" % sp)
    else:
        space_below_sp = sp_offset
        space_above_sp = stack_size - sp_offset
        print("\n--- SP Position ---")
        print("  Below SP (deeper):  %d bytes" % space_below_sp)
        print("  Above SP (top):     %d bytes" % space_above_sp)

    # 栈溢出判定
    print("\n--- Stack Overflow Verdict ---")
    overflow = False
    if free_at_bottom == 0:
        print("  [OVERFLOW] Stack bottom fill pattern is OVERWRITTEN!")
        overflow = True
    elif peak_usage > stack_size * 0.95:
        print("  [HIGH RISK] Peak usage %.1f%% exceeds 95%% threshold" % (
            100.0 * peak_usage / stack_size))
        overflow = True
    elif peak_usage > stack_size * 0.80:
        print("  [WARNING] Peak usage %.1f%% is high (>80%%)" % (
            100.0 * peak_usage / stack_size))
    else:
        print("  [OK] Peak usage %.1f%%, stack overflow RULED OUT" % (
            100.0 * peak_usage / stack_size))

    # 2. 识别栈中的代码地址（调用链还原）
    print("\n--- Call Chain from Stack ---")
    call_chain = []

    # 从 MAP 文件推断代码地址范围，避免硬编码平台特定地址
    code_range_min, code_range_max = infer_code_range(symbols)

    for i in range(0, len(stack_data), 4):
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        if code_range_min <= val <= code_range_max:
            addr = stack_bottom + i
            sym_name = resolve_symbol(symbols, val)
            if sym_name:
                offset_from_sp = addr - sp
                call_chain.append((addr, val, sym_name, offset_from_sp))
                print("  0x%08X: 0x%08X -> %s (SP%+d)" % (
                    addr, val, sym_name, offset_from_sp))

    # 3. 栈帧数据摘要
    print("\n--- Stack Frame Data ---")
    sp_idx = sp - stack_bottom
    start = max(0, sp_idx - 48)
    end = min(len(stack_data), sp_idx + 80)
    for i in range(start, end, 4):
        addr = stack_bottom + i
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        marker = ""
        if addr == sp:
            marker = " <-- SP"
        if val == THREADX_STACK_FILL:
            marker += " (unused)"
        elif val == STACK_GUARD_DEADBEEF:
            marker += " (guard)"
        sym = resolve_symbol(symbols, val)
        if sym:
            marker += " [%s]" % sym
        print("  0x%08X: 0x%08X%s" % (addr, val, marker))

    # 栈底区域（溢出检查）
    print("\n--- Stack Bottom (overflow check) ---")
    for i in range(0, min(64, len(stack_data)), 4):
        addr = stack_bottom + i
        val = struct.unpack('<I', stack_data[i:i+4])[0]
        marker = " (unused)" if val == THREADX_STACK_FILL else ""
        if val == STACK_GUARD_DEADBEEF:
            marker = " (guard)"
        print("  0x%08X: 0x%08X%s" % (addr, val, marker))

    return {
        'stack_size': stack_size,
        'peak_usage': peak_usage,
        'free_at_bottom': free_at_bottom,
        'overflow': overflow,
        'call_chain': call_chain,
    }


# ============================================================================
# 全线程栈溢出扫描
# ============================================================================

TX_THREAD_ID = 0x54485244  # "THRD" little-endian
TX_THREAD_CSIT = 0x54495343  # "CSIT" little-endian


def scan_all_threads(ddr_path, base_addr):
    """
    扫描 DDR dump 中的所有 TX_THREAD 结构，检查每个线程的栈溢出情况。

    全量加载 DDR dump 到内存后扫描，避免流式扫描中文件指针被随机读取破坏的问题。

    TX_THREAD 关键成员:
      +0x00: thread_id (0x54485244)
      +0x04: thread_state
      +0x08: thread_stack_ptr (当前SP)
      +0x0C: thread_stack_start (栈底)
      +0x10: thread_stack_end (栈顶)
      +0x14: thread_stack_size
      +0x28: thread_name_ptr (指向名称字符串)

    返回: (threads_list, overflow_count, high_usage_count)
    """
    SCAN_WINDOW = 80  # Need at least 80 bytes to validate a TX_THREAD

    with open(ddr_path, 'rb') as f:
        ddr = f.read()

    def read32(off):
        if 0 <= off < len(ddr) - 3:
            return struct.unpack('<I', ddr[off:off + 4])[0]
        return None

    # 地址合理性检查：使用 DDR base + dump size 作为有效范围
    # 不硬编码绝对地址，任何在 DDR dump 范围内的地址都视为有效
    file_size = len(ddr)
    addr_lo = base_addr          # DDR 起始地址
    addr_hi = base_addr + file_size  # DDR 结束地址

    threads = []
    i = 0
    while i <= len(ddr) - SCAN_WINDOW:
        val = struct.unpack('<I', ddr[i:i + 4])[0]
        if val == TX_THREAD_ID:
            state = struct.unpack('<I', ddr[i + 4:i + 8])[0]
            sp = struct.unpack('<I', ddr[i + 8:i + 12])[0]
            ss = struct.unpack('<I', ddr[i + 12:i + 16])[0]
            se = struct.unpack('<I', ddr[i + 16:i + 20])[0]
            sz = struct.unpack('<I', ddr[i + 20:i + 24])[0]
            name_ptr = struct.unpack('<I', ddr[i + 40:i + 44])[0] if i + 44 <= len(ddr) else 0

            # Sanity check: stack_start must be within DDR dump address range,
            # AND stack_size should closely match (stack_end - stack_start).
            # ThreadX tx_thread_stack_size should equal stack_end - stack_start + 1
            # within alignment tolerance. This filters out false TX_THREAD_ID matches.
            actual_sz = se - ss + 1 if (se and se > ss) else 0
            sz_match = (abs(actual_sz - sz) <= 16) if actual_sz > 0 and sz > 0 else False
            if (sz and sz >= 32 and sz < 0x100000 and
                    addr_lo <= ss <= addr_hi and
                    se and se > ss and
                    actual_sz < 0x200000 and  # stack < 2MB
                    sz_match):
                # Read thread name
                name = ''
                if name_ptr and addr_lo <= name_ptr <= addr_hi:
                    name_off = name_ptr - base_addr
                    if 0 <= name_off < len(ddr) - 16:
                        nb = ddr[name_off:name_off + 16]
                        for b in nb:
                            if 0x20 <= b < 0x7F:
                                name += chr(b)
                            else:
                                break

                # Check stack bottom 0xEF fill pattern
                ef_intact = 0
                ef_check_limit = min(128, (se - ss) // 4) * 4
                for off in range(0, ef_check_limit, 4):
                    addr = ss + off
                    v = read32(addr - base_addr)
                    if v == THREADX_STACK_FILL:
                        ef_intact += 4
                    else:
                        break

                # Check DEADBEEF guard
                guard_ok = False
                g1 = read32(ss - 8 - base_addr)
                g2 = read32(ss - 4 - base_addr)
                if g1 == STACK_GUARD_DEADBEEF or g2 == STACK_GUARD_DEADBEEF:
                    guard_ok = True

                # Calculate peak usage
                total = se - ss + 1
                peak_pct = ((total - ef_intact) * 100 // total) if total > 0 else 100

                is_overflow = (ef_intact == 0)
                is_high = (not is_overflow and peak_pct > 90)

                threads.append({
                    'tcb': base_addr + i,
                    'name': name,
                    'state': state,
                    'sp': sp,
                    'stack_start': ss,
                    'stack_end': se,
                    'stack_size': sz,
                    'ef_intact': ef_intact,
                    'peak_pct': peak_pct,
                    'guard_ok': guard_ok,
                    'is_overflow': is_overflow,
                    'is_high': is_high,
                })
            i += 4
        else:
            i += 4

    # 输出结果
    overflow_count = sum(1 for t in threads if t['is_overflow'])
    high_count = sum(1 for t in threads if t['is_high'])

    print("=" * 60)
    print("  All Thread Stack Overflow Scan")
    print("=" * 60)
    print("  Total TX_THREAD found: %d" % len(threads))
    print("  Stack overflow (0xEF overwritten): %d" % overflow_count)
    print("  High usage (>90%%): %d" % high_count)
    print()

    if overflow_count > 0:
        print("--- OVERFLOW DETECTED ---")
        for t in threads:
            if t['is_overflow']:
                print("  *** %-12s TCB=0x%08X stack=0x%08X..0x%08X (%d bytes)" % (
                    t['name'], t['tcb'], t['stack_start'], t['stack_end'], t['stack_size']))
        print()

    if high_count > 0:
        print("--- HIGH USAGE (>90%%) ---")
        for t in threads:
            if t['is_high']:
                print("  %-12s stack=%d bytes, ~%d%% used, %d bytes 0xEF intact" % (
                    t['name'], t['stack_size'], t['peak_pct'], t['ef_intact']))
        print()

    # 崩溃任务的详情
    print("--- Thread List Summary ---")
    print("  %-12s %-6s %-10s %-10s %s" % ("Name", "Size", "Peak%", "0xEF", "Overflow"))
    for t in threads:
        status = "OVERFLOW!" if t['is_overflow'] else ("HIGH" if t['is_high'] else "OK")
        print("  %-12s %-6d %-10d %-10d %s" % (
            t['name'], t['stack_size'], t['peak_pct'], t['ef_intact'], status))

    return threads, overflow_count, high_count


# ============================================================================
# Map 文件解析 — delegates to common.py
# ============================================================================

def load_map_symbols(map_path):
    """加载 map 文件符号。返回 (entries, code_addrs) 元组（来自 common.py）。"""
    return parse_map_file(map_path)


def resolve_symbol(symbols_data, target_addr):
    """
    将地址解析为函数名+偏移。
    symbols_data 是 (entries, code_addrs) 元组（来自 load_map_symbols）。
    """
    if not symbols_data:
        return None
    if isinstance(symbols_data, tuple) and len(symbols_data) == 2:
        entries, code_addrs = symbols_data
        return format_symbol(entries, target_addr, code_addrs)
    return None


def resolve_addresses(map_path, addresses):
    """批量解析地址列表"""
    symbols = load_map_symbols(map_path)
    if isinstance(symbols, tuple):
        entries, code_addrs = symbols
    else:
        entries, code_addrs = symbols, None
    results = []

    for addr_str in addresses:
        addr = int(addr_str, 16) if addr_str.startswith('0x') else int(addr_str)
        sym = resolve_symbol(symbols, addr)
        if sym:
            results.append((addr, sym))
            print("0x%08X -> %s" % (addr, sym))
        else:
            results.append((addr, "NOT FOUND"))
            print("0x%08X -> NOT FOUND" % addr)

    return results


# ============================================================================
# EE Hbuf Bin 解析
# ============================================================================

def _u32(data, offset):
    """Read a little-endian UINT32 at offset, return 0 if out of range."""
    if offset + 4 <= len(data):
        return struct.unpack_from('<I', data, offset)[0]
    return 0


def _u16(data, offset):
    """Read a little-endian UINT16 at offset, return 0 if out of range."""
    if offset + 2 <= len(data):
        return struct.unpack_from('<H', data, offset)[0]
    return 0


def _ascii(data, offset, maxlen):
    """Read null-terminated ASCII string from data at offset."""
    end = data.find(b'\x00', offset, offset + maxlen)
    if end < 0:
        end = offset + maxlen
    return data[offset:end].decode('ascii', errors='replace')


def parse_ee_hbuf(bin_path):
    """
    解析 com_EE_Hbuf.bin 二进制异常头 (EE_Entry_t 结构)。
    返回与 parse_cmm() 相同格式的 dict，可直接传给 print_cmm_summary()。

    二进制布局参考: softutil/EEhandler/inc/EEHandler.h (EE_Entry_t)
    """
    with open(bin_path, 'rb') as f:
        data = f.read()

    regs = OrderedDict()
    stack_dump = OrderedDict()
    fault_info = OrderedDict()
    prints = []

    # --- 崩溃描述字符串 (偏移 0x0C, 100 字节) ---
    desc = ''
    if len(data) > 0x0C:
        desc = _ascii(data, 0x0C, 100)
        if desc:
            prints.append(desc)

    # --- 从 desc 解析异常类型 ---
    # 格式: "DataAbort[AT 7E880040],NULL" 或 "Assert ..."
    exc_match = re.match(r'(\w+)\[AT\s+([0-9A-Fa-f]+)\]', desc)
    if exc_match:
        fault_info['exception_type'] = exc_match.group(1)
        fault_info['exception_pc'] = int(exc_match.group(2), 16)
    elif desc.startswith('Assert'):
        fault_info['exception_type'] = 'Assert'
    elif desc:
        fault_info['exception_type'] = desc.split()[0] if desc.split() else 'Unknown'

    # --- 寄存器 (偏移 0x70 ~ 0xB8) ---
    # 0x70: R0~R12 (13 × 4 = 52 字节)
    # 0xA4: SP, 0xA8: LR, 0xAC: PC
    # 0xB0: CPSR, 0xB4: FSR, 0xB8: FAR
    if len(data) >= 0xBC:
        for i in range(13):
            regs['r%d' % i] = _u32(data, 0x70 + i * 4)
        regs['sp'] = _u32(data, 0xA4)
        regs['lr'] = _u32(data, 0xA8)
        regs['pc'] = _u32(data, 0xAC)
        regs['cpsr'] = _u32(data, 0xB0)

        fault_info['FAULT_STATUS'] = _u32(data, 0xB4)
        fault_info['FAULT_ADDRESS'] = _u32(data, 0xB8)

    elif len(data) >= 0xB4:
        # 最小可用：至少到 CPSR
        for i in range(13):
            regs['r%d' % i] = _u32(data, 0x70 + i * 4)
        regs['sp'] = _u32(data, 0xA4)
        regs['lr'] = _u32(data, 0xA8)
        regs['pc'] = _u32(data, 0xAC)
        regs['cpsr'] = _u32(data, 0xB0)

    # --- Context Buffer / 栈 dump (偏移 0xC0~0x2C0) ---
    # 0xC0: contextBufferType (1 字节, 2 = StackDump)
    # 0xC1: contextBuffer (512 字节 = 128 × UINT32)
    if len(data) > 0xC0:
        ctx_type = data[0xC0]
        if ctx_type == 2 and 'sp' in regs:
            sp_val = regs['sp']
            num_words = min(128, (len(data) - 0xC1) // 4)
            for i in range(num_words):
                val = _u32(data, 0xC1 + i * 4)
                addr = sp_val + i * 4
                stack_dump[addr] = val

    # --- 任务信息 (偏移 0x2CC~0x2DC) ---
    if len(data) >= 0x2D6:
        task_name = _ascii(data, 0x2CC, 10)
        if task_name:
            fault_info['task_name'] = task_name
    if len(data) >= 0x2E0:
        fault_info['stack_bottom'] = _u32(data, 0x2D8)
        fault_info['stack_top'] = _u32(data, 0x2DC)

    # --- 崩溃类型枚举 (偏移 0x002, UINT16) ---
    ee_type = _u16(data, 0x002)
    if ee_type:
        fault_info['ee_type_raw'] = ee_type

    # --- PMU 复位原因 (偏移 0xBC, UINT32) ---
    PMU_CAUSES = {0: 'Unknown', 1: 'PowerOn', 2: 'ExternalReset', 3: 'WDT_Reset'}
    pmu_reg = _u32(data, 0xBC)
    if pmu_reg:
        fault_info['PMU_reg'] = pmu_reg
        fault_info['reset_cause'] = PMU_CAUSES.get(pmu_reg, 'Unknown(0x%X)' % pmu_reg)

    # --- Assert file:line 解析 ---
    # desc 格式: "描述,文件名,L:行号" 或 "描述,文件名,L:行号,NULL"
    if desc:
        assert_match = re.search(r',([^,]+\.[ch]),L:(\d+)', desc)
        if assert_match:
            fault_info['assert_file'] = assert_match.group(1)
            fault_info['assert_line'] = int(assert_match.group(2))

    # --- ISR 上下文检测 ---
    # CPSR mode: 0x12=IRQ, 0x11=FIQ; task_name="INTRRPT" also indicates ISR
    cpsr_val = regs.get('cpsr', 0)
    cpsr_mode = cpsr_val & 0x1F
    is_isr = cpsr_mode in (0x11, 0x12)
    task_name = fault_info.get('task_name', '')
    if task_name == 'INTRRPT':
        is_isr = True
    fault_info['is_isr'] = is_isr

    # --- EE 类型映射 ---
    EE_TYPES = {300: 'SYS_RESET', 350: 'ASSERT', 450: 'EXCEPTION', 550: 'WARNING'}
    if ee_type and ee_type in EE_TYPES:
        fault_info['ee_type'] = EE_TYPES[ee_type]

    return {
        'registers': regs,
        'stack_dump': stack_dump,
        'fault_info': fault_info,
        'prints': prints,
    }


# ============================================================================
# WDT Kick 解析
# ============================================================================

def parse_wdt_kick(bin_path):
    """解析 com_wdtKICK.bin — WDT kick 追踪 (3 × 12 字节 = 36 字节)。

    布局: eeWdtTraceBuf[3], 每个 EeWdtTraceBuf_s = {apdc(4), timer32k(4), aptimer32k(4)}
    [0],[1] = 最近两次 kick (ping-pong), [2] = 崩溃时刻时间戳
    """
    TICK_TO_MS = 1000.0 / 32768.0

    with open(bin_path, 'rb') as f:
        data = f.read()

    if len(data) < 36:
        print("WARNING: File too short (%d bytes, expected 36)" % len(data))

    entries = []
    for i in range(min(3, len(data) // 12)):
        off = i * 12
        apdc = _u32(data, off)
        t32k = _u32(data, off + 4)
        apt32k = _u32(data, off + 8)
        entries.append({'apdc': apdc, 'timer32k': t32k, 'aptimer32k': apt32k})

    print("=" * 60)
    print("  WDT Kick Trace")
    print("=" * 60)

    for i, e in enumerate(entries):
        label = ['Last kick', 'Prev kick', 'Crash time'][i]
        print("  [%d] %-12s  apdc=0x%08X  timer32k=0x%08X  aptimer32k=0x%08X" %
              (i, label, e['apdc'], e['timer32k'], e['aptimer32k']))

    def _tick_delta(newer, older):
        """计算两个 32kHz 32-bit 时间戳的差值（毫秒），处理回绕。"""
        if not newer or not older:
            return None
        diff = newer - older
        # 处理 32-bit 回绕: 如果差值 > 一半范围，说明发生了回绕
        if diff > 0x80000000:
            diff -= 0x100000000
        elif diff < -0x80000000:
            diff += 0x100000000
        return abs(diff) * TICK_TO_MS

    # 分析
    print("\n--- Analysis ---")
    if len(entries) >= 2:
        t0 = entries[0]['timer32k']
        t1 = entries[1]['timer32k']
        kick_interval = _tick_delta(t0, t1)
        if kick_interval is not None:
            print("  Kick interval:    %.1f ms" % kick_interval)
        else:
            print("  Kick interval:    N/A (zero timestamp)")

    if len(entries) >= 3:
        t_crash = entries[2]['timer32k']
        timeout_interval = _tick_delta(t_crash, entries[0]['timer32k'])
        if timeout_interval is not None:
            print("  Timeout interval: %.1f ms (last kick to crash)" % timeout_interval)
            if timeout_interval > 10000:
                print("  >> Long gap detected — WDT was not kicked for %.1f seconds!" %
                      (timeout_interval / 1000.0))
        else:
            if not t_crash:
                print("  Timeout interval: N/A (crash timestamp not captured)")
                print("  >> WDT kick was active before crash (kick timestamps present)")
            else:
                print("  Timeout interval: N/A (zero kick timestamp)")

    return entries


# ============================================================================
# RTI Task 解析
# ============================================================================

def parse_rti_tsk(bin_path, last_n=0):
    """解析 com_rti_tsk.bin — ThreadX 任务切换追踪 (rti_rt_t 结构, 2148 字节)。

    布局:
      0x000: rti_rt_cnt (写入索引, 0~127)
      0x010: rti_rt_list[128] (每条 16 字节: "NNT:" + name[8] + timestamp[4])
      0x824: eehandler_magic[2], eehandler_counter, ...

    每条 RT_TASK_INFO:
      +0~+3: 标签 (hex序号 + 类型 'T'/'I' + ':')
      +4~+11: 任务名 (最多 8 字符)
      +12~+15: 时间戳 (32kHz ticks)
    """
    TICK_TO_MS = 1000.0 / 32768.0
    RTI_ARRAY_SIZE = 128
    RTI_ENTRY_SIZE = 16

    with open(bin_path, 'rb') as f:
        data = f.read()

    print("=" * 60)
    print("  ThreadX Task Switch Trace")
    print("=" * 60)

    if len(data) < 16 + RTI_ARRAY_SIZE * RTI_ENTRY_SIZE:
        print("ERROR: File too short (%d bytes)" % len(data))
        return []

    rti_cnt = _u32(data, 0x000)
    ipc_lr = _u32(data, 0x00C)
    print("  Write index (rti_rt_cnt): %d" % rti_cnt)
    print("  ipc_lr: 0x%08X" % ipc_lr)

    # 解析所有条目
    all_entries = []
    for i in range(RTI_ARRAY_SIZE):
        off = 0x010 + i * RTI_ENTRY_SIZE
        raw_tag = data[off:off + 4]
        # 标签格式: byte0=low hex, byte1=high hex, byte2=type char, byte3=':'
        type_char = chr(raw_tag[2]) if raw_tag[2] in (ord('T'), ord('I'), ord('X'), ord('H'), ord('L')) else '?'
        name_bytes = data[off + 4:off + 12]
        name = name_bytes.split(b'\x00')[0].decode('ascii', errors='replace')
        timestamp = _u32(data, off + 12)
        all_entries.append({
            'index': i,
            'type': type_char,
            'name': name,
            'timestamp': timestamp,
        })

    # 按时间顺序输出：从 (rti_cnt+1)%128 开始
    ordered = []
    for j in range(RTI_ARRAY_SIZE):
        idx = (rti_cnt + 1 + j) % RTI_ARRAY_SIZE
        ordered.append(all_entries[idx])

    # 如果指定 --last，只输出最后 N 条
    if last_n > 0 and last_n < RTI_ARRAY_SIZE:
        display = ordered[-last_n:]
    else:
        display = ordered

    print("\n--- Task Switch History (%d entries) ---" % len(display))
    print("%-6s %-4s %-20s %12s %10s" % ('Idx', 'Type', 'Name', 'Timestamp', 'Delta_ms'))
    print('-' * 60)

    prev_ts = 0
    task_counts = {}
    max_delta = 0
    max_delta_entry = None

    for e in display:
        if not e['name'] and not e['timestamp']:
            continue  # 空 entry
        delta_ms = (e['timestamp'] - prev_ts) * TICK_TO_MS if prev_ts and e['timestamp'] else 0
        if delta_ms > max_delta and prev_ts:
            max_delta = delta_ms
            max_delta_entry = e

        print("%-6d %-4s %-20s 0x%08X %10.1f" %
              (e['index'], e['type'], e['name'][:20], e['timestamp'], delta_ms))
        prev_ts = e['timestamp']

        key = e['name']
        task_counts[key] = task_counts.get(key, 0) + 1

    # 统计
    print("\n--- Statistics ---")
    print("  Total non-empty entries: %d" % sum(1 for e in display if e['name'] or e['timestamp']))
    sorted_tasks = sorted(task_counts.items(), key=lambda x: -x[1])
    print("  Top tasks by switch count:")
    for name, cnt in sorted_tasks[:10]:
        print("    %-30s %d" % (name, cnt))

    if max_delta_entry:
        print("  Max interval: %.1f ms (before %s)" % (max_delta, max_delta_entry['name']))

    # Tail fields
    if len(data) >= 0x830:
        print("\n--- Tail Fields ---")
        eh_magic0 = _u32(data, 0x824)
        eh_magic1 = _u32(data, 0x828)
        eh_counter = _u32(data, 0x82C)
        sleep_state = _u32(data, 0x840)
        print("  eehandler_magic:    [0x%08X, 0x%08X]" % (eh_magic0, eh_magic1))
        print("  eehandler_counter:  %d" % eh_counter)
        print("  sleep_state:        0x%08X" % sleep_state)

    return ordered


# ============================================================================
# 堆完整性扫描
# ============================================================================

TX_BYTE_POOL_ID = 0x42595445   # "BYTE"
TX_BYTE_BLOCK_ALLOC = 0xAAAAAAAA
TX_BYTE_BLOCK_FREE = 0xFFFFEEEE


def scan_heap(ddr_path, ddr_base):
    """
    扫描 DDR dump 中的 ThreadX TX_BYTE_POOL 结构，检查堆完整性。

    策略: 流式搜索 TX_BYTE_POOL_ID (0x42595445)，对每个命中验证池结构，
    然后遍历池内 block 检查 alloc/free 标记。
    """
    print("=" * 60)
    print("  Heap Integrity Scan")
    print("=" * 60)

    target = struct.pack('<I', TX_BYTE_POOL_ID)
    chunk_size = 4 * 1024 * 1024
    overlap = 3  # 至少需要 4 字节匹配

    # 地址合理性范围
    ddr_file_size = os.path.getsize(ddr_path)
    addr_lo = ddr_base
    addr_hi = ddr_base + ddr_file_size

    pools_found = []

    with open(ddr_path, 'rb') as f:
        offset = 0
        prev_tail = b''

        while True:
            data = f.read(chunk_size)
            if not data:
                break

            search_data = prev_tail + data
            idx = 0
            while True:
                pos = search_data.find(target, idx)
                if pos == -1:
                    break

                abs_offset = offset - len(prev_tail) + pos
                pool_addr = ddr_base + abs_offset

                # 读取池结构字段 (至少 0x34 字节)
                if pos + 0x34 <= len(search_data):
                    pool_struct = search_data[pos:pos + 0x34]
                    pool_name_ptr = struct.unpack_from('<I', pool_struct, 0x04)[0]
                    pool_available = struct.unpack_from('<I', pool_struct, 0x08)[0]
                    pool_fragments = struct.unpack_from('<I', pool_struct, 0x0C)[0]
                    pool_start = struct.unpack_from('<I', pool_struct, 0x18)[0]
                    pool_size = struct.unpack_from('<I', pool_struct, 0x1C)[0]

                    # 验证合理性: pool_start 应在 DDR dump 地址范围内
                    if (addr_lo <= pool_start <= addr_hi and
                            0 < pool_size < 10 * 1024 * 1024):
                        pools_found.append({
                            'addr': pool_addr,
                            'name_ptr': pool_name_ptr,
                            'available': pool_available,
                            'fragments': pool_fragments,
                            'pool_start': pool_start,
                            'pool_size': pool_size,
                            'file_offset': abs_offset,
                        })

                idx = pos + 1

            prev_tail = data[-overlap:] if len(data) >= overlap else data
            offset += len(data)

    if not pools_found:
        print("\n  No TX_BYTE_POOL structures found in DDR dump.")
        return []

    print("\n  Found %d byte pool(s):\n" % len(pools_found))

    for pool in pools_found:
        print("  Pool at 0x%08X: start=0x%08X size=%d (%d KB)" %
              (pool['addr'], pool['pool_start'],
               pool['pool_size'], pool['pool_size'] // 1024))
        print("    available=%d  fragments=%d" %
              (pool['available'], pool['fragments']))

        # 遍历池内 block
        block_offset = pool['pool_start'] - ddr_base
        pool_end = block_offset + pool['pool_size']

        if block_offset < 0:
            print("    SKIP: pool_start below DDR base")
            continue

        total_blocks = 0
        alloc_blocks = 0
        free_blocks = 0
        corrupt_blocks = 0
        corrupt_details = []

        with open(ddr_path, 'rb') as f:
            cur = block_offset
            max_blocks = 10000  # 安全限制
            while cur + 8 <= pool_end and total_blocks < max_blocks:
                f.seek(cur)
                header = f.read(8)
                if len(header) < 8:
                    break

                marker = struct.unpack_from('<I', header, 0)[0]
                blk_size = struct.unpack_from('<I', header, 4)[0]

                if marker == TX_BYTE_BLOCK_ALLOC:
                    alloc_blocks += 1
                elif marker == TX_BYTE_BLOCK_FREE:
                    free_blocks += 1
                else:
                    corrupt_blocks += 1
                    blk_addr = ddr_base + cur
                    corrupt_details.append(
                        "    Block at 0x%08X: INVALID marker 0x%08X" %
                        (blk_addr, marker))

                # 前进到下一个 block
                if blk_size < 20:  # TX_BYTE_BLOCK_MIN
                    break  # 无法继续遍历
                cur += blk_size
                total_blocks += 1

        print("    Blocks: %d total, %d alloc, %d free, %d corrupted" %
              (total_blocks, alloc_blocks, free_blocks, corrupt_blocks))

        if corrupt_blocks > 0:
            print("    *** HEAP CORRUPTION DETECTED ***")
            for detail in corrupt_details[:5]:  # 最多显示 5 条
                print(detail)
            if len(corrupt_details) > 5:
                print("    ... and %d more corrupted blocks" %
                      (len(corrupt_details) - 5))
        else:
            print("    Pool integrity: OK")

        print()

    return pools_found


# ============================================================================
# 版本校验
# ============================================================================

def check_version(hbuf_path, axf_path=None, map_path=None, custver_path=None):
    """
    对比 EE_Hbuf DBversionID + com_CustVer.bin + AXF BuildVersion 三方版本。
    不匹配时输出警告。
    """
    print("=" * 60)
    print("  Version Check")
    print("=" * 60)

    # 1. 从 EE_Hbuf 偏移 0x2FC 读取 DBversionID (16 字节)
    db_version = None
    try:
        with open(hbuf_path, 'rb') as f:
            f.seek(0x2FC)
            raw = f.read(16)
            if raw:
                db_version = raw.split(b'\x00')[0].decode('ascii', errors='replace')
    except Exception as e:
        print("  EE_Hbuf DBversionID: ERROR (%s)" % e)

    print("  EE_Hbuf DBversionID:  %s" % (db_version or "N/A"))

    # 2. 从 com_CustVer.bin 读取 BuildVersion
    cust_version = None
    if custver_path:
        try:
            with open(custver_path, 'rb') as f:
                raw = f.read()
                cust_version = raw.split(b'\x00')[0].decode('ascii', errors='replace')
        except Exception as e:
            print("  CustVer: ERROR (%s)" % e)

    print("  CustVer BuildVersion: %s" % (cust_version or "N/A"))

    # 3. 从 MAP 文件查找 BuildVersion 符号地址，再从 AXF 读取
    axf_version = None
    if axf_path and map_path:
        try:
            symbols_data = load_map_symbols(map_path)
            entries = symbols_data[0] if isinstance(symbols_data, tuple) else symbols_data
            # 搜索 BuildVersion 符号
            bv_addr = None
            for e in entries:
                name = e[1] if len(e) > 1 else ''
                if name == 'BuildVersion':
                    bv_addr = e[0] if len(e) > 0 else None
                    break

            if bv_addr is not None:
                from common import parse_elf_sections, find_elf_section, read_elf_bytes
                sections = parse_elf_sections(axf_path)
                raw = read_elf_bytes(axf_path, sections, bv_addr, 64)
                if raw:
                    axf_version = raw.split(b'\x00')[0].decode('ascii', errors='replace')
        except Exception as e:
            print("  AXF BuildVersion: ERROR (%s)" % e)

    if axf_version:
        print("  AXF BuildVersion:     %s" % axf_version)
    else:
        print("  AXF BuildVersion:     N/A")

    # 对比
    print("\n--- Comparison ---")
    versions = []
    if db_version:
        versions.append(('DBversionID', db_version))
    if cust_version:
        versions.append(('CustVer', cust_version))
    if axf_version:
        versions.append(('AXF', axf_version))

    if len(versions) <= 1:
        print("  WARNING: Not enough version sources to compare")
        return

    # 检查是否所有版本包含相同的关键标识
    # DBversionID 格式: "dbID=0xHHHHHHHH", CustVer/AXF 格式各不相同
    # 只比较 CustVer 和 AXF（两者应为同一字符串）
    mismatch = False
    if cust_version and axf_version and cust_version != axf_version:
        print("  *** MISMATCH: CustVer != AXF BuildVersion ***")
        print("    CustVer: %s" % cust_version)
        print("    AXF:     %s" % axf_version)
        print("  >> Analysis results may be incorrect! Check AXF/dump version compatibility.")
        mismatch = True

    if not mismatch:
        print("  All available version sources match.")

    return {'db_version': db_version, 'cust_version': cust_version,
            'axf_version': axf_version, 'mismatch': mismatch}


# ============================================================================
# DDR 字符串/字节搜索
# ============================================================================

def ddr_search(ddr_path, ddr_base, pattern, mode='string', context_bytes=32, max_results=20):
    """在 DDR dump 中搜索字符串或字节模式。

    Args:
        ddr_path: DDR dump 文件路径
        ddr_base: DDR 基地址 (整数)
        pattern: 搜索模式 (字符串 / hex / 正则)
        mode: 'string' | 'hex' | 'regex'
        context_bytes: 匹配前后显示的上下文字节数
        max_results: 最大输出条数
    """
    print("=" * 60)
    print("  DDR Search")
    print("=" * 60)

    # 准备搜索模式
    if mode == 'string':
        search_bytes = pattern.encode('ascii', errors='replace')
        print("  Mode: string")
        print("  Pattern: \"%s\" (%d bytes)" % (pattern, len(search_bytes)))
    elif mode == 'hex':
        search_bytes = bytes.fromhex(pattern)
        print("  Mode: hex")
        print("  Pattern: %s (%d bytes)" % (pattern, len(search_bytes)))
    elif mode == 'regex':
        compiled = re.compile(pattern.encode('ascii', errors='replace'))
        print("  Mode: regex")
        print("  Pattern: %s" % pattern)
        search_bytes = None  # regex 不使用固定字节
    else:
        print("  ERROR: Unknown mode '%s'" % mode)
        return []

    chunk_size = 4 * 1024 * 1024
    overlap = len(search_bytes) if search_bytes else 256
    results = []
    found = 0

    with open(ddr_path, 'rb') as f:
        offset = 0
        prev_tail = b''

        while found < max_results:
            data = f.read(chunk_size)
            if not data:
                break

            search_data = prev_tail + data

            if mode == 'regex':
                for m in compiled.finditer(search_data):
                    abs_offset = offset - len(prev_tail) + m.start()
                    va = ddr_base + abs_offset
                    match_bytes = m.group()
                    results.append((va, match_bytes, abs_offset))
                    found += 1
                    if found >= max_results:
                        break
            else:
                idx = 0
                while found < max_results:
                    pos = search_data.find(search_bytes, idx)
                    if pos == -1:
                        break
                    abs_offset = offset - len(prev_tail) + pos
                    va = ddr_base + abs_offset
                    results.append((va, search_bytes, abs_offset))
                    found += 1
                    idx = pos + 1

            prev_tail = data[-overlap:] if len(data) >= overlap else data
            offset += len(data)

    if not results:
        print("\n  No matches found.")
        return []

    print("\n  Found %d match(es):\n" % len(results))

    for va, match_data, abs_off in results:
        # 读取上下文
        ctx_start = max(0, abs_off - context_bytes)
        ctx_len = context_bytes + len(match_data) + context_bytes
        try:
            with open(ddr_path, 'rb') as f:
                f.seek(ctx_start)
                ctx_data = f.read(ctx_len)
        except Exception:
            ctx_data = match_data

        # 格式化输出
        ascii_ctx = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in ctx_data)
        hex_ctx = ' '.join('%02X' % b for b in ctx_data[:min(64, len(ctx_data))])
        if len(ctx_data) > 64:
            hex_ctx += ' ...'

        print("  VA=0x%08X  (file offset 0x%X)" % (va, abs_off))
        print("    Hex: %s" % hex_ctx)
        print("    ASCII: %s" % ascii_ctx[:80])
        print()

    return results


# ============================================================================
# 综合分析
# ============================================================================

def full_analyze(dump_dir, map_path):
    """一键诊断分析 — 按 ee_type_raw 自动选择分析路径。

    执行自动化分析步骤（寄存器解析、DDR 栈分析、线程扫描、堆检查、
    AXF vs DDR 代码完整性）。某些步骤（反汇编、静态栈深度、PSRAM 损坏映射、
    报告生成）需要按 skill.md 流程手动执行对应脚本。
    """
    # 收集诊断结果用于最终摘要
    diag = {
        'code_integrity': None,   # 'intact' / 'corrupted' / 'skipped'
        'stack_overflow': None,   # True / False / None
        'heap_corrupt': None,     # True / False / None
        'thread_overflow': 0,
        'ddr_base': None,
        'root_cause_hint': [],
    }

    print("=" * 60)
    print("  Full Dump Analysis (Auto-Diagnosis)")
    print("=" * 60)

    # Step 1: 扫描 dump 目录
    print("\n[Step 1] Scanning dump directory...")
    files = {}
    for fname in os.listdir(dump_dir):
        fpath = os.path.join(dump_dir, fname)
        if os.path.isfile(fpath):
            fsize = os.path.getsize(fpath)
            files[fname] = fpath
            print("  %-30s %d bytes" % (fname, fsize))

    # 查找关键文件
    hbuf_file = None
    cmm_file = None
    xdb_file = None
    ddr_file = None
    wdt_file = None
    rti_file = None
    custver_file = None
    axf_file = None
    map_file = map_path

    for fname, fpath in files.items():
        fl = fname.lower()
        if 'hbuf' in fl and fname.endswith('.bin') and not hbuf_file:
            hbuf_file = fpath
        elif fname.endswith('.cmm'):
            cmm_file = fpath
        elif fname.endswith('.xdb'):
            xdb_file = fpath
        elif ('ddr' in fl or 'psram' in fl) and fname.endswith('.bin') and not ddr_file:
            ddr_file = fpath
        elif 'wdt' in fl and fname.endswith('.bin') and not wdt_file:
            wdt_file = fpath
        elif ('rti' in fl or 'tsk' in fl) and fname.endswith('.bin') and not rti_file:
            rti_file = fpath
        elif ('ver' in fl or 'cust' in fl) and fname.endswith('.bin') and not custver_file:
            custver_file = fpath
        elif fname.endswith('.axf') or fname.endswith('.elf'):
            axf_file = fpath

    # Step 1.5: 版本校验
    if hbuf_file:
        print("\n[Step 1.5] Version check...")
        try:
            check_version(hbuf_file, axf_path=axf_file,
                          map_path=map_file, custver_path=custver_file)
        except Exception as e:
            print("  ERROR: %s" % e)

    # Step 2: 解析异常信息（优先 hbuf，回退 cmm/xdb）
    if hbuf_file:
        print("\n[Step 2] Parsing exception header: %s" % os.path.basename(hbuf_file))
        parsed = parse_ee_hbuf(hbuf_file)
    elif cmm_file or xdb_file:
        script_file = cmm_file or xdb_file
        print("\n[Step 2] Parsing TRACE32 script: %s" % os.path.basename(script_file))
        parsed = parse_cmm(script_file)
    else:
        print("\nERROR: No com_EE_Hbuf.bin or .cmm/.xdb file found!")
        return

    print_cmm_summary(parsed)

    fault = parsed['fault_info']
    regs = parsed['registers']
    ee_type = fault.get('ee_type_raw', 0)

    # --- 按 EE 类型分支 ---
    EE_SYS_RESET = 300
    EE_ASSERT = 350
    EE_EXCEPTION = 450
    EE_WARNING = 550

    # ============== WDT 超时分析路径 ==============
    is_wdt = (ee_type == EE_SYS_RESET and fault.get('PMU_reg') == 3)
    # ASSERT 中也检查是否是 WDT_EXPIRED_withoutAssert
    desc = parsed['prints'][0] if parsed['prints'] else ''
    if ee_type == EE_ASSERT and 'WDT_EXPIRED' in desc:
        is_wdt = True

    if is_wdt:
        print("\n" + "=" * 60)
        print("  WDT TIMEOUT ANALYSIS PATH")
        print("=" * 60)

        if wdt_file:
            print("\n[Step W1] WDT kick trace...")
            try:
                parse_wdt_kick(wdt_file)
            except Exception as e:
                print("  ERROR: %s" % e)

        if rti_file:
            print("\n[Step W2] Task switch history (last 30)...")
            try:
                parse_rti_tsk(rti_file, last_n=30)
            except Exception as e:
                print("  ERROR: %s" % e)

        # DDR 分析（WDT/SYS_RESET 可能没有 stack_dump，但仍需检查线程和堆）
        ddr_base = None
        if ddr_file:
            if parsed['stack_dump']:
                print("\n[Step W3] DDR base detection (from stack dump)...")
                try:
                    ddr_base = find_ddr_base(ddr_file, parsed)
                except Exception as e:
                    print("  ERROR: %s" % e)
            else:
                # WDT/SYS_RESET 常见：没有 stack dump，尝试通过已知内存模式定位
                print("\n[Step W3] DDR base detection (no stack dump, scanning for TX_THREAD)...")
                try:
                    ddr_base = _detect_base_by_thread_scan(ddr_file)
                except Exception as e:
                    print("  ERROR: %s" % e)

        if ddr_file and ddr_base is not None:
            diag['ddr_base'] = ddr_base
            print("\n[Step W4] Scanning all threads...")
            try:
                threads, of_cnt, high_cnt = scan_all_threads(ddr_file, ddr_base)
                diag['thread_overflow'] = of_cnt
                if of_cnt > 0:
                    print("\n  >> %d thread(s) with stack overflow detected!" % of_cnt)
                    diag['root_cause_hint'].append('Thread stack overflow -> WDT kick blocked')
            except Exception as e:
                print("  ERROR: %s" % e)

            print("\n[Step W5] Heap integrity scan...")
            try:
                scan_heap(ddr_file, ddr_base)
            except Exception as e:
                print("  ERROR: %s" % e)
        elif ddr_file and ddr_base is None:
            print("  SKIPPED: Could not determine DDR base address")
            print("  Tip: Use 'dump_analyzer.py ddr-base --ddr <DDR> --hbuf <HBUF>' with manual base")

        # WDT 结论
        print("\n" + "=" * 60)
        print("  WDT ANALYSIS CONCLUSION")
        print("=" * 60)
        print("  Reset cause: WDT_TIMEOUT")
        if 'PMU_reg' in fault:
            print("  PMU_reg: %d (%s)" % (fault['PMU_reg'], fault.get('reset_cause', '?')))
        _print_manual_steps_hint(diag, 'WDT')

        return parsed

    # ============== ASSERT 分析路径 ==============
    if ee_type == EE_ASSERT:
        print("\n" + "=" * 60)
        print("  ASSERT ANALYSIS PATH")
        print("=" * 60)

        if 'assert_file' in fault:
            print("  Assert location: %s line %d" % (
                fault['assert_file'], fault['assert_line']))
        if desc:
            print("  Assert desc: %s" % desc)

        pc = regs.get('pc', 0)
        lr = regs.get('lr', 0)
        if map_file and (pc or lr):
            print("\n[Step A1] Resolving PC/LR...")
            try:
                symbols = load_map_symbols(map_file)
                if pc:
                    print("  PC 0x%08X -> %s" % (pc, resolve_symbol(symbols, pc) or "NOT FOUND"))
                if lr:
                    print("  LR 0x%08X -> %s" % (lr, resolve_symbol(symbols, lr) or "NOT FOUND"))
            except Exception as e:
                print("  ERROR: %s" % e)

        # DDR 分析
        sp = regs.get('sp', 0)
        stack_bottom = fault.get('stack_bottom', 0)
        stack_top = fault.get('stack_top', 0)
        if ddr_file and stack_bottom and stack_top:
            try:
                ddr_base = find_ddr_base(ddr_file, parsed)
                if ddr_base is not None:
                    diag['ddr_base'] = ddr_base
                    analyze_stack(ddr_file, ddr_base, stack_bottom, stack_top, sp, map_file)
                    scan_all_threads(ddr_file, ddr_base)
                    scan_heap(ddr_file, ddr_base)
            except Exception as e:
                print("  ERROR in DDR analysis: %s" % e)

        _print_manual_steps_hint(diag, 'ASSERT')
        return parsed

    # ============== WARNING 分析路径 ==============
    if ee_type == EE_WARNING:
        print("\n" + "=" * 60)
        print("  WARNING ANALYSIS PATH")
        print("=" * 60)
        if desc:
            print("  Warning: %s" % desc)
        return parsed

    # ============== EXCEPTION 分析路径（默认） ==============
    # 提取关键信息
    pc = regs.get('pc', fault.get('exception_pc', 0))
    lr = regs.get('lr', regs.get('r14', 0))
    sp = regs.get('sp', regs.get('r13', 0))
    stack_bottom = fault.get('stack_bottom', 0)
    stack_top = fault.get('stack_top', 0)

    # ISR 上下文检测
    is_isr = fault.get('is_isr', False)
    if is_isr:
        print("\n  *** ISR CONTEXT DETECTED (CPSR mode=0x%02X, task_name=%s) ***" %
              (regs.get('cpsr', 0) & 0x1F, fault.get('task_name', '?')))
        print("  The crash occurred in interrupt context.")
        print("  Stack bounds may be invalid (ISR stack, not task stack).")

    # Step 3: 解析 PC/LR
    if map_file:
        print("\n[Step 3] Resolving PC/LR addresses...")
        try:
            symbols = load_map_symbols(map_file)
            sym_count = len(symbols[0]) if isinstance(symbols, tuple) else len(symbols)
            print("  Loaded %d symbols from map file" % sym_count)

            pc_sym = resolve_symbol(symbols, pc)
            lr_sym = resolve_symbol(symbols, lr)
            print("  PC 0x%08X -> %s" % (pc, pc_sym or "NOT FOUND"))
            print("  LR 0x%08X -> %s" % (lr, lr_sym or "NOT FOUND"))
        except Exception as e:
            print("  ERROR: %s" % e)
    else:
        print("\n[Step 3] WARNING: No map file provided, skipping symbol resolution")

    # Step 4: DDR 栈分析
    stack_result = None
    if ddr_file and stack_bottom and stack_top:
        print("\n[Step 4] DDR base address detection...")
        ddr_base = None
        try:
            ddr_base = find_ddr_base(ddr_file, parsed)
        except Exception as e:
            print("  ERROR: %s" % e)

        if ddr_base is not None:
            diag['ddr_base'] = ddr_base

            print("\n[Step 5] Stack analysis from DDR dump...")
            try:
                stack_result = analyze_stack(ddr_file, ddr_base, stack_bottom, stack_top, sp, map_file)
                if stack_result:
                    diag['stack_overflow'] = stack_result['overflow']
            except Exception as e:
                print("  ERROR: %s" % e)

            # Step 5.5: 堆完整性检查
            print("\n[Step 5.5] Heap integrity scan...")
            try:
                scan_heap(ddr_file, ddr_base)
            except Exception as e:
                print("  ERROR: %s" % e)

            # Step 6: 全线程栈溢出扫描
            print("\n[Step 6] Scanning ALL threads for stack overflow...")
            try:
                threads, of_cnt, high_cnt = scan_all_threads(ddr_file, ddr_base)
                diag['thread_overflow'] = of_cnt
                if of_cnt > 0:
                    print("\n  >> WARNING: %d OTHER threads have stack overflow!" % of_cnt)
                else:
                    print("\n  >> All %d threads OK, no stack overflow anywhere" % len(threads))
            except Exception as e:
                print("  ERROR: %s" % e)
        else:
            print("\n[Step 5] FAILED: Could not determine DDR base address")
    else:
        print("\n[Step 4/5] SKIPPED: DDR dump or stack range not available")

    # Step 7: AXF vs DDR 代码完整性检查（关键步骤）
    code_intact = None
    if axf_file and ddr_file and diag.get('ddr_base') and pc:
        print("\n[Step 7] AXF vs DDR code integrity check...")
        try:
            from ddr_code_compare import (
                read_axf_bytes, read_ddr_bytes, compare_bytes,
                classify_corruption, parse_elf_sections as _parse_ddr_sections,
                find_section_for_addr,
            )
            cmp_sections = _parse_ddr_sections(axf_file)
            if cmp_sections:
                pc_clean = pc & ~1
                axf_data, axf_sec = read_axf_bytes(axf_file, cmp_sections, pc_clean, 64)
                if axf_data:
                    ddr_data = read_ddr_bytes(ddr_file, pc_clean, diag['ddr_base'], len(axf_data))
                    if ddr_data:
                        report = compare_bytes(axf_data, ddr_data, pc_clean)
                        if report['diff_count'] == 0:
                            print("  CODE INTACT: AXF matches DDR — No corruption detected")
                            code_intact = True
                            diag['code_integrity'] = 'intact'
                        else:
                            ctype, cdesc = classify_corruption(report, len(axf_data))
                            print("  *** CODE MISMATCH: %s ***" % ctype)
                            print("  %s" % cdesc)
                            print("  %d/%d bytes differ (%.1f%%)" % (
                                report['diff_count'], report['total'],
                                report['diff_count'] * 100.0 / report['total']))

                            # --- 段归属分析 ---
                            pc_section = find_section_for_addr(cmp_sections, pc_clean)
                            lr_clean = lr & ~1 if lr else 0
                            lr_section = find_section_for_addr(cmp_sections, lr_clean) if lr_clean else None

                            cross_section = False
                            entire_section_corrupt = False

                            if pc_section:
                                pc_sec_name = pc_section['name']
                                pc_sec_start = pc_section['addr']
                                pc_sec_end = pc_section['addr'] + pc_section['size']
                                print("  PC section: %s (0x%08X..0x%08X, %d KB)" % (
                                    pc_sec_name, pc_sec_start, pc_sec_end,
                                    pc_section['size'] // 1024))

                                # 跨段调用检测
                                if lr_section:
                                    lr_sec_name = lr_section['name']
                                    if pc_sec_name != lr_sec_name:
                                        cross_section = True
                                        print("  LR section:  %s" % lr_sec_name)
                                        print("  *** CROSS-SECTION CALL: LR in [%s], PC in [%s] ***" % (
                                            lr_sec_name, pc_sec_name))

                                # 段首/段尾采样
                                sample_points = [
                                    ("section start", pc_sec_start),
                                    ("section end", max(pc_sec_start, pc_sec_end - 64)),
                                ]
                                all_samples_corrupt = True
                                for sample_name, sample_addr in sample_points:
                                    try:
                                        s_axf, _ = read_axf_bytes(axf_file, cmp_sections,
                                                                   sample_addr, 64)
                                        if s_axf:
                                            s_ddr = read_ddr_bytes(ddr_file, sample_addr,
                                                                    diag['ddr_base'], len(s_axf))
                                            if s_ddr:
                                                s_report = compare_bytes(s_axf, s_ddr, sample_addr)
                                                s_pct = s_report['diff_count'] * 100.0 / s_report['total'] if s_report['total'] else 0
                                                print("  %s (0x%08X): %d/%d differ (%.0f%%)" % (
                                                    sample_name, sample_addr,
                                                    s_report['diff_count'], s_report['total'], s_pct))
                                                if s_report['diff_count'] < s_report['total']:
                                                    all_samples_corrupt = False
                                            else:
                                                all_samples_corrupt = False
                                        else:
                                            all_samples_corrupt = False
                                    except Exception:
                                        all_samples_corrupt = False

                                if all_samples_corrupt:
                                    entire_section_corrupt = True
                                    print("  ENTIRE SECTION MISMATCHED (start+end all differ)")

                                # 综合判定
                                if ctype == 'BIST_CHECKERBOARD' and entire_section_corrupt:
                                    print("\n  >> VERDICT: Code section NOT LOADED (not runtime corruption)")
                                    print("  >> BIST checkerboard pattern across entire section")
                                    print("  >> Suggest searching DDR for boot/load config:")
                                    print("  >>   python dump_analyzer.py ddr-search "
                                          "--ddr <DDR> --base 0x%08X --string \"<keyword>\"" % diag['ddr_base'])
                                    code_intact = False
                                    diag['code_integrity'] = 'not_loaded'
                                    diag['root_cause_hint'].insert(
                                        0, 'Code section not loaded (BIST checkerboard, entire section)')
                                elif cross_section and entire_section_corrupt:
                                    print("\n  >> VERDICT: Called into a section that was NOT LOADED")
                                    if lr_section:
                                        print("  >> LR in loaded section [%s], PC in unloaded section [%s]" % (
                                            lr_section['name'], pc_sec_name))
                                    code_intact = False
                                    diag['code_integrity'] = 'not_loaded'
                                    diag['root_cause_hint'].insert(
                                        0, 'Cross-section call to unloaded code (%s)' % pc_sec_name)
                                else:
                                    code_intact = False
                                    diag['code_integrity'] = 'corrupted'
                                    diag['root_cause_hint'].append('PSRAM code corruption (%s)' % ctype)
                            else:
                                code_intact = False
                                diag['code_integrity'] = 'corrupted'
                                diag['root_cause_hint'].append('PSRAM code corruption (%s)' % ctype)
                    else:
                        print("  SKIPPED: Cannot read DDR at PC address")
                else:
                    print("  SKIPPED: PC 0x%08X not in any AXF section" % (pc & ~1))
            else:
                print("  SKIPPED: Cannot parse AXF sections")
        except ImportError:
            print("  SKIPPED: ddr_code_compare.py not available")
        except Exception as e:
            print("  ERROR: %s" % e)
    elif not axf_file:
        diag['code_integrity'] = 'skipped'
    elif not diag.get('ddr_base'):
        diag['code_integrity'] = 'skipped'

    # 最终结论
    print("\n" + "=" * 60)
    print("  ANALYSIS CONCLUSION")
    print("=" * 60)

    fault_addr = fault.get('FAULT_ADDRESS', 0)
    fault_status = fault.get('FAULT_STATUS', 0)
    exc_type = fault.get('exception_type', 'Unknown')

    print("\n  Exception type:  %s" % exc_type)
    print("  Fault Address:   0x%08X" % fault_addr)
    print("  Fault Status:    0x%08X" % fault_status)

    # FSC 快速解码
    fsc = ((fault_status >> 6) & 0x10) + (fault_status & 0x0F)
    wnr = (fault_status >> 11) & 1
    fsc_names = {0x00: 'Background', 0x01: 'Alignment', 0x06: 'Translation',
                 0x08: 'SyncExtAbort', 0x0D: 'Permission', 0x0E: 'Alignment(L1)',
                 0x16: 'AsyncExtAbort', 0x19: 'SyncParity'}
    print("  FSC decoded:     0x%02X (%s) WnR=%d" % (
        fsc, fsc_names.get(fsc, 'Unknown'), wnr))

    # 根因推理
    if diag.get('code_integrity') == 'not_loaded':
        print("\n  >> ROOT CAUSE: CODE SECTION NOT LOADED")
        print("  >> CPU executed BIST/test residual data instead of code")
        print("  >> The code section was never loaded into PSRAM — not runtime corruption")
        if diag['root_cause_hint']:
            print("  >> Hint: %s" % diag['root_cause_hint'][0])
    elif code_intact is False:
        print("\n  >> ROOT CAUSE: PSRAM CODE CORRUPTION")
        print("  >> AXF code differs from DDR runtime — CPU executed corrupted instructions")
        diag['root_cause_hint'].insert(0, 'PSRAM code corruption')
    elif fault_addr > 0 and fault_addr < 0x10:
        print("\n  >> ROOT CAUSE HINT: NULL POINTER DEREFERENCE (offset %d)" % fault_addr)
        print("  >> Trace malloc/calloc return values in the call chain")
        diag['root_cause_hint'].append('NULL pointer dereference (offset %d)' % fault_addr)
    elif fault_addr == 0:
        print("\n  >> ROOT CAUSE HINT: NULL POINTER DEREFERENCE (direct)")
        diag['root_cause_hint'].append('NULL pointer dereference')

    if diag.get('stack_overflow'):
        print("\n  >> STACK OVERFLOW CONFIRMED (crash task)")
        diag['root_cause_hint'].append('Stack overflow (crash task)')
    elif stack_result:
        print("\n  >> Stack overflow: RULED OUT (%.1f%% usage)" % (
            100.0 * stack_result['peak_usage'] / stack_result['stack_size']))

    if diag['thread_overflow'] > 0:
        print("\n  >> %d OTHER thread(s) with stack overflow!" % diag['thread_overflow'])
        diag['root_cause_hint'].append('Stack overflow (%d other threads)' % diag['thread_overflow'])

    _print_manual_steps_hint(diag, 'EXCEPTION')

    return parsed


def _print_manual_steps_hint(diag, analysis_type):
    """打印后续需要手动执行的步骤提示。"""
    print("\n" + "-" * 60)
    print("  AUTO-DIAGNOSIS COMPLETE — Manual steps may be needed:")
    print("-" * 60)

    hints = []
    if diag['code_integrity'] is None and analysis_type == 'EXCEPTION':
        hints.append("  * AXF vs DDR code integrity check (if AXF + DDR available)")
    if diag['code_integrity'] == 'intact':
        hints.append("  * AXF disassembly of crash instruction (axf_disasm.py --address PC)")
    if diag['code_integrity'] == 'corrupted':
        hints.append("  * PSRAM corruption scope mapping (see references/psram-corruption-mapping.md)")
    if diag.get('stack_overflow') or diag['thread_overflow'] > 0:
        hints.append("  * Static stack depth analysis (stack_analysis.py --func FUNC)")
    hints.append("  * Generate report using references/bug-report-template.md")

    for h in hints:
        print(h)


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    parser = argparse.ArgumentParser(description='ARM Cortex-R Crash Dump Analyzer')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # parse-cmm
    p = subparsers.add_parser('parse-cmm', help='Parse TRACE32 .cmm/.xdb script')
    p.add_argument('cmm_file', help='Path to .cmm or .xdb file')

    # ddr-base
    p = subparsers.add_parser('ddr-base', help='Determine DDR base address')
    p.add_argument('--ddr', required=True, help='Path to DDR dump binary')
    p.add_argument('--hbuf', help='Path to com_EE_Hbuf.bin (preferred)')
    p.add_argument('--cmm', help='Path to .cmm/.xdb file (legacy fallback)')

    # stack-analysis
    p = subparsers.add_parser('stack-analysis', help='Analyze stack from DDR dump')
    p.add_argument('--ddr', required=True, help='Path to DDR dump binary')
    p.add_argument('--base', required=True, help='DDR base address (hex)')
    p.add_argument('--stack-bottom', required=True, help='Stack bottom address (hex)')
    p.add_argument('--stack-top', required=True, help='Stack top address (hex)')
    p.add_argument('--sp', required=True, help='Stack pointer at crash (hex)')
    p.add_argument('--map', help='Path to map file for symbol resolution')

    # resolve
    p = subparsers.add_parser('resolve', help='Resolve addresses using map file')
    p.add_argument('--map', required=True, help='Path to map file')
    p.add_argument('addresses', nargs='+', help='Addresses to resolve (hex)')

    # scan-threads
    p = subparsers.add_parser('scan-threads', help='Scan all TX_THREAD for stack overflow')
    p.add_argument('--ddr', required=True, help='Path to DDR dump binary')
    p.add_argument('--base', required=True, help='DDR base address (hex)')

    # parse-hbuf
    p = subparsers.add_parser('parse-hbuf',
                              help='Parse com_EE_Hbuf.bin binary exception header')
    p.add_argument('hbuf_file', help='Path to com_EE_Hbuf.bin')
    p.add_argument('--map', help='MAP file for symbol resolution')

    # parse-wdt
    p = subparsers.add_parser('parse-wdt',
                              help='Parse com_wdtKICK.bin watchdog trace')
    p.add_argument('wdt_file', help='Path to com_wdtKICK.bin')

    # parse-rti
    p = subparsers.add_parser('parse-rti',
                              help='Parse com_rti_tsk.bin task switch trace')
    p.add_argument('rti_file', help='Path to com_rti_tsk.bin')
    p.add_argument('--last', type=int, default=0,
                   help='Show only last N entries (0=all)')

    # scan-heap
    p = subparsers.add_parser('scan-heap',
                              help='Scan TX_BYTE_POOL integrity in DDR dump')
    p.add_argument('--ddr', required=True, help='Path to DDR dump binary')
    p.add_argument('--base', required=True, help='DDR base address (hex)')

    # check-version
    p = subparsers.add_parser('check-version',
                              help='Verify AXF/dump version compatibility')
    p.add_argument('--hbuf', required=True, help='Path to com_EE_Hbuf.bin')
    p.add_argument('--axf', help='Path to AXF/ELF file')
    p.add_argument('--map', help='Path to MAP file')
    p.add_argument('--custver', help='Path to com_CustVer.bin')

    # ddr-search
    p = subparsers.add_parser('ddr-search',
                              help='Search DDR dump for string/byte pattern')
    p.add_argument('--ddr', required=True, help='Path to DDR dump binary')
    p.add_argument('--base', required=True, help='DDR base address (hex)')
    p.add_argument('--string', help='ASCII string to search for')
    p.add_argument('--hex', help='Hex byte sequence to search (e.g. "AA55")')
    p.add_argument('--regex', help='Regex pattern to search in DDR dump')
    p.add_argument('--context', type=int, default=32,
                   help='Context bytes around match (default: 32)')
    p.add_argument('--max', type=int, default=20,
                   help='Maximum results to show (default: 20)')

    # full-analyze
    p = subparsers.add_parser('full-analyze', help='Full dump analysis')
    p.add_argument('--dump-dir', required=True, help='Path to dump directory')
    p.add_argument('--map', help='Path to map file')

    args = parser.parse_args()

    if args.command == 'parse-cmm':
        parsed = parse_cmm(args.cmm_file)
        print_cmm_summary(parsed)

    elif args.command == 'parse-hbuf':
        parsed = parse_ee_hbuf(args.hbuf_file)
        print_cmm_summary(parsed)
        # 可选: MAP 符号解析
        if args.map and parsed['registers'].get('pc'):
            try:
                symbols_data = load_map_symbols(args.map)
                pc = parsed['registers'].get('pc', 0)
                lr = parsed['registers'].get('lr', 0)
                if pc:
                    sym = resolve_symbol(symbols_data, pc)
                    if sym:
                        print("\nPC Symbol: %s" % sym)
                if lr:
                    sym = resolve_symbol(symbols_data, lr)
                    if sym:
                        print("LR Symbol: %s" % sym)
            except Exception as e:
                print("\nMAP resolution error: %s" % e)

    elif args.command == 'parse-wdt':
        parse_wdt_kick(args.wdt_file)

    elif args.command == 'parse-rti':
        parse_rti_tsk(args.rti_file, last_n=args.last)

    elif args.command == 'scan-heap':
        scan_heap(args.ddr, int(args.base, 16))

    elif args.command == 'check-version':
        check_version(args.hbuf, axf_path=args.axf,
                      map_path=args.map, custver_path=args.custver)

    elif args.command == 'ddr-search':
        if not (args.string or args.hex or args.regex):
            print("ERROR: --string, --hex, or --regex is required")
            sys.exit(1)
        if args.string:
            mode, pattern = 'string', args.string
        elif args.hex:
            mode, pattern = 'hex', args.hex
        else:
            mode, pattern = 'regex', args.regex
        ddr_search(args.ddr, int(args.base, 16), pattern,
                   mode=mode, context_bytes=args.context,
                   max_results=args.max)

    elif args.command == 'ddr-base':
        if args.hbuf:
            parsed = parse_ee_hbuf(args.hbuf)
        elif args.cmm:
            parsed = parse_cmm(args.cmm)
        else:
            print("ERROR: --hbuf or --cmm is required")
            sys.exit(1)
        base = find_ddr_base(args.ddr, parsed)
        if base is not None:
            print("\nResult: DDR_BASE = 0x%08X" % base)

    elif args.command == 'stack-analysis':
        analyze_stack(
            args.ddr,
            int(args.base, 16),
            int(args.stack_bottom, 16),
            int(args.stack_top, 16),
            int(args.sp, 16),
            args.map
        )

    elif args.command == 'resolve':
        resolve_addresses(args.map, args.addresses)

    elif args.command == 'scan-threads':
        scan_all_threads(args.ddr, int(args.base, 16))

    elif args.command == 'full-analyze':
        full_analyze(args.dump_dir, args.map)

    else:
        parser.print_help()


if __name__ == '__main__':
    main()
