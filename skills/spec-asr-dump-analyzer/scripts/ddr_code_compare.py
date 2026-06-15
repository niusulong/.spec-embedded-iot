#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AXF vs DDR Code Integrity Compare
===================================
对比 AXF 编译代码与 DDR dump 中 PSRAM 实际运行内容，检测代码是否被损坏。

用法:
  # 对比指定地址处 AXF 与 DDR 的代码
  python ddr_code_compare.py <axf_file> <ddr_file> --pc 0x7E88003C --base 0x7E20FFFC

  # 指定对比范围
  python ddr_code_compare.py <axf_file> <ddr_file> --pc 0x7E88003C --base 0x7E20FFFC --size 64

  # 扫描整个代码段（大范围检测）
  python ddr_code_compare.py <axf_file> <ddr_file> --pc 0x7E88003C --base 0x7E20FFFC --scan 0x100000

  # 仅提取 AXF 字节（不对比）
  python ddr_code_compare.py <axf_file> --pc 0x7E88003C --size 32 --disasm

  # 列出 AXF 中所有代码段
  python ddr_code_compare.py <axf_file> --list-sections
"""

import struct
import sys
import os
import argparse

# ELF section header type and flag constants
SHT_PROGBITS = 1
SHF_EXECINSTR = 4

# Import shared utilities — use common.py's unified ELF parser instead of local duplicate
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (
    parse_map_file as _parse_map_file,
    lookup_address as _lookup_address,
    parse_elf_sections as _parse_elf_sections,
    find_elf_section as _find_elf_section,
    read_elf_bytes as _read_elf_bytes,
)


def parse_elf_sections(filepath):
    """Parse ELF section headers. Delegates to common.py for unified 32/64-bit support.

    Returns list of dicts (name, type, flags, addr, offset, size, index) for
    backward compatibility with section listing output.
    """
    raw = _parse_elf_sections(filepath)
    if not raw:
        return []

    # Re-read full section headers to get type/flags for listing display.
    # For address lookup and byte reads, use common.py functions directly.
    sections = []
    try:
        with open(filepath, 'rb') as f:
            magic = f.read(4)
            if magic != b'\x7fELF':
                return []
            ei_class = struct.unpack('B', f.read(1))[0]
            f.seek(0)
            if ei_class == 2:
                ehdr = f.read(64)
                e_shoff = struct.unpack_from('<Q', ehdr, 40)[0]
                e_shentsize = struct.unpack_from('<H', ehdr, 58)[0]
                e_shnum = struct.unpack_from('<H', ehdr, 60)[0]
                e_shstrndx = struct.unpack_from('<H', ehdr, 62)[0]
                endian = '<'
            else:
                ehdr = f.read(52)
                e_shoff = struct.unpack_from('<I', ehdr, 32)[0]
                e_shentsize = struct.unpack_from('<H', ehdr, 46)[0]
                e_shnum = struct.unpack_from('<H', ehdr, 48)[0]
                e_shstrndx = struct.unpack_from('<H', ehdr, 50)[0]
                endian = '<'

            if e_shoff == 0 or e_shnum == 0:
                return [dict(index=i, name=n, type=1, flags=6, addr=a, offset=o, size=s)
                        for i, (n, a, o, s) in enumerate(raw)]

            f.seek(e_shoff + e_shstrndx * e_shentsize)
            shstr_hdr = f.read(e_shentsize)
            if ei_class == 2:
                shstr_off = struct.unpack_from(endian + 'Q', shstr_hdr, 24)[0]
                shstr_sz = struct.unpack_from(endian + 'Q', shstr_hdr, 32)[0]
            else:
                shstr_off = struct.unpack_from(endian + 'I', shstr_hdr, 16)[0]
                shstr_sz = struct.unpack_from(endian + 'I', shstr_hdr, 20)[0]
            f.seek(shstr_off)
            shstr_data = f.read(shstr_sz)

            for i in range(e_shnum):
                f.seek(e_shoff + i * e_shentsize)
                shdr = f.read(e_shentsize)
                if ei_class == 2:
                    sh_name = struct.unpack_from(endian + 'I', shdr, 0)[0]
                    sh_type = struct.unpack_from(endian + 'I', shdr, 4)[0]
                    sh_flags = struct.unpack_from(endian + 'Q', shdr, 8)[0]
                    sh_addr = struct.unpack_from(endian + 'Q', shdr, 16)[0]
                    sh_offset = struct.unpack_from(endian + 'Q', shdr, 24)[0]
                    sh_size = struct.unpack_from(endian + 'Q', shdr, 32)[0]
                else:
                    sh_name = struct.unpack_from(endian + 'I', shdr, 0)[0]
                    sh_type = struct.unpack_from(endian + 'I', shdr, 4)[0]
                    sh_flags = struct.unpack_from(endian + 'I', shdr, 8)[0]
                    sh_addr = struct.unpack_from(endian + 'I', shdr, 12)[0]
                    sh_offset = struct.unpack_from(endian + 'I', shdr, 16)[0]
                    sh_size = struct.unpack_from(endian + 'I', shdr, 20)[0]

                name_end = shstr_data.find(b'\x00', sh_name)
                name = shstr_data[sh_name:name_end].decode('ascii', errors='replace')
                sections.append(dict(
                    index=i, name=name, type=sh_type, flags=sh_flags,
                    addr=sh_addr, offset=sh_offset, size=sh_size,
                ))
    except Exception:
        # Fallback: use common.py's parsed data without type/flags
        sections = [dict(index=i, name=n, type=1, flags=6, addr=a, offset=o, size=s)
                    for i, (n, a, o, s) in enumerate(raw)]

    return sections


def find_section_for_addr(sections, vaddr):
    """找到包含指定虚拟地址的 section"""
    for sec in sections:
        if sec['type'] == SHT_PROGBITS and sec['size'] > 0:
            if sec['addr'] <= vaddr < sec['addr'] + sec['size']:
                return sec
    return None


def read_axf_bytes(filepath, sections, vaddr, size):
    """从 AXF 文件读取指定虚拟地址处的字节"""
    sec = find_section_for_addr(sections, vaddr)
    if sec is None:
        return None, None

    offset_in_sec = vaddr - sec['addr']
    file_offset = sec['offset'] + offset_in_sec
    readable_size = min(size, sec['size'] - offset_in_sec)

    with open(filepath, 'rb') as f:
        f.seek(file_offset)
        data = f.read(readable_size)

    return data, sec


def read_ddr_bytes(filepath, vaddr, base_addr, size):
    """从 DDR dump 读取指定虚拟地址处的字节"""
    file_offset = vaddr - base_addr
    if file_offset < 0:
        return None

    file_size = os.path.getsize(filepath)
    readable_size = min(size, file_size - file_offset)
    if readable_size <= 0:
        return None

    with open(filepath, 'rb') as f:
        f.seek(file_offset)
        data = f.read(readable_size)

    return data


def compare_bytes(axf_data, ddr_data, start_addr):
    """对比两组字节，返回差异报告"""
    if axf_data is None:
        return {'error': 'AXF data not available at this address'}
    if ddr_data is None:
        return {'error': 'DDR data not available at this address'}

    compare_len = min(len(axf_data), len(ddr_data))
    diffs = []
    diff_count = 0

    for i in range(compare_len):
        if axf_data[i] != ddr_data[i]:
            diffs.append((start_addr + i, axf_data[i], ddr_data[i]))
            diff_count += 1

    # 分析损坏字节模式
    ddr_byte_freq = {}
    for _, _, ddr_byte in diffs:
        ddr_byte_freq[ddr_byte] = ddr_byte_freq.get(ddr_byte, 0) + 1

    # 损坏区域连续性分析
    regions = []
    if diffs:
        region_start = diffs[0][0]
        prev_addr = diffs[0][0]
        for diff in diffs[1:]:
            if diff[0] == prev_addr + 1:
                prev_addr = diff[0]
            else:
                regions.append((region_start, prev_addr + 1 - region_start))
                region_start = diff[0]
                prev_addr = diff[0]
        regions.append((region_start, prev_addr + 1 - region_start))

    return {
        'total': compare_len,
        'diff_count': diff_count,
        'match_count': compare_len - diff_count,
        'match_pct': (compare_len - diff_count) * 100.0 / compare_len if compare_len > 0 else 100.0,
        'diffs': diffs,
        'regions': regions,
        'ddr_byte_freq': ddr_byte_freq,
    }


def classify_corruption(report, total_size):
    """根据损坏特征分类损坏类型"""
    if report['diff_count'] == 0:
        return 'NONE', '代码完整，无损坏'

    freq = report['ddr_byte_freq']
    diff_pct = report['diff_count'] * 100.0 / report['total'] if report['total'] > 0 else 0

    # 检查主导字节
    dominant_byte = max(freq, key=freq.get) if freq else 0
    dominant_pct = freq[dominant_byte] * 100.0 / report['diff_count'] if report['diff_count'] > 0 else 0

    # 优先检测 checkerboard 模式（0xAA/0x55 BIST 测试残留）
    # 这是"代码段从未被加载"的标志，比 PSRAM_BUS_FAILURE 更精确
    # BIST checkerboard 按 PSRAM bank 分组分布，0xAA 和 0x55 可能不在同一区域
    # 因此检测单字节主导：0xAA > 25% 或 0x55 > 25%（编译代码不会出现这种分布）
    bist_aa = freq.get(0xAA, 0)
    bist_55 = freq.get(0x55, 0)
    bist_total = bist_aa + bist_55
    bist_dominant = max(bist_aa, bist_55)
    bist_dominant_pct = bist_dominant * 100.0 / report['diff_count'] if report['diff_count'] > 0 else 0
    if diff_pct > 90 and report['diff_count'] > 0 and bist_dominant_pct > 25:
        return 'BIST_CHECKERBOARD', \
            'BIST/memory test residual (0xAA=%.0f%% 0x55=%.0f%%) — ' \
            'section likely never loaded, not runtime corruption' % (
                bist_aa * 100.0 / report['diff_count'],
                bist_55 * 100.0 / report['diff_count'])

    if total_size > 0x10000 and diff_pct > 90:
        if dominant_pct > 30:
            return 'PSRAM_BUS_FAILURE', \
                '大面积损坏 (0x%X bytes, %.1f%%), 0x%02X 占主导 (%.1f%%) — PSRAM 总线大面积故障' % (
                    total_size, diff_pct, dominant_byte, dominant_pct)
        return 'DMA_OVERWRITE', \
            '大面积损坏 (0x%X bytes, %.1f%%) — DMA 覆写或 Flash 扇区溢出' % (total_size, diff_pct)

    if diff_pct > 90:
        if dominant_pct > 30:
            return 'PSRAM_BUS_FAILURE', \
                '全覆盖损坏 (%.1f%%), 0x%02X 占主导 (%.1f%%) — PSRAM 总线故障' % (
                    diff_pct, dominant_byte, dominant_pct)
        return 'FULL_OVERWRITE', \
            '全覆盖损坏 (%.1f%%, %d bytes) — 代码区域被完全覆写' % (diff_pct, report['diff_count'])

    if len(report['regions']) == 1:
        region_size = report['regions'][0][1]
        if region_size <= 8:
            return 'BIT_FLIP', \
                '小范围损坏 (%d bytes) — PSRAM 总线 glitch / 比特翻转' % region_size
        if region_size % 0x1000 == 0:
            return 'FLASH_SECTOR', \
                '4KB 对齐损坏 (%d bytes) — Flash 扇区擦写溢出' % region_size

    if len(report['regions']) > 5:
        return 'PARTIAL_OVERWRITE', \
            '多区域损坏 (%d regions, %d bytes) — 堆溢出或缓冲区越界' % (
                len(report['regions']), report['diff_count'])

    return 'UNKNOWN', \
        '损坏 (%d/%d bytes, %.1f%%) — 需进一步分析' % (
            report['diff_count'], report['total'], diff_pct)


def hex_dump(data, base_addr, width=16):
    """格式化 hex dump 输出"""
    lines = []
    for i in range(0, len(data), width):
        chunk = data[i:i + width]
        hex_part = ' '.join('%02X' % b for b in chunk)
        ascii_part = ''.join(chr(b) if 0x20 <= b < 0x7F else '.' for b in chunk)
        lines.append('  %08X: %-48s  %s' % (base_addr + i, hex_part, ascii_part))
    return '\n'.join(lines)


# ============================================================================
# ARM Thumb 反汇编（简化版，覆盖常见指令）
# ============================================================================

def disasm_thumb(data, base_addr):
    """简化的 Thumb 指令反汇编（16位和32位 Thumb-2）"""
    lines = []
    i = 0
    while i < len(data) - 1:
        hw = struct.unpack_from('<H', data, i)[0]
        addr = base_addr + i

        # 检查是否为 32 位 Thumb-2 指令
        is_32bit = (hw >> 11) >= 0x1D  # 0xE800-0xFFFF range

        if is_32bit and i + 3 < len(data):
            hw2 = struct.unpack_from('<H', data, i + 2)[0]
            instr32 = (hw << 16) | hw2

            # BL imm
            if (hw & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xD000:
                s = (hw >> 10) & 1
                j1 = (hw2 >> 13) & 1
                j2 = (hw2 >> 11) & 1
                i1 = ~(j1 ^ s) & 1
                i2 = ~(j2 ^ s) & 1
                imm10 = hw & 0x3FF
                imm11 = hw2 & 0x7FF
                offset = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
                if s:
                    offset |= 0xFE000000
                    offset -= 0x100000000
                target = addr + 4 + offset
                lines.append('  %08X: %04X %04X    bl       0x%08X' % (addr, hw, hw2, target & 0xFFFFFFFF))
            # BLX imm — Thumb-2 BLX (immediate) T1 encoding
            # hw1 = 1111 0 S imm10, hw2 = 11 J1 0 J2 imm10H H=0
            # Offset = sign : i1 : i2 : imm10 : imm10H : '0' (must be word-aligned)
            elif (hw & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0xC000:
                s = (hw >> 10) & 1
                j1 = (hw2 >> 13) & 1
                j2 = (hw2 >> 11) & 1
                i1 = (~(j1 ^ s)) & 1
                i2 = (~(j2 ^ s)) & 1
                imm10 = hw & 0x3FF
                imm10h = (hw2 & 0x7FF) >> 1   # bits 10:1 of hw2
                offset = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm10h << 2)
                if s:
                    offset |= 0xFE000000
                    offset = offset - 0x100000000
                target = ((addr + 4 + offset) & 0xFFFFFFFC)
                lines.append('  %08X: %04X %04X    blx      0x%08X' % (addr, hw, hw2, target & 0xFFFFFFFF))
            # STR/LDR with immediate offset (Thumb-2)
            elif (hw & 0xFFF0) == 0xF8C0:
                rn = hw & 0xF
                rt = (hw2 >> 12) & 0xF
                imm12 = hw2 & 0xFFF
                lines.append('  %08X: %04X %04X    str      r%d, [r%d, #0x%X]' % (addr, hw, hw2, rt, rn, imm12))
            elif (hw & 0xFFF0) == 0xF8D0:
                rn = hw & 0xF
                rt = (hw2 >> 12) & 0xF
                imm12 = hw2 & 0xFFF
                lines.append('  %08X: %04X %04X    ldr      r%d, [r%d, #0x%X]' % (addr, hw, hw2, rt, rn, imm12))
            else:
                lines.append('  %08X: %04X %04X    .long    0x%08X' % (addr, hw, hw2, instr32))
            i += 4
        else:
            # 16-bit Thumb instructions
            op = hw >> 8
            rd = hw & 0x7
            rn = (hw >> 3) & 0x7

            if (hw & 0xFE00) == 0xB400:  # PUSH
                reg_list = []
                M = (hw >> 8) & 1  # LR bit
                rlist = hw & 0xFF
                for bit in range(8):
                    if rlist & (1 << bit):
                        reg_list.append('r%d' % bit)
                if M:
                    reg_list.append('lr')
                lines.append('  %08X: %04X          push     {%s}' % (addr, hw, ', '.join(reg_list)))
            elif (hw & 0xFE00) == 0xBC00:  # POP
                reg_list = []
                M = (hw >> 8) & 1  # PC bit
                rlist = hw & 0xFF
                for bit in range(8):
                    if rlist & (1 << bit):
                        reg_list.append('r%d' % bit)
                if M:
                    reg_list.append('pc')
                lines.append('  %08X: %04X          pop      {%s}' % (addr, hw, ', '.join(reg_list)))
            elif (hw & 0xF800) == 0x2000:  # MOVS Rd, #imm8
                rd = (hw >> 8) & 0x7
                imm = hw & 0xFF
                lines.append('  %08X: %04X          movs     r%d, #0x%X' % (addr, hw, rd, imm))
            elif (hw & 0xF800) == 0x9000:  # STR Rt, [SP, #imm8*4]
                rt = (hw >> 8) & 0x7
                imm = (hw & 0xFF) * 4
                lines.append('  %08X: %04X          str      r%d, [sp, #0x%X]' % (addr, hw, rt, imm))
            elif (hw & 0xF800) == 0x9800:  # LDR Rt, [SP, #imm8*4]
                rt = (hw >> 8) & 0x7
                imm = (hw & 0xFF) * 4
                lines.append('  %08X: %04X          ldr      r%d, [sp, #0x%X]' % (addr, hw, rt, imm))
            elif (hw & 0xF800) == 0x6000:  # STR Rt, [Rn, #imm5*4]
                imm = ((hw >> 6) & 0x1F) * 4
                lines.append('  %08X: %04X          str      r%d, [r%d, #0x%X]' % (addr, hw, rd, rn, imm))
            elif (hw & 0xF800) == 0x6800:  # LDR Rt, [Rn, #imm5*4]
                imm = ((hw >> 6) & 0x1F) * 4
                lines.append('  %08X: %04X          ldr      r%d, [r%d, #0x%X]' % (addr, hw, rd, rn, imm))
            elif (hw & 0xFF00) == 0x4600:  # MOV Rd, Rm (high register)
                d = (hw >> 7) & 1
                rm = (hw >> 3) & 0xF
                rd2 = (hw & 7) | (d << 3)
                if rd2 == 15:
                    lines.append('  %08X: %04X          mov      pc, r%d' % (addr, hw, rm))
                elif rm == 15:
                    lines.append('  %08X: %04X          mov      r%d, pc' % (addr, hw, rd2))
                else:
                    lines.append('  %08X: %04X          mov      r%d, r%d' % (addr, hw, rd2, rm))
            elif (hw & 0xF800) == 0x7800:  # LDRB Rt, [Rn, #imm5]
                imm = (hw >> 6) & 0x1F
                lines.append('  %08X: %04X          ldrb     r%d, [r%d, #0x%X]' % (addr, hw, rd, rn, imm))
            elif (hw & 0xF800) == 0x7000:  # STRB Rt, [Rn, #imm5]
                imm = (hw >> 6) & 0x1F
                lines.append('  %08X: %04X          strb     r%d, [r%d, #0x%X]' % (addr, hw, rd, rn, imm))
            elif (hw & 0xF800) == 0x8800:  # LDRH Rt, [Rn, #imm5*2]
                imm = ((hw >> 6) & 0x1F) * 2
                lines.append('  %08X: %04X          ldrh     r%d, [r%d, #0x%X]' % (addr, hw, rd, rn, imm))
            elif (hw & 0xF800) == 0x8000:  # STRH Rt, [Rn, #imm5*2]
                imm = ((hw >> 6) & 0x1F) * 2
                lines.append('  %08X: %04X          strh     r%d, [r%d, #0x%X]' % (addr, hw, rd, rn, imm))
            elif (hw & 0xF800) == 0xA800:  # ADD Rd, SP, #imm8*4
                rd = (hw >> 8) & 0x7
                imm = (hw & 0xFF) * 4
                lines.append('  %08X: %04X          add      r%d, sp, #0x%X' % (addr, hw, rd, imm))
            elif (hw & 0xF800) == 0xA000:  # ADR Rd, label
                rd = (hw >> 8) & 0x7
                imm = (hw & 0xFF) * 4
                lines.append('  %08X: %04X          adr      r%d, [pc, #0x%X]' % (addr, hw, rd, imm))
            elif (hw & 0xFF00) == 0xB000:  # SUB/ADD SP
                if hw & 0x80:
                    imm = (hw & 0x7F) * 4
                    lines.append('  %08X: %04X          sub      sp, sp, #0x%X' % (addr, hw, imm))
                else:
                    imm = (hw & 0x7F) * 4
                    lines.append('  %08X: %04X          add      sp, sp, #0x%X' % (addr, hw, imm))
            elif (hw & 0xF800) == 0x2800:  # CMP Rn, #imm8
                rn = (hw >> 8) & 0x7
                imm = hw & 0xFF
                lines.append('  %08X: %04X          cmp      r%d, #0x%X' % (addr, hw, rn, imm))
            elif (hw & 0xF000) == 0xD000:  # B<cond>
                cond = (hw >> 8) & 0xF
                offset = hw & 0xFF
                if offset & 0x80:
                    offset -= 0x100
                target = addr + 4 + offset * 2
                cond_names = ['eq', 'ne', 'cs', 'cc', 'mi', 'pl', 'vs', 'vc',
                              'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al', 'nv']
                lines.append('  %08X: %04X          b%s      0x%08X' % (addr, hw, cond_names[cond], target & 0xFFFFFFFF))
            elif (hw & 0xF800) == 0xE000:  # B (unconditional)
                offset = hw & 0x7FF
                if offset & 0x400:
                    offset -= 0x800
                target = addr + 4 + offset * 2
                lines.append('  %08X: %04X          b        0x%08X' % (addr, hw, target & 0xFFFFFFFF))
            elif (hw & 0xFF80) == 0x4700:  # BX Rm
                rm = (hw >> 3) & 0xF
                lines.append('  %08X: %04X          bx       r%d' % (addr, hw, rm))
            elif (hw & 0xFF80) == 0x4780:  # BLX Rm
                rm = (hw >> 3) & 0xF
                lines.append('  %08X: %04X          blx      r%d' % (addr, hw, rm))
            elif (hw & 0xF800) == 0x4800:  # LDR Rt, [PC, #imm8*4]
                rt = (hw >> 8) & 0x7
                imm = (hw & 0xFF) * 4
                lines.append('  %08X: %04X          ldr      r%d, [pc, #0x%X]' % (addr, hw, rt, imm))
            elif (hw & 0xFE00) == 0x0000:  # MOV (register) Rd, Rm
                lines.append('  %08X: %04X          movs     r%d, r%d' % (addr, hw, rd, rn))
            elif (hw & 0xF800) == 0x3000:  # ADD/SUB Rd, #imm8
                rd = (hw >> 8) & 0x7
                imm = hw & 0xFF
                lines.append('  %08X: %04X          adds     r%d, #0x%X' % (addr, hw, rd, imm))
            elif (hw & 0xF800) == 0x3800:  # SUBS Rd, #imm8
                rd = (hw >> 8) & 0x7
                imm = hw & 0xFF
                lines.append('  %08X: %04X          subs     r%d, #0x%X' % (addr, hw, rd, imm))
            elif (hw & 0xF800) == 0x1800:  # ADDS Rd, Rn, Rm
                rm = hw & 0x7
                lines.append('  %08X: %04X          adds     r%d, r%d, r%d' % (addr, hw, rd, rn, rm))
            elif (hw & 0xF800) == 0x1A00:  # SUBS Rd, Rn, Rm
                rm = hw & 0x7
                lines.append('  %08X: %04X          subs     r%d, r%d, r%d' % (addr, hw, rd, rn, rm))
            else:
                lines.append('  %08X: %04X          .short   0x%04X' % (addr, hw, hw))
            i += 2

    return lines


def cmd_list_sections(axf_path):
    """列出 AXF 文件中所有可执行段"""
    sections = parse_elf_sections(axf_path)
    if not sections:
        return

    print("=" * 72)
    print("  ELF Sections: %s" % os.path.basename(axf_path))
    print("=" * 72)
    print("  %-4s %-20s %-10s %-10s %-10s %-6s %s" % (
        "#", "Name", "VirtAddr", "FileSize", "Size", "Flags", "Type"))
    print("  " + "-" * 70)

    for sec in sections:
        if sec['type'] == SHT_PROGBITS and sec['size'] > 0:
            flags_str = ''
            if sec['flags'] & SHF_ALLOC:
                flags_str += 'A'
            if sec['flags'] & SHF_EXECINSTR:
                flags_str += 'X'
            if sec['flags'] & 0x1:
                flags_str += 'W'

            print("  %-4d %-20s 0x%08X 0x%08X 0x%08X %-6s %d" % (
                sec['index'], sec['name'], sec['addr'],
                sec['size'], sec['size'], flags_str or '-',
                sec['type']))


def cmd_compare(axf_path, ddr_path, pc_addr, base_addr, size, scan_size, show_disasm):
    """执行 AXF vs DDR 对比"""
    # 解析 AXF
    sections = parse_elf_sections(axf_path)
    if not sections:
        return

    # 确定对比的起始地址（清除 Thumb bit）
    start_addr = pc_addr & ~1

    # 确定对比范围
    if scan_size > 0:
        end_addr = start_addr + scan_size
    else:
        end_addr = start_addr + size

    print("=" * 72)
    print("  AXF vs DDR Code Integrity Compare")
    print("=" * 72)
    print("  AXF:      %s" % os.path.basename(axf_path))
    print("  DDR:      %s" % os.path.basename(ddr_path))
    print("  Address:  0x%08X" % start_addr)
    print("  DDR base: 0x%08X" % base_addr)
    print("  Size:     0x%X (%d bytes)" % (end_addr - start_addr, end_addr - start_addr))

    # 找到包含起始地址的 section
    sec = find_section_for_addr(sections, start_addr)
    if sec:
        print("  Section:  %s (0x%08X..0x%08X)" % (sec['name'], sec['addr'], sec['addr'] + sec['size']))
    else:
        print("  WARNING: Address 0x%08X not found in any AXF section" % start_addr)

    # 读取 AXF 字节
    axf_data, sec = read_axf_bytes(axf_path, sections, start_addr, end_addr - start_addr)
    if axf_data is None:
        print("\nERROR: Cannot read AXF bytes at 0x%08X" % start_addr)
        print("  Available sections:")
        for s in sections:
            if s['type'] == SHT_PROGBITS and s['flags'] & SHF_EXECINSTR:
                print("    %s: 0x%08X..0x%08X" % (s['name'], s['addr'], s['addr'] + s['size']))
        return

    # 读取 DDR 字节
    ddr_data = read_ddr_bytes(ddr_path, start_addr, base_addr, len(axf_data))
    if ddr_data is None:
        print("\nERROR: Cannot read DDR bytes at 0x%08X (base=0x%08X)" % (start_addr, base_addr))
        return

    # 对比
    report = compare_bytes(axf_data, ddr_data, start_addr)

    # 输出结果
    print("\n--- Comparison Result ---")
    print("  Total bytes: %d" % report['total'])
    print("  Matched:     %d (%.1f%%)" % (report['match_count'], report['match_pct']))
    print("  Different:   %d (%.1f%%)" % (report['diff_count'],
                                            report['diff_count'] * 100.0 / report['total'] if report['total'] > 0 else 0))

    if report['diff_count'] == 0:
        print("\n  *** CODE INTACT: AXF matches DDR — No corruption detected ***")
        if show_disasm:
            print("\n--- AXF Disassembly ---")
            disasm_lines = disasm_thumb(axf_data, start_addr)
            for line in disasm_lines:
                print(line)
        return

    # 有差异 — 详细报告
    print("\n  *** CODE CORRUPTED: AXF differs from DDR ***")

    # 损坏分类
    ctype, cdesc = classify_corruption(report, len(axf_data))
    print("\n--- Corruption Classification ---")
    print("  Type: %s" % ctype)
    print("  %s" % cdesc)

    # DDR 损坏字节频率
    if report['ddr_byte_freq']:
        print("\n--- DDR Corrupted Byte Frequency (top 5) ---")
        sorted_freq = sorted(report['ddr_byte_freq'].items(), key=lambda x: -x[1])[:5]
        for byte_val, count in sorted_freq:
            pct = count * 100.0 / report['diff_count']
            print("  0x%02X: %d times (%.1f%%)" % (byte_val, count, pct))

    # 连续损坏区域
    if report['regions']:
        print("\n--- Corrupted Regions ---")
        for region_start, region_size in report['regions'][:10]:
            print("  0x%08X..0x%08X (%d bytes)" % (region_start, region_start + region_size - 1, region_size))
        if len(report['regions']) > 10:
            print("  ... and %d more regions" % (len(report['regions']) - 10))

    # Hex dump 对比
    print("\n--- Hex Dump Comparison ---")
    context_before = min(16, start_addr & 0xF)  # 对齐到16字节边界

    # AXF hex dump
    print("\n  AXF (compiled):")
    print(hex_dump(axf_data[:min(128, len(axf_data))], start_addr))

    # DDR hex dump
    print("\n  DDR (runtime):")
    print(hex_dump(ddr_data[:min(128, len(ddr_data))], start_addr))

    # 差异标注
    print("\n  Diff markers:")
    diff_addrs = set(d[0] for d in report['diffs'][:64])
    for i in range(0, min(128, len(axf_data)), 16):
        markers = []
        for j in range(16):
            if i + j < len(axf_data) and (start_addr + i + j) in diff_addrs:
                markers.append('XX')
            else:
                markers.append('  ')
        print("  %08X: %s" % (start_addr + i, ' '.join(markers)))

    # 反汇编对比
    if show_disasm:
        print("\n--- AXF Disassembly (original compiled code) ---")
        disasm_lines = disasm_thumb(axf_data[:min(64, len(axf_data))], start_addr)
        for line in disasm_lines:
            print(line)

        print("\n--- DDR Disassembly (runtime, possibly corrupted) ---")
        disasm_lines = disasm_thumb(ddr_data[:min(64, len(ddr_data))], start_addr)
        for line in disasm_lines:
            print(line)

    # 结论
    print("\n" + "=" * 72)
    print("  CONCLUSION")
    print("=" * 72)
    if ctype != 'NONE':
        print("  Root cause: PSRAM CODE CORRUPTION (%s)" % ctype)
        print("  Platform crash analysis may be WRONG (based on corrupted code)")
        print("  AXF disassembly represents the ORIGINAL compiled code")


def cmd_axf_only(axf_path, pc_addr, size, show_disasm):
    """仅提取并显示 AXF 中的代码"""
    sections = parse_elf_sections(axf_path)
    if not sections:
        return

    start_addr = pc_addr & ~1

    axf_data, sec = read_axf_bytes(axf_path, sections, start_addr, size)
    if axf_data is None:
        print("ERROR: Cannot read AXF bytes at 0x%08X" % start_addr)
        return

    print("=" * 72)
    print("  AXF Code at 0x%08X" % start_addr)
    print("=" * 72)
    print("  Section: %s" % (sec['name'] if sec else 'unknown'))
    print("  Size:    %d bytes" % len(axf_data))

    print("\n--- Hex Dump ---")
    print(hex_dump(axf_data, start_addr))

    if show_disasm:
        print("\n--- Disassembly ---")
        disasm_lines = disasm_thumb(axf_data, start_addr)
        for line in disasm_lines:
            print(line)


def cmd_decode_dfsr(dfsr_value):
    """解码 DFSR 寄存器值"""
    print("=" * 60)
    print("  DFSR Decoder (PMSAv7)")
    print("=" * 60)
    print("  DFSR = 0x%08X" % dfsr_value)
    print("  Binary: %s" % format(dfsr_value, '032b'))

    fsc = ((dfsr_value >> 6) & 0x10) + (dfsr_value & 0x0F)
    wnr = (dfsr_value >> 11) & 1
    ext = (dfsr_value >> 12) & 1

    print("\n  FSC (Fault Status Code): 0x%02X" % fsc)
    print("  WnR (Write not Read):    %d (%s)" % (wnr, "Write" if wnr else "Read"))
    print("  ExT (External abort):    %d" % ext)

    fsc_names = {
        0x00: 'Background fault (MPU)',
        0x01: 'Alignment fault',
        0x02: 'Debug event',
        0x03: 'Reserved',
        0x04: 'Instruction cache maintenance fault',
        0x05: 'Reserved',
        0x06: 'Translation fault, first level',
        0x07: 'Translation fault, second level',
        0x08: 'Synchronous external abort',
        0x09: 'Reserved',
        0x0A: 'Reserved',
        0x0B: 'Reserved',
        0x0C: 'Sync external abort, first level',
        0x0D: 'Permission fault (MPU)',
        0x0E: 'Alignment fault, first level',
        0x0F: 'Reserved',
        0x14: 'Lockdown (implementation defined)',
        0x16: 'Asynchronous external abort',
        0x18: 'Asynchronous parity error',
        0x19: 'Synchronous parity error',
        0x1A: 'Co-processor abort',
    }

    name = fsc_names.get(fsc, 'Reserved/Unknown')
    print("\n  Fault: %s" % name)

    dfar_valid = fsc in (0x00, 0x01, 0x06, 0x07, 0x08, 0x0C, 0x0D, 0x0E, 0x19)
    pc_points_to_fault = fsc not in (0x16, 0x18)

    print("  DFAR valid: %s" % ("Yes" if dfar_valid else "NO"))
    print("  PC points to faulting instruction: %s" % ("Yes" if pc_points_to_fault else "NO (asynchronous)"))

    if fsc == 0x0D:
        print("\n  >>> This is a Permission fault (MPU), NOT an async external abort")
        print("  >>> DFAR is valid, PC points to the faulting instruction")
    elif fsc == 0x16:
        print("\n  >>> This is an Asynchronous external abort")
        print("  >>> DFAR is NOT reliable, PC does NOT point to the faulting instruction")


def main():
    parser = argparse.ArgumentParser(
        description='AXF vs DDR Code Integrity Compare',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Compare AXF vs DDR at crash address
  python ddr_code_compare.py app.axf com_DDR_RW.bin --pc 0x7E88003C --base 0x7E20FFFC

  # Compare with larger scan range
  python ddr_code_compare.py app.axf com_DDR_RW.bin --pc 0x7E88003C --base 0x7E20FFFC --scan 0x100000

  # Show AXF code only with disassembly
  python ddr_code_compare.py app.axf --pc 0x7E88003C --disasm

  # List ELF sections
  python ddr_code_compare.py app.axf --list-sections

  # Decode DFSR register value
  python ddr_code_compare.py --decode-dfsr 0x80D
        """)

    parser.add_argument('axf_file', nargs='?', help='Path to AXF/ELF file')
    parser.add_argument('ddr_file', nargs='?', help='Path to DDR dump binary')
    parser.add_argument('--pc', type=lambda x: int(x, 0), default=0,
                        help='Crash PC address (hex)')
    parser.add_argument('--base', type=lambda x: int(x, 0), default=0,
                        help='DDR dump base address (hex)')
    parser.add_argument('--size', type=lambda x: int(x, 0), default=32,
                        help='Bytes to compare (default: 32)')
    parser.add_argument('--scan', type=lambda x: int(x, 0), default=0,
                        help='Scan range in bytes for large-area detection (hex)')
    parser.add_argument('--disasm', action='store_true',
                        help='Show Thumb disassembly')
    parser.add_argument('--list-sections', action='store_true',
                        help='List all ELF sections')
    parser.add_argument('--decode-dfsr', type=lambda x: int(x, 0),
                        help='Decode DFSR register value (hex)')

    args = parser.parse_args()

    if args.decode_dfsr is not None:
        cmd_decode_dfsr(args.decode_dfsr)
        return

    if args.list_sections:
        if not args.axf_file:
            print("ERROR: AXF file path required for --list-sections")
            return
        cmd_list_sections(args.axf_file)
        return

    if not args.axf_file:
        parser.print_help()
        return

    if args.ddr_file:
        # Full compare mode
        cmd_compare(args.axf_file, args.ddr_file, args.pc, args.base,
                    args.size, args.scan, args.disasm)
    else:
        # AXF only mode
        cmd_axf_only(args.axf_file, args.pc, args.size, args.disasm)


if __name__ == '__main__':
    main()
