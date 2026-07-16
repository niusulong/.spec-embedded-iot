#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 纯 Python Thumb/Thumb-2 反汇编器（无 capstone 依赖）。

QCX216 工具链无 ARM binutils，本机也无 capstone。崩溃分析需要看清触发点附近的
指令（如 ASSERT 的 B . 死循环、HardFault 的崩溃指令），本模块用纯 Python 实现
Thumb-2 反汇编，覆盖**调试高频指令**：

  16 位：PUSH/POP、MOV/CMP/ADD/SUB（imm8/reg/high）、LDR/STR/LDRB/STRB/LDRH/STRH
        （imm/reg/imm5 偏移）、LSL/LSR/ASR、B/B<cond>/BX/BLX、CBZ/CBNZ、LDR literal、
        ADD/SUB SP、ADR、STMIA/LDMIA、SVC、IT
  32 位：PUSH.W/POP.W、BL/B.W、LDR.W/LDRB.W/LDRSB.W/STR.W/STRB.W、MOVW/MOVT/MOV.W、
        ADD.W/SUB.W、CMP.W、MRS/MSR、LDR.W literal

未识别的指令降级为 `.short 0xXXXX` / `.word 0xXXXXXXXX`，绝不崩溃。
16/32 位判定按 ARMv7-M 规范：(hw1 & 0xE000==0xE000) and (hw1 & 0x1800!=0) → 32 位。

