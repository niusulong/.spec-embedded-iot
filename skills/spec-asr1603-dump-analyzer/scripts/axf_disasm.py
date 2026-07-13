#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
AXF (ARM ELF) crash disassembler for Thumb/ARM instruction set.
Extracts and decodes machine code at a given address range from an AXF/ELF binary.

Usage:
    python axf_disasm.py <axf_file> --address <hex_addr> [--size <bytes>] [--arch thumb|arm]

Example:
    python axf_disasm.py firmware.axf --address 0x7e880040 --size 32
    python axf_disasm.py firmware.axf --address 0x7e88003d --size 26 --arch thumb
"""

import struct
import sys
import os
import argparse

# Import shared utilities
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_elf_sections as _parse_elf_sections, find_elf_section, read_elf_bytes

_REG_NAMES = {13: 'sp', 14: 'lr', 15: 'pc'}


def reg(n):
    """Register name with alias: 13→sp, 14→lr, 15→pc."""
    return _REG_NAMES.get(n, 'r{}'.format(n))


def thumb2_expand_imm(i, imm3, imm8):
    """ARM Thumb-2 modified immediate expansion (ARM Architecture Reference Manual A5.3).

    Decodes the 12-bit modified immediate (i:imm3:imm8) into a 32-bit value.
    For crash analysis, this is sufficient for common small immediates.
    """
    imm12 = (i << 11) | (imm3 << 8) | imm8
    if (imm12 >> 10) == 0:
        # 000x xxhh hhhh: value = imm8 (0-255)
        return imm8
    rotation = ((imm12 >> 7) & 0x1E)  # 0,2,4,...,30
    if rotation:
        val = ((imm8 >> rotation) | (imm8 << (32 - rotation))) & 0xFFFFFFFF
    else:
        val = imm8
    return val


def _reg_list_str(mask, include_sp_lr_pc=False):
    """Format a 16-bit register mask as a register list string."""
    names = ['r0','r1','r2','r3','r4','r5','r6','r7',
             'r8','r9','r10','fp','ip','sp','lr','pc']
    regs = []
    for i in range(16):
        if mask & (1 << i):
            regs.append(names[i])
    return ", ".join(regs)


def _decode_thumb32_ldr_str(hw1, hw2, pc):
    """Decode Thumb-2 LDR.W/STR.W/LDRB.W/STRB.W with immediate offset."""
    # LDR.W Rt, [Rn, #imm12] — encoding 0xF8D0
    if (hw1 & 0xFFF0) == 0xF8D0:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm12 = hw2 & 0xFFF
        if imm12 == 0:
            return "ldr.w {}, [{}]".format(reg(rt), reg(rn))
        return "ldr.w {}, [{}, #0x{:x}]".format(reg(rt), reg(rn), imm12)

    # STR.W Rt, [Rn, #imm12] — encoding 0xF8C0
    if (hw1 & 0xFFF0) == 0xF8C0:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm12 = hw2 & 0xFFF
        if imm12 == 0:
            return "str.w {}, [{}]".format(reg(rt), reg(rn))
        return "str.w {}, [{}, #0x{:x}]".format(reg(rt), reg(rn), imm12)

    # LDRB.W Rt, [Rn, #imm12] — encoding 0xF890
    if (hw1 & 0xFFF0) == 0xF890:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm12 = hw2 & 0xFFF
        if imm12 == 0:
            return "ldrb.w {}, [{}]".format(reg(rt), reg(rn))
        return "ldrb.w {}, [{}, #0x{:x}]".format(reg(rt), reg(rn), imm12)

    # STRB.W Rt, [Rn, #imm12] — encoding 0xF880
    if (hw1 & 0xFFF0) == 0xF880:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm12 = hw2 & 0xFFF
        if imm12 == 0:
            return "strb.w {}, [{}]".format(reg(rt), reg(rn))
        return "strb.w {}, [{}, #0x{:x}]".format(reg(rt), reg(rn), imm12)

    # LDR.W Rt, [Rn, #-imm8] (negative offset) — encoding 0xF850 with U=0
    if (hw1 & 0xFFF0) == 0xF850 and not (hw2 & 0x0800):
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm8 = hw2 & 0xFF
        if imm8 == 0:
            return "ldr.w {}, [{}]".format(reg(rt), reg(rn))
        return "ldr.w {}, [{}, #-0x{:x}]".format(reg(rt), reg(rn), imm8)

    # STR.W Rt, [Rn, #-imm8] (negative offset) — encoding 0xF840 with U=0
    if (hw1 & 0xFFF0) == 0xF840 and not (hw2 & 0x0800):
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm8 = hw2 & 0xFF
        if imm8 == 0:
            return "str.w {}, [{}]".format(reg(rt), reg(rn))
        return "str.w {}, [{}, #-0x{:x}]".format(reg(rt), reg(rn), imm8)

    # LDR.W Rt, [Rn, Rm, LSL #imm2] (register offset) — 0xF850 with U=1, imm8=0
    if (hw1 & 0xFFF0) == 0xF850 and (hw2 & 0x0800):
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        rm = hw2 & 0xF
        shift = (hw2 >> 4) & 3
        if shift:
            return "ldr.w {}, [{}, {}, lsl #{}]".format(reg(rt), reg(rn), reg(rm), shift)
        return "ldr.w {}, [{}, {}]".format(reg(rt), reg(rn), reg(rm))

    # STR.W Rt, [Rn, Rm, LSL #imm2] (register offset) — 0xF840 with U=1
    if (hw1 & 0xFFF0) == 0xF840 and (hw2 & 0x0800):
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        rm = hw2 & 0xF
        shift = (hw2 >> 4) & 3
        if shift:
            return "str.w {}, [{}, {}, lsl #{}]".format(reg(rt), reg(rn), reg(rm), shift)
        return "str.w {}, [{}, {}]".format(reg(rt), reg(rn), reg(rm))

    return None


def _decode_thumb32_push_pop(hw1, hw2, pc):
    """Decode Thumb-2 PUSH.W / POP.W (STM/PUSH / LDM/POP)."""
    # PUSH.W {regs} — STMDB SP!, {regs} = 0xE92D
    if hw1 == 0xE92D:
        return "push.w {{{}}}".format(_reg_list_str(hw2))

    # POP.W {regs} — LDMIA SP!, {regs} = 0xE8BD
    if hw1 == 0xE8BD:
        return "pop.w {{{}}}".format(_reg_list_str(hw2))

    # STMIA Rn!, {regs} — 0xE880 or 0xE890
    if (hw1 & 0xFFF0) == 0xE880 or (hw1 & 0xFFF0) == 0xE890:
        rn = hw1 & 0xF
        wback = "!" if (hw1 & 0x10) else ""
        return "stmia {}{}, {{{}}}".format(reg(rn), wback, _reg_list_str(hw2))

    # LDMIA Rn!, {regs} — 0xE890 or 0xE8A0
    if (hw1 & 0xFFF0) == 0xE8A0 or (hw1 & 0xFFF0) == 0xE8B0:
        rn = hw1 & 0xF
        wback = "!" if (hw1 & 0x10) else ""
        return "ldmia {}{}, {{{}}}".format(reg(rn), wback, _reg_list_str(hw2))

    return None


def _decode_thumb32_mov_sub_add_cmp(hw1, hw2, pc):
    """Decode Thumb-2 MOV.W / SUB.W / ADD.W / CMP.W with modified immediate."""
    i_bit = (hw1 >> 10) & 1
    imm3 = (hw2 >> 12) & 7
    imm8 = hw2 & 0xFF
    imm_val = thumb2_expand_imm(i_bit, imm3, imm8)

    # ORR.W / MOV.W Rd, Rn, #imm — (hw1 & 0xFBE0) == 0xF040
    # MOV.W is ORR.W with Rn=15 (PC): hw1[3:0]=0xF
    if (hw1 & 0xFBE0) == 0xF040:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        if rn == 15:
            return "mov{}.w {}, #0x{:x}".format(s_bit, reg(rd), imm_val)
        return "orr{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    # ORN.W / MVN.W Rd, Rn, #imm — (hw1 & 0xFBE0) == 0xF060
    # MVN.W is ORN.W with Rn=15 (PC): hw1[3:0]=0xF
    if (hw1 & 0xFBE0) == 0xF060:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        if rn == 15:
            return "mvn{}.w {}, #0x{:x}".format(s_bit, reg(rd), imm_val)
        return "orn{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    # SUB.W / CMP.W Rd, Rn, #imm12 — (hw1 & 0xFBE0) == 0xF1A0
    # CMP.W is SUBS.W with Rd=15 (encoded in hw2 bits[11:8]=0xF)
    if (hw1 & 0xFBE0) == 0xF1A0:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        if rd == 15:
            return "cmp.w {}, #0x{:x}".format(reg(rn), imm_val)
        if rd == 13 and rn == 13:
            return "sub{}.w sp, sp, #0x{:x}".format(s_bit, imm_val)
        return "sub{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    # ADD.W / CMN.W Rd, Rn, #imm12 — (hw1 & 0xFBE0) == 0xF100
    # CMN.W is ADDS.W with Rd=15
    if (hw1 & 0xFBE0) == 0xF100:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        if rd == 15:
            return "cmn.w {}, #0x{:x}".format(reg(rn), imm_val)
        if rd == 13 and rn == 13:
            return "add{}.w sp, sp, #0x{:x}".format(s_bit, imm_val)
        return "add{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    # ADD.W Rd, Rn, Rm (register) — (hw1 & 0xFFE0) == 0xEB00
    if (hw1 & 0xFFE0) == 0xEB00:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rm = hw2 & 0xF
        shift_type = (hw2 >> 4) & 3
        shift_imm = (hw2 >> 10) & 3
        shift_names = ["lsl", "lsr", "asr", "ror"]
        s_bit = "s" if (hw1 & 0x1000) else ""
        if shift_imm or shift_type:
            return "add{}.w {}, {}, {}, {} #{}".format(s_bit, reg(rd), reg(rn), reg(rm), shift_names[shift_type], shift_imm * 2 if shift_type else shift_imm)
        return "add{}.w {}, {}, {}".format(s_bit, reg(rd), reg(rn), reg(rm))

    # SUB.W Rd, Rn, Rm (register) — (hw1 & 0xFFE0) == 0xEBA0
    if (hw1 & 0xFFE0) == 0xEBA0:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rm = hw2 & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        return "sub{}.w {}, {}, {}".format(s_bit, reg(rd), reg(rn), reg(rm))

    # AND.W / TST.W Rd, Rn, #imm — (hw1 & 0xFBE0) == 0xF000
    # TST.W is ANDS.W with Rd=15
    if (hw1 & 0xFBE0) == 0xF000:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        if rd == 15:
            return "tst.w {}, #0x{:x}".format(reg(rn), imm_val)
        return "and{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    # EOR.W / TEQ.W Rd, Rn, #imm — (hw1 & 0xFBE0) == 0xF080
    # TEQ.W is EORS.W with Rd=15
    if (hw1 & 0xFBE0) == 0xF080:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        if rd == 15:
            return "teq.w {}, #0x{:x}".format(reg(rn), imm_val)
        return "eor{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    # BIC.W Rd, Rn, #imm — (hw1 & 0xFBE0) == 0xF020
    if (hw1 & 0xFBE0) == 0xF020:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        return "bic{}.w {}, {}, #0x{:x}".format(s_bit, reg(rd), reg(rn), imm_val)

    return None


def _decode_thumb32_ldrh_strh(hw1, hw2, pc):
    """Decode Thumb-2 LDRH.W / STRH.W with immediate offset."""
    # LDRH.W Rt, [Rn, #imm12] — 0xF8B0
    if (hw1 & 0xFFF0) == 0xF8B0:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm12 = hw2 & 0xFFF
        if imm12 == 0:
            return "ldrh.w {}, [{}]".format(reg(rt), reg(rn))
        return "ldrh.w {}, [{}, #0x{:x}]".format(reg(rt), reg(rn), imm12)

    # STRH.W Rt, [Rn, #imm12] — 0xF8A0
    if (hw1 & 0xFFF0) == 0xF8A0:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        imm12 = hw2 & 0xFFF
        if imm12 == 0:
            return "strh.w {}, [{}]".format(reg(rt), reg(rn))
        return "strh.w {}, [{}, #0x{:x}]".format(reg(rt), reg(rn), imm12)

    return None


def _decode_thumb32_cbz(hw1, hw2, pc):
    """Decode Thumb-2 CBZ / CBNZ (32-bit form is actually 16-bit, but may appear in 32-bit context)."""
    return None


def _decode_thumb32_misc(hw1, hw2, pc):
    """Decode miscellaneous Thumb-2 instructions."""
    # MRS Rd, APSR — 0xF3EF 0x8000
    if hw1 == 0xF3EF and (hw2 & 0xFF00) == 0x8000:
        rd = (hw2 >> 8) & 0xF
        return "mrs {}, apsr".format(reg(rd))

    # MSR APSR, Rn — 0xF380 0x8000
    if (hw1 & 0xFFF0) == 0xF380 and (hw2 & 0xFF00) == 0x8000:
        rn = hw1 & 0xF
        return "msr apsr, {}".format(reg(rn))

    # DMB / DSB / ISB — 0xF3BF 0x8F5x
    if hw1 == 0xF3BF:
        option = hw2 & 0xF
        if (hw2 & 0xFFF0) == 0x8F50:
            return "dmb #{}".format(option)
        if (hw2 & 0xFFF0) == 0x8F40:
            return "dsb #{}".format(option)
        if hw2 == 0x8F60:
            return "isb"

    # SXTB / SXTH / UXTB / UXTH — 0xFA4x / 0xFA5x
    if (hw1 & 0xFFE0) == 0xFA40:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rotate = ((hw2 >> 4) & 3) * 8
        if (hw1 & 0x10) == 0:
            if rotate:
                return "sxth.w {}, {}, ror #{}".format(reg(rd), reg(rn), rotate)
            return "sxth.w {}, {}".format(reg(rd), reg(rn))
        else:
            if rotate:
                return "sxtb.w {}, {}, ror #{}".format(reg(rd), reg(rn), rotate)
            return "sxtb.w {}, {}".format(reg(rd), reg(rn))

    if (hw1 & 0xFFE0) == 0xFA50:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rotate = ((hw2 >> 4) & 3) * 8
        if (hw1 & 0x10) == 0:
            if rotate:
                return "uxth.w {}, {}, ror #{}".format(reg(rd), reg(rn), rotate)
            return "uxth.w {}, {}".format(reg(rd), reg(rn))
        else:
            if rotate:
                return "uxtb.w {}, {}, ror #{}".format(reg(rd), reg(rn), rotate)
            return "uxtb.w {}, {}".format(reg(rd), reg(rn))

    # MOV.W Rd, Rm (register to register, T3 encoding) — 0xEA4F
    if (hw1 & 0xFFEF) == 0xEA4F:
        rd = (hw2 >> 8) & 0xF
        rm = hw2 & 0xF
        s_bit = "s" if (hw1 & 0x1000) else ""
        shift_type = (hw2 >> 4) & 3
        shift_imm = (hw2 >> 10) & 3
        shift_names = ["lsl", "lsr", "asr", "ror"]
        if shift_imm or shift_type:
            return "mov{}.w {}, {}, {} #{}".format(s_bit, reg(rd), reg(rm), shift_names[shift_type], shift_imm)
        return "mov{}.w {}, {}".format(s_bit, reg(rd), reg(rm))

    # BFC / BFI — bit field clear/insert
    # BFC Rd, #lsb, #width — encoding: hw1=0xF36F, hw2 encodes msb:lsb
    #   msb = (hw2 >> 6) & 0x1F  (5-bit field)
    #   lsb = (hw2 & 0x7) | ((hw2 >> 10) & 0x1C)  (5-bit field from imm3:imm2)
    if hw1 == 0xF36F:
        rd = (hw2 >> 8) & 0xF
        msb = (hw2 >> 6) & 0x1F
        lsb = (hw2 & 0x7) | ((hw2 >> 10) & 0x1C)
        width = msb - lsb + 1
        return "bfc {}, #{}, #{}".format(reg(rd), lsb, width)
    # BFI Rd, Rm, #lsb, #width — encoding: hw1=0xF360|(Rm), hw2 encodes msb:lsb
    if (hw1 & 0xFFF0) == 0xF360 and (hw1 & 0xF) != 0xF:
        rm = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        msb = (hw2 >> 6) & 0x1F
        lsb = (hw2 & 0x7) | ((hw2 >> 10) & 0x1C)
        width = msb - lsb + 1
        return "bfi {}, {}, #{}, #{}".format(reg(rd), reg(rm), lsb, width)

    # UDIV / SDIV — 0xFBB0 / 0xFB90
    if (hw1 & 0xFFF0) == 0xFBB0:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rm = hw2 & 0xF
        return "udiv {}, {}, {}".format(reg(rd), reg(rn), reg(rm))

    if (hw1 & 0xFFF0) == 0xFB90:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rm = hw2 & 0xF
        return "sdiv {}, {}, {}".format(reg(rd), reg(rn), reg(rm))

    # MUL — 0xFB00
    if (hw1 & 0xFFF0) == 0xFB00:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        rm = hw2 & 0xF
        return "mul {}, {}, {}".format(reg(rd), reg(rn), reg(rm))

    # MLA / MLS — 0xFB00 with Ra != 0xF / 0xFB10
    if (hw1 & 0xFFF0) == 0xFB00:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        ra = (hw2 >> 12) & 0xF
        rm = hw2 & 0xF
        if ra == 0xF:
            return "mul {}, {}, {}".format(reg(rd), reg(rn), reg(rm))
        return "mla {}, {}, {}, {}".format(reg(rd), reg(rn), reg(rm), reg(ra))

    if (hw1 & 0xFFF0) == 0xFB10:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        ra = (hw2 >> 12) & 0xF
        rm = hw2 & 0xF
        return "mls {}, {}, {}, {}".format(reg(rd), reg(rn), reg(rm), reg(ra))

    # REV / REVH / RBIT — 0xFA90 / 0xFAA0 / 0xFA90
    if (hw1 & 0xFFF0) == 0xFA90:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        op2 = (hw2 >> 4) & 0xF
        rev_ops = {0x0: "rev.w", 0x2: "revh.w", 0x4: "rev16.w"}
        if op2 in rev_ops:
            return "{} {}, {}".format(rev_ops[op2], reg(rd), reg(rn))

    if (hw1 & 0xFFF0) == 0xFA90 and (hw2 & 0xF0F0) == 0x0000:
        rn = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        return "rbit {}, {}".format(reg(rd), reg(rn))

    return None


def _decode_thumb32_branch(hw1, hw2, pc):
    """Decode Thumb-2 conditional branch B<c>.W and other branch instructions."""
    # B<c>.W (conditional branch, 32-bit encoding) — 0xF000 with hw2[15:14]=10
    if (hw1 & 0xF800) == 0xF000 and (hw2 & 0xD000) == 0x8000:
        cond = (hw1 >> 6) & 0xF
        s = (hw1 >> 10) & 1
        imm6 = hw1 & 0x3F
        j1 = (hw2 >> 13) & 1
        j2 = (hw2 >> 11) & 1
        imm11 = hw2 & 0x7FF
        offset = (s << 20) | (j2 << 19) | (j1 << 18) | (imm6 << 12) | (imm11 << 1)
        if s:
            offset -= (1 << 21)
        target = pc + 4 + offset
        cond_names = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
                      "hi", "ls", "ge", "lt", "gt", "le", "", ""]
        if cond < 14:
            return "b{}.w 0x{:x}".format(cond_names[cond], target & 0xFFFFFFFF)

    return None


def _decode_bl_pair(hw1, hw2, pc):
    """Decode a BL/BLX instruction pair from prefix (hw1) and suffix (hw2)."""
    s = (hw1 >> 10) & 1
    imm10 = hw1 & 0x3FF
    j1 = (hw2 >> 13) & 1
    j2 = (hw2 >> 11) & 1
    imm11 = hw2 & 0x7FF
    i1 = 1 - (j1 ^ s)
    i2 = 1 - (j2 ^ s)
    offset = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
    if s:
        offset -= (1 << 25)
    target = pc + 4 + offset
    if hw2 & 0x1000:
        return "bl 0x{:x}".format(target & 0xFFFFFFFF)
    return "blx 0x{:x}".format((target & 0xFFFFFFFF) & ~1)


def _decode_shift_imm(insn, op, pc):
    imm5 = (insn >> 6) & 0x1F
    rm = (insn >> 3) & 7
    rd = insn & 7
    return "{op} r{rd}, r{rm}, #{imm}".format(op=op, rd=rd, rm=rm, imm=imm5)


def _decode_add_sub(insn, pc):
    op = (insn >> 9) & 3
    rm_imm = (insn >> 6) & 7
    rn = (insn >> 3) & 7
    rd = insn & 7
    if op == 0:
        return "add r{rd}, r{rn}, r{rm}".format(rd=rd, rn=rn, rm=rm_imm)
    elif op == 1:
        return "sub r{rd}, r{rn}, r{rm}".format(rd=rd, rn=rn, rm=rm_imm)
    elif op == 2:
        return "add r{rd}, r{rn}, #{imm}".format(rd=rd, rn=rn, imm=rm_imm)
    else:
        return "sub r{rd}, r{rn}, #{imm}".format(rd=rd, rn=rn, imm=rm_imm)


def _decode_mov_imm(insn, pc):
    rd = (insn >> 8) & 7
    imm = insn & 0xFF
    return "movs r{rd}, #{imm}".format(rd=rd, imm=imm)


def _decode_cmp_imm(insn, pc):
    rn = (insn >> 8) & 7
    imm = insn & 0xFF
    return "cmp r{rn}, #{imm}".format(rn=rn, imm=imm)


def _decode_add_imm(insn, pc):
    rd = (insn >> 8) & 7
    imm = insn & 0xFF
    return "adds r{rd}, #{imm}".format(rd=rd, imm=imm)


def _decode_sub_imm(insn, pc):
    rd = (insn >> 8) & 7
    imm = insn & 0xFF
    return "subs r{rd}, #{imm}".format(rd=rd, imm=imm)


def _decode_alu_op(insn, pc):
    op = (insn >> 6) & 0xF
    rm = (insn >> 3) & 7
    rd = insn & 7
    ops = ["and", "eor", "lsl", "lsr", "asr", "adc", "sbc", "ror",
           "tst", "rsb", "cmp", "cmn", "orr", "mul", "bic", "mvn"]
    return "{op} r{rd}, r{rm}".format(op=ops[op], rd=rd, rm=rm)


def _decode_str_ldr(insn, op, pc):
    imm5 = (insn >> 6) & 0x1F
    rn = (insn >> 3) & 7
    rd = insn & 7
    return "{op} r{rd}, [r{rn}, #{imm}]".format(op=op, rd=rd, rn=rn, imm=imm5 * 4)


def _decode_strb_ldrb(insn, op, pc):
    imm5 = (insn >> 6) & 0x1F
    rn = (insn >> 3) & 7
    rd = insn & 7
    return "{op} r{rd}, [r{rn}, #{imm}]".format(op=op, rd=rd, rn=rn, imm=imm5)


def _decode_strh_ldrh(insn, op, pc):
    imm5 = (insn >> 6) & 0x1F
    rn = (insn >> 3) & 7
    rd = insn & 7
    return "{op} r{rd}, [r{rn}, #{imm}]".format(op=op, rd=rd, rn=rn, imm=imm5 * 2)


def _decode_misc(insn, pc):
    op1 = (insn >> 5) & 0x1F
    if insn & 0xFF00 == 0xB400 or insn & 0xFF00 == 0xB500:
        # PUSH
        rlist = insn & 0xFF
        lr = (insn >> 8) & 1
        regs = [i for i in range(8) if rlist & (1 << i)]
        if lr:
            regs.append("lr")
        return "push {{{reg_list}}}".format(reg_list=", ".join(
            ["r{}".format(r) if isinstance(r, int) else r for r in regs]))
    elif insn & 0xFF00 == 0xBC00 or insn & 0xFF00 == 0xBD00:
        # POP
        rlist = insn & 0xFF
        pc_bit = (insn >> 8) & 1
        regs = [i for i in range(8) if rlist & (1 << i)]
        if pc_bit:
            regs.append("pc")
        return "pop {{{reg_list}}}".format(reg_list=", ".join(
            ["r{}".format(r) if isinstance(r, int) else r for r in regs]))
    elif insn & 0xFF80 == 0xB680:
        return "cpsie {flags}".format(flags=insn & 0x7F)
    elif insn & 0xFF80 == 0xB600:
        return "cpsid {flags}".format(flags=insn & 0x7F)
    elif insn == 0xBF00:
        return "nop"
    elif insn & 0xFF00 == 0xBF00:
        return "it/ite (conditional exec)"
    elif insn & 0xFF00 == 0x4600 or (insn & 0xFF00 == 0x4700):
        # MOV high register / BX/BLX
        d = (insn >> 7) & 1
        rm = (insn >> 3) & 0xF
        rd = (insn & 7) | (d << 3)
        if insn & 0xFF80 == 0x4700:
            if insn & 0x80:
                return "blx {rm}".format(rm=reg(rm))
            else:
                return "bx {rm}".format(rm=reg(rm))
        return "mov {rd}, {rm}".format(rd=reg(rd), rm=reg(rm))
    return "misc 0x{:04x}".format(insn)


def _decode_push_pop(insn, pc):
    if insn & 0x0800:
        # POP
        rlist = insn & 0xFF
        pc_bit = (insn >> 8) & 1
        regs = [i for i in range(8) if rlist & (1 << i)]
        if pc_bit:
            regs.append("pc")
        return "pop {{{}}}".format(", ".join(
            ["r{}".format(r) if isinstance(r, int) else r for r in regs]))
    else:
        # PUSH
        rlist = insn & 0xFF
        lr = (insn >> 8) & 1
        regs = [i for i in range(8) if rlist & (1 << i)]
        if lr:
            regs.append("lr")
        return "push {{{}}}".format(", ".join(
            ["r{}".format(r) if isinstance(r, int) else r for r in regs]))


def _decode_cond_branch(insn, pc):
    cond = (insn >> 8) & 0xF
    offset = insn & 0xFF
    if offset & 0x80:
        offset -= 256
    target = pc + 4 + offset * 2
    cond_names = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
                  "hi", "ls", "ge", "lt", "gt", "le", "", ""]
    if cond == 0xE:
        return "b 0x{:x}".format(target)
    if cond == 0xF:
        return "svc #{:02x}".format(insn & 0xFF)
    return "b{cond} 0x{target:x}".format(cond=cond_names[cond], target=target)


def _decode_uncond_branch(insn, pc):
    offset = insn & 0x7FF
    if offset & 0x400:
        offset -= 0x800
    target = pc + 4 + offset * 2
    return "b 0x{:x}".format(target)


def decode_thumb16(insn, pc):
    """Decode a 16-bit Thumb instruction using top-down bit pattern matching."""
    hi5 = (insn >> 11) & 0x1F

    # Format 1-3: Shift / Add/Sub (000xx)
    if hi5 < 4:
        return _decode_shift_imm(insn, ["lsl", "lsr", "asr"][hi5 & 3], pc) if hi5 < 3 else _decode_add_sub(insn, pc)

    # Format 4: MOV immediate (00100)
    if hi5 == 4:
        return _decode_mov_imm(insn, pc)

    # Format 5: CMP immediate (00101)
    if hi5 == 5:
        return _decode_cmp_imm(insn, pc)

    # Format 6: ADD immediate (00110)
    if hi5 == 6:
        return _decode_add_imm(insn, pc)

    # Format 7: SUB immediate (00111)
    if hi5 == 7:
        return _decode_sub_imm(insn, pc)

    # Format 8: Data processing / High register ops (010xx)
    if hi5 == 8:
        hi8 = (insn >> 8) & 0xFF
        # MOV high register / BX / BLX (01000110 / 01000111)
        if (insn >> 8) & 0xFC == 0x44 or (insn >> 8) & 0xFC == 0x46:
            d = (insn >> 7) & 1
            rm = (insn >> 3) & 0xF
            rd = (insn & 7) | (d << 3)
            return "mov {rd}, {rm}".format(rd=reg(rd), rm=reg(rm))
        if (insn >> 8) & 0xFC == 0x47:
            rm = (insn >> 3) & 0xF
            if insn & 0x80:
                return "blx {rm}".format(rm=reg(rm))
            return "bx {rm}".format(rm=reg(rm))
        # ALU operations (010000 xx)
        if (insn >> 10) & 0x3F == 0x10:
            return _decode_alu_op(insn, pc)
        return "data_proc 0x{:04x}".format(insn)

    # Format 9: LDR [PC+imm] (01001)
    if hi5 == 9:
        return "ldr r{Rd}, [pc, #{imm}]".format(Rd=(insn >> 8) & 7, imm=((insn & 0xFF) << 2))

    # Format 10: STR/LDR register offset (0101x)
    if hi5 == 0xA:
        b = (insn >> 9) & 1
        return _decode_str_ldr(insn, "str" if b == 0 else "ldr", pc)
    if hi5 == 0xB:
        b = (insn >> 9) & 1
        return _decode_str_ldr(insn, "str" if b == 0 else "ldr", pc)

    # STRB/LDRB (01100/01101)
    if hi5 == 0xC:
        return _decode_strb_ldrb(insn, "strb", pc)
    if hi5 == 0xD:
        return _decode_strb_ldrb(insn, "ldrb", pc)

    # STRH/LDRH (01110/01111)
    if hi5 == 0xE:
        return _decode_strh_ldrh(insn, "strh", pc)
    if hi5 == 0xF:
        return _decode_strh_ldrh(insn, "ldrh", pc)

    # STR/LDR word [Rn, #imm5*4] (10000/10001)
    if hi5 == 0x10:
        return _decode_str_ldr(insn, "str", pc)
    if hi5 == 0x11:
        return _decode_str_ldr(insn, "ldr", pc)

    # Format 11: STR [SP+imm] (10010)
    if hi5 == 0x12:
        return "str r{Rd}, [sp, #{imm}]".format(Rd=(insn >> 8) & 7, imm=((insn & 0xFF) << 2))

    # LDR [SP+imm] (10011)
    if hi5 == 0x13:
        return "ldr r{Rd}, [sp, #{imm}]".format(Rd=(insn >> 8) & 7, imm=((insn & 0xFF) << 2))

    # Format 12: ADD Rd, SP/PC+imm (10100/10101)
    if hi5 == 0x14:
        return "add r{Rd}, sp, #{imm}".format(Rd=(insn >> 8) & 7, imm=((insn & 0xFF) << 2))
    if hi5 == 0x15:
        return "add r{Rd}, pc, #{imm}".format(Rd=(insn >> 8) & 7, imm=((insn & 0xFF) << 2))

    # PUSH (1011 0 10 x)
    hi7 = (insn >> 9) & 0x7F
    if hi7 == 0x5A:  # 1011010 = PUSH
        rlist = insn & 0xFF
        lr = (insn >> 8) & 1
        regs = [i for i in range(8) if rlist & (1 << i)]
        if lr:
            regs.append("lr")
        return "push {{{}}}".format(", ".join(["r{}".format(r) if isinstance(r, int) else r for r in regs]))

    # POP (1011 1 10 x)
    if hi7 == 0x5E:  # 1011110 = POP
        rlist = insn & 0xFF
        pc_bit = (insn >> 8) & 1
        regs = [i for i in range(8) if rlist & (1 << i)]
        if pc_bit:
            regs.append("pc")
        return "pop {{{}}}".format(", ".join(["r{}".format(r) if isinstance(r, int) else r for r in regs]))

    # SUB SP, SP, #imm7*4 (1011 0000 1 xxxxxxx) — (insn & 0xFF80) == 0xB080
    if (insn & 0xFF80) == 0xB080:
        imm7 = insn & 0x7F
        return "sub sp, sp, #{}".format(imm7 * 4)

    # ADD SP, SP, #imm7*4 (1011 0000 0 xxxxxxx) — (insn & 0xFF80) == 0xB000
    if (insn & 0xFF80) == 0xB000:
        imm7 = insn & 0x7F
        return "add sp, sp, #{}".format(imm7 * 4)

    # Miscellaneous instructions (10110) — hi5 = 0x16
    # SXTB, SXTH, UXTB, UXTH, REV, CPS, SETEND, CBZ/CBNZ, etc.
    if hi5 == 0x16:
        # CBZ / CBNZ — 1011 x0x1 xxxxxxxx
        if (insn & 0xF500) == 0xB100:
            rn = insn & 7
            i_bit = (insn >> 9) & 1
            imm5 = (insn >> 3) & 0x1F
            offset = (i_bit << 6) | (imm5 << 1)
            target = pc + 4 + offset
            if insn & 0x0800:
                return "cbnz r{}, 0x{:x}".format(rn, target & 0xFFFFFFFF)
            return "cbz r{}, 0x{:x}".format(rn, target & 0xFFFFFFFF)
        if (insn & 0xFFC0) == 0xB240:
            return "sxtb r{}, r{}".format(insn & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xB200:
            return "sxth r{}, r{}".format(insn & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xB2C0:
            return "uxtb r{}, r{}".format(insn & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xB280:
            return "uxth r{}, r{}".format(insn & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xBAC0:
            return "revh r{}, r{}".format(insn & 7, (insn >> 3) & 7)
        if (insn & 0xFF00) == 0xBA00:
            return "rev r{}, r{}".format(insn & 7, (insn >> 3) & 7)
        if (insn & 0xFF80) == 0xB680:
            return "cpsie {}".format(insn & 0x7F)
        if (insn & 0xFF80) == 0xB600:
            return "cpsid {}".format(insn & 0x7f)
        return "misc16 0x{:04x}".format(insn)

    # NOP / IT/ITE conditional execution blocks (1011 1111 xxxx xxxx)
    # hi5 = 0x17 (10111), hi8 = 0xBFxx
    if hi5 == 0x17:
        if insn == 0xBF00:
            return "nop"
        if (insn & 0xFF00) == 0xBF00:
            # IT block: firstcond in bits[7:4], mask in bits[3:0]
            firstcond = (insn >> 4) & 0xF
            mask = insn & 0xF
            cond_names = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
                          "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]
            cond_str = cond_names[firstcond] if firstcond < 14 else "??"
            # Build IT string from mask
            it_chars = ""
            for bit in range(4, 0, -1):
                if mask & (1 << bit):
                    it_chars += cond_str[:2] if firstcond < 14 else "??"
                else:
                    # Invert condition
                    inv = firstcond ^ 1
                    it_chars += cond_names[inv] if inv < 14 else "??"
            # Trim to actual length (mask determines number of instructions)
            it_len = 0
            m = mask
            while m & 1:
                it_len += 1
                m >>= 1
            # The IT instruction itself: IT{x{y{z}}}
            suffix = ""
            m = mask
            for bit_pos in range(4):
                if m & (1 << bit_pos):
                    if m & (1 << (bit_pos + 1)) or bit_pos == 0:
                        suffix += "t" if (mask & (1 << bit_pos)) else "e"
            # Simplified: just show IT with condition
            return "it {}".format(cond_str)
        # Other misc: CPS, SETEND, etc.
        if (insn & 0xFF80) == 0xB680:
            return "cpsie {}".format(insn & 0x7F)
        if (insn & 0xFF80) == 0xB600:
            return "cpsid {}".format(insn & 0x7F)
        if (insn & 0xFFC0) == 0xB240:
            return "sxtb r{}, r{}".format((insn >> 0) & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xB200:
            return "sxth r{}, r{}".format((insn >> 0) & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xB2C0:
            return "uxtb r{}, r{}".format((insn >> 0) & 7, (insn >> 3) & 7)
        if (insn & 0xFFC0) == 0xB280:
            return "uxth r{}, r{}".format((insn >> 0) & 7, (insn >> 3) & 7)
        if (insn & 0xFF00) == 0xBA00:
            return "rev r{}, r{}".format((insn >> 0) & 7, (insn >> 3) & 7)
        return "misc 0x{:04x}".format(insn)

    # Format 16: Conditional branch (1101 cond imm8)
    if (insn >> 12) == 0xD:
        return _decode_cond_branch(insn, pc)

    # Format 18: Unconditional branch B (11100 xxxxxxxxxxx) — hi5=0x1C
    if hi5 == 0x1C:
        return _decode_uncond_branch(insn, pc)

    # Format 19: BL prefix (11110) — suffix handled by caller
    if hi5 == 0x1E:
        return "bl <prefix>"
    if hi5 == 0x1F:
        return "bl <suffix>"

    return "unknown thumb-16 (0x{:04x})".format(insn)


def decode_thumb32(insn_hi, insn_lo, pc):
    """Decode a 32-bit Thumb-2 instruction."""
    hw1 = insn_hi
    hw2 = insn_lo

    op1 = (hw1 >> 11) & 0x1F
    op2 = (hw1 >> 4) & 0x7F

    # BL / BLX — only when suffix has bit[15:14]=11 (0x1E prefix + 0x1F suffix pattern)
    if op1 == 0x1E and (hw2 >> 14) == 3:
        s = (hw1 >> 10) & 1
        imm10 = hw1 & 0x3FF
        j1 = (hw2 >> 13) & 1
        j2 = (hw2 >> 11) & 1
        imm11 = hw2 & 0x7FF
        i1 = 1 - (j1 ^ s)
        i2 = 1 - (j2 ^ s)
        offset = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
        # Sign extend from 25 bits
        if s:
            offset -= (1 << 25)
        target = pc + 4 + offset
        if hw2 & 0x1000:  # bit 12 = 1 → BL (not BLX)
            return "bl 0x{:x}".format(target & 0xFFFFFFFF), True
        return "blx 0x{:x}".format((target & 0xFFFFFFFF) & ~1), True

    # PUSH.W / POP.W / STM / LDM
    result = _decode_thumb32_push_pop(hw1, hw2, pc)
    if result:
        return result, True

    # MOVW Rd, #imm16 — (hw1 & 0xFBF0) == 0xF240
    if (hw1 & 0xFBF0) == 0xF240:
        i_bit = (hw1 >> 10) & 1
        imm4 = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        imm3 = (hw2 >> 12) & 7
        imm8 = hw2 & 0xFF
        imm16 = (i_bit << 11) | (imm4 << 12) | (imm3 << 8) | imm8
        return "movw {}, #0x{:x}".format(reg(rd), imm16), True

    # MOVT Rd, #imm16 — (hw1 & 0xFBF0) == 0xF2C0
    if (hw1 & 0xFBF0) == 0xF2C0:
        i_bit = (hw1 >> 10) & 1
        imm4 = hw1 & 0xF
        rd = (hw2 >> 8) & 0xF
        imm3 = (hw2 >> 12) & 7
        imm8 = hw2 & 0xFF
        imm16 = (i_bit << 11) | (imm4 << 12) | (imm3 << 8) | imm8
        return "movt {}, #0x{:x}".format(reg(rd), imm16), True

    # STRD / LDRD (dual register load/store)
    # Encoding: 1110 1001 P U 1 W L Rn Rt Rt2 imm8
    # STRD: L=0, LDRD: L=1
    # Mask: check bits 15:12=1110, 11:10=10, 7=1, 4=0
    # (hw1 & 0xFE50) == 0xE840 for STRD (L=0)
    # (hw1 & 0xFE50) == 0xE850 for LDRD (L=1)
    # Combined: (hw1 & 0xFE40) == 0xE840 (mask out L, P, U, W, Rn)
    if (hw1 & 0xFE40) == 0xE840:
        rn = hw1 & 0xF
        rt = (hw2 >> 12) & 0xF
        rt2 = (hw2 >> 8) & 0xF
        imm8 = hw2 & 0xFF
        p_bit = (hw1 >> 9) & 1
        u_bit = (hw1 >> 8) & 1
        w_bit = (hw1 >> 6) & 1
        l_bit = (hw1 >> 5) & 1
        offset = imm8 * 4
        op = "ldrd" if l_bit else "strd"
        sign = "" if u_bit else "-"
        wback = "!" if w_bit else ""
        if offset == 0:
            return "{} {}, {}, [{}{}]".format(op, reg(rt), reg(rt2), reg(rn), wback), True
        return "{} {}, {}, [{}{}, #{}{}]".format(op, reg(rt), reg(rt2), reg(rn), wback, sign, offset), True

    # LDR.W / STR.W / LDRB.W / STRB.W
    result = _decode_thumb32_ldr_str(hw1, hw2, pc)
    if result:
        return result, True

    # LDRH.W / STRH.W
    result = _decode_thumb32_ldrh_strh(hw1, hw2, pc)
    if result:
        return result, True

    # MOV.W / SUB.W / ADD.W / CMP.W / ORR / AND / EOR / BIC
    result = _decode_thumb32_mov_sub_add_cmp(hw1, hw2, pc)
    if result:
        return result, True

    # Conditional branch B<c>.W
    result = _decode_thumb32_branch(hw1, hw2, pc)
    if result:
        return result, True

    # Miscellaneous: MRS, MSR, DMB, SXTB, SXTH, UXTB, UXTH, etc.
    result = _decode_thumb32_misc(hw1, hw2, pc)
    if result:
        return result, True

    return "thumb2 0x{:04x}{:04x}".format(hw1, hw2), True


def parse_elf_sections(axf_path):
    """Parse ELF section headers. Delegates to common.py."""
    return _parse_elf_sections(axf_path)


def find_section(sections, addr):
    """Find the ELF section containing the given address."""
    for name, sec_addr, sec_offset, sec_size in sections:
        if sec_addr <= addr < sec_addr + sec_size:
            return name, sec_addr, sec_offset, sec_size
    return None


def read_bytes_from_axf(axf_path, section_offset, addr_offset, size):
    """Read raw bytes from AXF file at the given file offset."""
    with open(axf_path, 'rb') as f:
        f.seek(section_offset + addr_offset)
        return f.read(size)


def disassemble_thumb(data, base_addr, highlight_addr=None):
    """Disassemble Thumb instruction stream from raw bytes."""
    lines = []
    i = 0
    while i < len(data) - 1:
        pc = base_addr + i
        hw = struct.unpack_from('<H', data, i)[0]

        # Thumb-2 32-bit: hw[15:11] = 11101/11110/11111 (0x1D/0x1E/0x1F)
        hi5 = (hw >> 11) & 0x1F
        is_32bit = hi5 >= 0x1D

        if is_32bit and i + 3 < len(data):
            hw2 = struct.unpack_from('<H', data, i + 2)[0]
            # BL/BLX pair — use _decode_bl_pair for correct S bit handling
            if hi5 == 0x1E and (hw2 >> 14) == 3:
                asm = _decode_bl_pair(hw, hw2, pc)
            else:
                asm, _ = decode_thumb32(hw, hw2, pc)
            raw = "0x{:04x} {:04x}".format(hw, hw2)
            marker = " ★" if (highlight_addr and pc <= highlight_addr < pc + 4) else ""
            lines.append("0x{addr:08x}:  {raw:12s} {asm}{marker}".format(
                addr=pc, raw=raw, asm=asm, marker=marker))
            i += 4
        else:
            asm = decode_thumb16(hw, pc)
            raw = "0x{:04x}".format(hw)
            marker = " ★" if (highlight_addr and pc <= highlight_addr < pc + 2) else ""
            lines.append("0x{addr:08x}:  {raw:12s} {asm}{marker}".format(
                addr=pc, raw=raw, asm=asm, marker=marker))
            i += 2

    return lines


def disassemble_arm(data, base_addr, highlight_addr=None):
    """Disassemble ARM instruction stream (simplified - shows raw hex and basic decode)."""
    lines = []
    i = 0
    while i + 3 < len(data):
        pc = base_addr + i
        insn = struct.unpack_from('<I', data, i)[0]
        marker = " ★" if (highlight_addr and pc <= highlight_addr < pc + 4) else ""

        cond = (insn >> 28) & 0xF
        cond_names = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
                      "hi", "ls", "ge", "lt", "gt", "le", "", ""]

        # Basic ARM decode
        asm = _decode_arm_basic(insn, cond_names[cond])

        lines.append("0x{addr:08x}:  0x{insn:08x}  {asm}{marker}".format(
            addr=pc, insn=insn, asm=asm, marker=marker))
        i += 4
    return lines


def _decode_arm_basic(insn, cond):
    """Basic ARM instruction decode."""
    op = (insn >> 25) & 7

    if insn == 0xE12FFF1E:
        return "bx lr"
    if insn & 0x0F000000 == 0x0A000000:
        offset = insn & 0x00FFFFFF
        if offset & 0x800000:
            offset |= 0xFF000000
            offset -= 0x100000000
        return "b{cond} 0x{target:x}".format(cond=cond, target=0)  # simplified

    if insn & 0x0FFFFFF0 == 0x012FFF10:
        rm = insn & 0xF
        return "bx{cond} {rm}".format(cond=cond, rm=reg(rm))

    if op == 0:
        return "data 0x{:08x}".format(insn)
    elif op == 1:
        rd = (insn >> 12) & 0xF
        rn = (insn >> 16) & 0xF
        imm = insn & 0xFF
        rotate = ((insn >> 8) & 0xF) * 2
        if rotate:
            imm = ((imm >> rotate) | (imm << (32 - rotate))) & 0xFFFFFFFF
        opcode = (insn >> 21) & 0xF
        ops = ["and", "eor", "sub", "rsb", "add", "adc", "sbc", "rsc",
               "tst", "teq", "cmp", "cmn", "orr", "mov", "bic", "mvn"]
        s = "s" if insn & (1 << 20) else ""
        if opcode in (8, 9, 10, 11):
            return "{op}{cond}{s} {rn}, #0x{imm:x}".format(
                op=ops[opcode], cond=cond, s=s, rn=reg(rn), imm=imm)
        if opcode in (13, 15):
            return "{op}{cond}{s} {rd}, #0x{imm:x}".format(
                op=ops[opcode], cond=cond, s=s, rd=reg(rd), imm=imm)
        return "{op}{cond}{s} {rd}, {rn}, #0x{imm:x}".format(
            op=ops[opcode], cond=cond, s=s, rd=reg(rd), rn=reg(rn), imm=imm)
    elif op == 2:
        return "ld/st 0x{:08x}".format(insn)
    elif op == 4:
        rd = (insn >> 12) & 0xF
        imm = insn & 0xFFF
        rn = (insn >> 16) & 0xF
        l = "ldr" if insn & (1 << 20) else "str"
        b = "b" if insn & (1 << 22) else ""
        return "{l}{b} {rd}, [{rn}, #{imm}]".format(l=l, b=b, rd=reg(rd), rn=reg(rn), imm=imm)
    elif op == 5:
        rd = (insn >> 12) & 0xF
        rn = (insn >> 16) & 0xF
        l = "ldr" if insn & (1 << 20) else "str"
        return "{l} {rd}, [{rn}]".format(l=l, rd=reg(rd), rn=reg(rn))
    else:
        return "op{} 0x{:08x}".format(op, insn)


def decode_map_file(map_path, target_addr):
    """Look up a function name from a MAP file for the given address. Uses common.py."""
    from common import parse_map_file, lookup_address
    entries, code_addrs = parse_map_file(map_path)
    result = lookup_address(entries, target_addr, code_addrs)
    if result:
        addr, name, size, section, is_thumb = result
        return (name, addr, size)
    return None


def main():
    parser = argparse.ArgumentParser(
        description='AXF crash disassembler for ARM/Thumb instruction set')
    parser.add_argument('axf_file', help='Path to AXF/ELF binary file')
    parser.add_argument('--address', default='0',
                        help='Target address (hex, e.g. 0x7e880040)')
    parser.add_argument('--size', type=int, default=32,
                        help='Number of bytes to disassemble (default: 32)')
    parser.add_argument('--arch', choices=['thumb', 'arm'], default='thumb',
                        help='Instruction set (default: thumb)')
    parser.add_argument('--map', help='MAP file for function lookup')
    parser.add_argument('--context', type=int, default=8,
                        help='Extra bytes before target address (default: 8)')
    parser.add_argument('--list-sections', action='store_true',
                        help='List ELF sections and exit')

    args = parser.parse_args()

    target_addr = int(args.address, 16) & ~1  # Clear Thumb bit for code alignment

    # Parse ELF sections
    sections = parse_elf_sections(args.axf_file)

    if args.list_sections:
        print("ELF Sections (name, addr, offset, size):")
        for name, addr, offset, size in sections:
            print("  {:40s} addr=0x{:08x} off=0x{:08x} size=0x{:x} ({})".format(
                name, addr, offset, size, size))
        return

    # Find section containing target address
    start_addr = target_addr - args.context
    section_info = find_section(sections, start_addr)
    if not section_info:
        # Try just the target address
        section_info = find_section(sections, target_addr)
        if not section_info:
            print("Error: Address 0x{:08x} not found in any ELF section".format(target_addr))
            print("\nAvailable sections:")
            for name, addr, offset, size in sections:
                print("  {:40s} 0x{:08x} - 0x{:08x}".format(name, addr, addr + size))
            sys.exit(1)
        start_addr = target_addr

    sec_name, sec_addr, sec_offset, sec_size = section_info
    print("Section: {} (0x{:08x} - 0x{:08x})".format(sec_name, sec_addr, sec_addr + sec_size))

    # MAP file lookup
    if args.map:
        match = decode_map_file(args.map, target_addr)
        if match:
            func_name, func_addr, func_size = match
            offset_in_func = target_addr - (func_addr & ~1)
            print("Function: {} (0x{:x}, {} bytes, offset +{})".format(
                func_name, func_addr, func_size, offset_in_func))
            # Auto-expand to show full function
            func_start = func_addr & ~1
            func_end = func_start + func_size
            read_start = min(start_addr, func_start)
            read_size = max(args.size + args.context, func_end - read_start)
            # Recalculate section
            sec_info2 = find_section(sections, func_start)
            if sec_info2:
                sec_name, sec_addr, sec_offset, sec_size = sec_info2
                addr_offset = read_start - sec_addr
                raw = read_bytes_from_axf(args.axf_file,
                                          sec_offset, addr_offset, read_size)
                print("\n--- Full function disassembly ---")
                if args.arch == 'thumb':
                    lines = disassemble_thumb(raw, read_start, target_addr)
                else:
                    lines = disassemble_arm(raw, read_start, target_addr)
                for line in lines:
                    print(line)
                return

    # Read bytes around target address
    addr_offset = start_addr - sec_addr
    raw = read_bytes_from_axf(args.axf_file, sec_offset, addr_offset,
                              args.size + args.context)

    print("\n--- Disassembly (0x{:08x} - 0x{:08x}) ---".format(
        start_addr, start_addr + len(raw)))

    if args.arch == 'thumb':
        lines = disassemble_thumb(raw, start_addr, target_addr)
    else:
        lines = disassemble_arm(raw, start_addr, target_addr)

    for line in lines:
        print(line)


if __name__ == '__main__':
    main()