已用 OsaCreateFastSymbol 反汇编流验证（PUSH.W / MOV / CBZ / LDR / B<cond> / B . 等）。
"""
from qcx216_common import u8

# 可选 capstone 后端（pip install capstone）：有则优先用，反汇编更准（正确解 ITE/MSR/宽指令，
# 避免纯 Python 漏解条件块导致误判，如 OsaCreateFastSignal 的 poolId 动态选择）。无则降级纯 Python。
try:
    from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB
    _CS = Cs(CS_ARCH_ARM, CS_MODE_THUMB)
    HAS_CAPSTONE = True
except Exception:
    HAS_CAPSTONE = False

REG = ["r0", "r1", "r2", "r3", "r4", "r5", "r6", "r7",
       "r8", "r9", "r10", "r11", "r12", "sp", "lr", "pc"]
COND = ["eq", "ne", "cs", "cc", "mi", "pl", "vs", "vc",
        "hi", "ls", "ge", "lt", "gt", "le", "al", "nv"]


def _R(idx: int) -> str:
    return REG[idx]


def _signext(value: int, bits: int) -> int:
    if value & (1 << (bits - 1)):
        return value - (1 << bits)
    return value


def _reglist(bits_val: int) -> str:
    """把位图转成 {r0,r1,...,lr,pc} 文本。"""
    regs = [REG[i] for i in range(16) if bits_val & (1 << i)]
    return "{" + ",".join(regs) + "}"


class ThumbDisasm:
    """Thumb-2 反汇编器。mem 为按地址读 u8 的函数（DumpReader.u8 即可）。

    若 capstone 可用，disasm_one 优先用 capstone（更准，正确解 ITE/MSR/宽指令），
    输出统一转大写 + 去地址 # 前缀以兼容纯 Python 格式；不可用则降级纯 Python。
    """

    def __init__(self, mem, use_capstone=True):
        self.mem = mem
        self.use_cs = use_capstone and HAS_CAPSTONE

    def _u16(self, addr):
        lo = self.mem(addr)
        hi = self.mem(addr + 1)
        if lo is None or hi is None:
            return None
        return lo | (hi << 8)

    @staticmethod
    def _is32(hw1: int) -> bool:
        return (hw1 & 0xE000 == 0xE000) and (hw1 & 0x1800 != 0)

    def disasm_one(self, addr: int):
        """反汇编一条指令，返回 (size, text)。size=0 表示无法读取。"""
        # capstone 后端：读 8 字节 buffer，取首条
        if self.use_cs:
            buf = bytes((self.mem(addr + i) or 0) for i in range(8))
            try:
                ins = next(_CS.disasm(buf, addr))
                txt = (ins.mnemonic + " " + ins.op_str).strip().upper()
                # 统一格式：地址立即数 #0x.. 去掉 #（与纯 Python 一致），数值立即数 # 保留
                txt = txt.replace("#0X", "0X")
                return (ins.size, txt)
            except StopIteration:
                pass
        # 纯 Python 降级
        hw1 = self._u16(addr)
        if hw1 is None:
            return (0, "<?>")
        if self._is32(hw1):
            hw2 = self._u16(addr + 2)
            if hw2 is None:
                return (2, f".short 0x{hw1:04X}")
            return (4, self._d32(hw1, hw2, addr))
        return (2, self._d16(hw1, addr))

    # ---------------- 16-bit ----------------
    def _d16(self, hw: int, addr: int) -> str:
        g = hw >> 13  # 顶层 3 位
        try:
            if g == 0b000:
                return self._g0_shift_addsub(hw)
            if g == 0b001:
                op = (hw >> 11) & 3
                rd = (hw >> 8) & 7
                imm8 = hw & 0xFF
                nm = ["MOVS", "CMP", "ADDS", "SUBS"][op]
                return f"{nm} {_R(rd)}, #{imm8}"
            if g == 0b010:
                return self._g2_dataproc_special(hw, addr)
            if g == 0b011:  # STR/LDR imm5 (word)
                op = (hw >> 11) & 1
                imm5 = (hw >> 6) & 0x1F
                rn = (hw >> 3) & 7
                rd = hw & 7
                nm = "LDR" if op == 0 else "STR"
                return f"{nm} {_R(rd)}, [{_R(rn)}, #{imm5 * 4}]"
            if g == 0b100:  # STRH/LDRH/LDRSB/LDRSH imm5
                op = (hw >> 11) & 3
                imm5 = (hw >> 6) & 0x1F
                rn = (hw >> 3) & 7
                rd = hw & 7
                nm = ["STRH", "LDRH", "LDRSB", "LDRSH"][op]
                # STRH/LDRH 半字偏移 ×2；LDRSB/LDRSH 字节偏移 ×1
                scale = 2 if op < 2 else 1
                return f"{nm} {_R(rd)}, [{_R(rn)}, #{imm5 * scale}]"
            if g == 0b101:
                return self._g5_spcbx(hw, addr)
            if g == 0b110:
                if hw < 0xC800:  # 0xC000-0xC7FF STMIA
                    rn = (hw >> 8) & 7
                    return f"STMIA {_R(rn)}!, {_reglist(hw & 0xFF)}"
                if hw < 0xD000:  # 0xC800-0xCFFF LDMIA
                    rn = (hw >> 8) & 7
                    return f"LDMIA {_R(rn)}!, {_reglist(hw & 0xFF)}"
                # 0xD000-0xD7FF B<cond> (0xD800-0xDFFF 罕见)
                cond = (hw >> 8) & 0xF
                imm8 = hw & 0xFF
                tgt = addr + 4 + _signext(imm8, 8) * 2
                if cond == 0xE:
                    return f"UDF #0x{imm8:X}"
                if cond == 0xF:
                    return f"SVC #0x{imm8:X}"
                return f"B{COND[cond].upper()} 0x{tgt:X}"
            # g == 0b111: 0xE000-0xE7FF B(T2 unconditional)
            imm11 = hw & 0x7FF
            tgt = addr + 4 + _signext(imm11, 11) * 2
            return f"B 0x{tgt:X}"
        except Exception:
            return f".short 0x{hw:04X}"

    def _g0_shift_addsub(self, hw: int) -> str:
        if hw < 0x1800:  # 0x0000-0x17FF shift imm5
            op = (hw >> 11) & 3
            imm5 = (hw >> 6) & 0x1F
            rm = (hw >> 3) & 7
            rd = hw & 7
            nm = ["LSLS", "LSRS", "ASRS"][op]
            if imm5 == 0:
                if op == 0:
                    return f"MOVS {_R(rd)}, {_R(rm)}"
                return f"{nm} {_R(rd)}, {_R(rm)}, #32"
            return f"{nm} {_R(rd)}, {_R(rm)}, #{imm5}"
        # 0x1800-0x1FFF ADD/SUB (reg / imm3)
        sub = (hw >> 9) & 1
        imm3 = (hw >> 6) & 0x7
        rn = (hw >> 3) & 7
        rd = hw & 7
        nm = "SUBS" if sub else "ADDS"
        if (hw >> 10) & 1:  # imm3 形式
            return f"{nm} {_R(rd)}, {_R(rn)}, #{imm3}"
        return f"{nm} {_R(rd)}, {_R(rn)}, {_R(imm3)}"

    def _g2_dataproc_special(self, hw: int, addr: int) -> str:
        if hw < 0x4400:  # 0x4000-0x43FF data-processing
            op = (hw >> 6) & 0xF
            rs = (hw >> 3) & 7
            rd = hw & 7
            tab = ["ANDS", "EORS", "LSLS", "LSRS", "ASRS", "ADCS", "SBCS", "RORS",
                   "TST", "RSBS", "CMP", "CMN", "ORRS", "MULS", "BICS", "MVNS"]
            nm = tab[op]
            if nm in ("RSBS",):
                return f"{nm} {_R(rd)}, {_R(rs)}, #0"
            if nm in ("TST", "CMP", "CMN"):
                return f"{nm} {_R(rd)}, {_R(rs)}"
            return f"{nm} {_R(rd)}, {_R(rs)}"
        if hw < 0x4700:  # 0x4400-0x46FF ADD/MOV high register / CMP
            op = (hw >> 8) & 3
            dn = (hw >> 7) & 1
            rm = (hw >> 3) & 0xF
            rdn = (dn << 3) | (hw & 7)
            if op == 0:
                return f"ADDS {_R(rdn)}, {_R(rm)}"
            if op == 1:
                return f"CMP {_R(rdn)}, {_R(rm)}"
            return f"MOV {_R(rdn)}, {_R(rm)}"
        if hw < 0x4780:  # 0x4700 BX Rm
            rm = (hw >> 3) & 0xF
            return f"BX {_R(rm)}"
        if hw < 0x4800:  # 0x4780 BLX Rm
            rm = (hw >> 3) & 0xF
            return f"BLX {_R(rm)}"
        # 0x4800-0x4FFF LDR literal
        rt = (hw >> 8) & 7
        imm8 = hw & 0xFF
        base = (addr + 4) & ~3
        tgt = base + imm8 * 4
        return f"LDR {_R(rt)}, [pc, #{imm8 * 4}]  ; =0x{tgt:X}"

    def _g5_spcbx(self, hw: int, addr: int) -> str:
        # 全部用精确位掩码判定，避免 PUSH/CBZ 等范围重叠误判
        if (hw & 0xF800) == 0xA000:  # 0xA000-0xA7FF ADR (ADD Rd, PC, imm8)
            rd = (hw >> 8) & 7
            imm8 = hw & 0xFF
            tgt = ((addr + 4) & ~3) + imm8 * 4
            return f"ADR {_R(rd)}, 0x{tgt:X}"
        if (hw & 0xF800) == 0xA800:  # 0xA800-0xAFFF ADD Rd, sp, imm8
            rd = (hw >> 8) & 7
            imm8 = hw & 0xFF
            return f"ADD {_R(rd)}, sp, #{imm8 * 4}"
        if (hw & 0xFF80) == 0xB000:  # ADD sp, imm7
            return f"ADD sp, #{(hw & 0x7F) * 4}"
        if (hw & 0xFF80) == 0xB080:  # SUB sp, imm7
            return f"SUB sp, #{(hw & 0x7F) * 4}"
        # CBZ/CBNZ: (hw & 0xF500)==0xB100 命中 0xB1xx 与 0xB9xx，op=bit11 区分
        if (hw & 0xF500) == 0xB100:
            op = (hw >> 11) & 1
            i = (hw >> 9) & 1
            imm5 = (hw >> 3) & 0x1F
            rn = hw & 7
            tgt = addr + 4 + ((i << 6) | (imm5 << 1))
            return f"{'CBNZ' if op else 'CBZ'} {_R(rn)}, 0x{tgt:X}"
        if 0xB200 <= hw < 0xB400:  # SXTH/SXTB/UXTH/UXTB
            op = (hw >> 6) & 3
            rm = (hw >> 3) & 7
            rd = hw & 7
            return f"{['SXTH', 'SXTB', 'UXTH', 'UXTB'][op]} {_R(rd)}, {_R(rm)}"
        if (hw & 0xFE00) == 0xB400:  # PUSH (0xB400-0xB5FF)
            return f"PUSH {_reglist((hw & 0xFF) | ((hw & 0x100) << 6))}"
        if (hw & 0xFE00) == 0xBC00:  # POP (0xBC00-0xBDFF)
            return f"POP {_reglist((hw & 0xFF) | ((hw & 0x100) << 7))}"
        if (hw & 0xFFC0) == 0xBA00:  # REV/REV16/REVSH
            op = (hw >> 6) & 3
            rm = (hw >> 3) & 7
            rd = hw & 7
            return f"{['REV', 'REV16', 'REVSH'][op]} {_R(rd)}, {_R(rm)}"
        if (hw & 0xFF00) == 0xBF00:  # IT / NOP / hints
            hints = {0xBF00: "NOP", 0xBF10: "YIELD", 0xBF20: "WFE",
                     0xBF30: "WFI", 0xBF40: "SEV"}
            if hw in hints:
                return hints[hw]
            firstcond = (hw >> 4) & 0xF
            mask = hw & 0xF
            if mask == 0:
                return "NOP"
            return f"IT{COND[firstcond]}"
        return f".short 0x{hw:04X}"

    # ---------------- 32-bit ----------------
    def _d32(self, hw1: int, hw2: int, addr: int) -> str:
        instr = (hw1 << 16) | hw2
        try:
            # PUSH.W / POP.W（精确匹配首半字）
            if hw1 == 0xE92D:
                return f"PUSH.W {_reglist((hw2 & 0x1FFF) | (hw2 & 0x4000))}"
            if hw1 == 0xE8BD:
                return f"POP.W {_reglist(hw2 & 0xFFFF)}"

            rn = hw1 & 0xF
            rt = (hw2 >> 12) & 0xF
            imm12 = hw2 & 0xFFF
            # 立即数偏移的加载/存储（mask 0xFFF0，Rn 在 bits[3:0]）
            ls_tab = {0xF8D0: "LDR.W", 0xF8C0: "STR.W", 0xF890: "LDRB.W",
                      0xF880: "STRB.W", 0xF990: "LDRSB.W", 0xF9D0: "LDRSH.W",
                      0xF8B0: "LDRH.W", 0xF8A0: "STRH.W", 0xF8F0: "LDRD.W", 0xF840: "STRD.W"}
            for opval, nm in ls_tab.items():
                if (hw1 & 0xFFF0) == opval:
                    return f"{nm} {_R(rt)}, [{_R(rn)}, #{imm12}]"

            # BL / B.W / B<cond>.W（首半字 0xF000-0xF7FF，按 hw2[15:14] 区分）
            if (hw1 & 0xF800) == 0xF000:
                if (hw2 & 0xD000) == 0xD000:
                    return f"BL 0x{self._bl_target(hw1, hw2, addr):X}"
                if (hw2 & 0xD000) == 0x9000:
                    return f"B.W 0x{self._b_target(hw1, hw2, addr):X}"
                if (hw2 & 0xD000) == 0x8000:
                    cond = (hw1 >> 6) & 0xF
                    return f"B{COND[cond]}.W 0x{self._b_target(hw1, hw2, addr):X}"

            # MOV.W Rd, #imm (F04F)
            if (hw1 & 0xFFEF) == 0xF04F:
                return f"MOV.W {_R(rt)}, #{hw2 & 0xFF}"
            # MOVW / MOVT（精简展示原始编码）
            if (hw1 & 0xFBF0) == 0xF240:
                return f"MOVW ... ; 0x{instr:08X}"
            if (hw1 & 0xFBF0) == 0xF2C0:
                return f"MOVT ... ; 0x{instr:08X}"
            # CBZ/CBNZ.W（F1B0）—— 罕见，留兜底
            return f".word 0x{instr:08X}"
        except Exception:
            return f".word 0x{instr:08X}"

    @staticmethod
    def _bl_target(hw1, hw2, addr):
        s = (hw1 >> 10) & 1
        imm10 = hw1 & 0x3FF
        j1 = (hw2 >> 13) & 1
        j2 = (hw2 >> 11) & 1
        imm11 = hw2 & 0x7FF
        i1 = 1 - (j1 ^ s)
        i2 = 1 - (j2 ^ s)
        off = (s << 24) | (i1 << 23) | (i2 << 22) | (imm10 << 12) | (imm11 << 1)
        off = _signext(off, 25)
        return (addr + 4 + off) & 0xFFFFFFFF

    @staticmethod
    def _b_target(hw1, hw2, addr):
        s = (hw1 >> 10) & 1
        imm6 = hw1 & 0x3F
        j1 = (hw2 >> 13) & 1
        j2 = (hw2 >> 11) & 1
        imm11 = hw2 & 0x7FF
        i1 = 1 - (j1 ^ s)
        i2 = 1 - (j2 ^ s)
        off = (s << 24) | (i1 << 23) | (i2 << 22) | (imm6 << 16) | (imm11 << 1)
        off = _signext(off, 25)
        return (addr + 4 + off) & 0xFFFFFFFF

    # ---------------- 区间反汇编 ----------------
    def disasm_around(self, center: int, before_words: int = 4, after_words: int = 6):
        """反汇编 center 前后。返回 [(addr, size, text, is_center)]。

        capstone 模式下用连续反汇编（从 start 整段解），保留 IT 块上下文，正确显示
        条件后缀（如 ITE LS 块内的 movls/movhi）；纯 Python 用单条循环。
        """
        start = (center & ~1) - max(0, (before_words - 1) * 2)
        if start < 0:
            start = 0
        start &= ~1
        end_limit = (center & ~1) + after_words * 4 + 4

        if self.use_cs:
            blen = min(end_limit - start + 8, 0x120)
            buf = bytes((self.mem(start + i) or 0) for i in range(blen))
            out = []
            for ins in _CS.disasm(buf, start):
                if ins.address >= end_limit:
                    break
                txt = (ins.mnemonic + " " + ins.op_str).strip().upper().replace("#0X", "0X")
                out.append((ins.address, ins.size, txt, ins.address == (center & ~1)))
                if len(out) > (before_words + after_words + 4):
                    break
            if out:
                return out

        # 纯 Python 单条循环
        out = []
        addr = start
        guard = 0
        while addr < end_limit and guard < 64:
            guard += 1
            size, text = self.disasm_one(addr)
            if size == 0:
                break
            is_c = (addr == (center & ~1))
            out.append((addr, size, text, is_c))
            addr += size
            if addr > end_limit + 8:
                break
        return out

    def format_around(self, center: int, before_words: int = 4, after_words: int = 6,
                      sym_resolver=None) -> str:
        """格式化 center 附近反汇编为多行文本。"""
        rows = self.disasm_around(center, before_words, after_words)
        lines = []
        for a, sz, text, is_c in rows:
            mark = ">>" if is_c else "  "
            extra = ""
            # 对跳转目标补充符号
            if sym_resolver and ("0x" in text):
                import re
                m = re.search(r"0x([0-9A-Fa-f]+)", text)
                if m:
                    ta = int(m.group(1), 16)
                    loc = sym_resolver(ta)
                    if loc and loc.get("symbol"):
                        extra = f"  ; {loc['symbol']}"
            lines.append(f"  {mark} 0x{a:08X}:  {text}{extra}")
        return "\n".join(lines)


def find_assert_failure_point(dis, func_addr, max_instr=200):
    """推理 assert 的真实失败点（遍历函数内所有 assert 点）。

    Unisoc assert 宏典型模式（已从 OsaCreateFastSignal 验证）：
        BL  <失败点函数>        ← 返回 r0 (0=失败)
        MOV rN, r0
        CBZ/CBNZ r0, <success>  ← r0 != 0 跳过 assert
        MOVS r1, #<line>        ← assert 行号
        BL   excepHardFaultHandler
        B    .                  ← 死循环

    返回所有 assert 点列表：[(bl_tgt, bl_txt, cbz_txt, bl_addr, assert_line), ...]
    一个函数可能有多个 assert（如 OsaCreateFastSignal 有 line 135 参数检查 + line 146 分配失败）。
    """
    instrs = []
    addr = func_addr & ~1
    for _ in range(max_instr):
        sz, txt = dis.disasm_one(addr)
        if sz == 0:
            break
        instrs.append((addr, txt))
        if "POP" in txt and "pc" in txt:    # 函数尾
            break
        addr += sz

    results = []
    for i, (a, txt) in enumerate(instrs):
        if not txt.startswith("B 0x"):
            continue
        try:
            tgt = int(txt.split()[1], 16)
        except (IndexError, ValueError):
            continue
        if tgt != a:           # 只看 B self 死循环
            continue
        # 往前回溯：找 assert_line(MOVS r1,#N) + CBZ/CBNZ r0 + BL 失败点
        assert_line = cbz_txt = bl_info = None
        for j in range(i - 1, max(0, i - 15), -1):
            tj = instrs[j][1]
            if assert_line is None and "MOVS r1, #" in tj:
                try:
                    assert_line = int(tj.split("#")[1], 0)
                except ValueError:
                    pass
            if cbz_txt is None and ("CBZ r0" in tj or "CBNZ r0" in tj):
                cbz_txt = tj
                for k in range(j - 1, max(0, j - 6), -1):
                    tk = instrs[k][1]
                    if tk.startswith("BL 0x"):
                        try:
                            bl_tgt = int(tk.split()[1], 16)
                            bl_info = (bl_tgt, tk, instrs[k][0])
                        except (IndexError, ValueError):
                            pass
                        break
            if bl_info:
                break
        if bl_info:
            results.append((bl_info[0], bl_info[1], cbz_txt, bl_info[2], assert_line))
    return results

