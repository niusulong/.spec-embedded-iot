#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Cortex-M3 fault 寄存器解码 + 异常栈帧解析。

QCX216 的 SCB fault 寄存器（CFSR/HFSR/MMFAR/BFAR @0xE000ED28 等）不在 dump 里
（dump 仅覆盖到 0x540000），因此 HardFault 的 fault 值必须从 excepInfoStore 转储
里找。本模块提供纯解码函数：给定 fault 寄存器值，输出可读的原因列表。

异常栈帧（硬件自动压栈的 8 个字）也在此解析，用于 HardFault 时还原崩溃点寄存器。
注意：ASSERT 是软件触发的（无硬件栈帧），栈帧解析仅在 HardFault 场景适用。
"""
from qcx216_common import u32

# Cortex-M 异常栈帧寄存器顺序（低地址→高地址）
FRAME_REGS = ["R0", "R1", "R2", "R3", "R12", "LR", "PC", "xPSR"]


def parse_exception_frame(data: bytes, sp: int):
    """解析 Cortex-M 异常栈帧（SP 指向 R0）。返回 {reg: value} 或 None。"""
    out = {}
    for i, nm in enumerate(FRAME_REGS):
        v = u32(data, sp + i * 4)
        if v is None:
            return None
        out[nm] = v
    return out


def decode_exc_return(lr):
    """解码 EXC_RETURN（异常返回值，存在 LR）。"""
    lr &= 0xFFFFFFFF
    tab = {
        0xFFFFFFF1: "Handler mode, return to MSP (nested exception)",
        0xFFFFFFF9: "Thread mode, return to MSP (main stack)",
        0xFFFFFFFD: "Thread mode, return to PSP (process stack)",
        0xFFFFFFE1: "Handler mode, MSP, FPU",
        0xFFFFFFE9: "Thread mode, MSP, FPU",
        0xFFFFFFED: "Thread mode, PSP, FPU",
    }
    return tab.get(lr, f"unknown EXC_RETURN 0x{lr:08X}")


def decode_mfsr(mfsr: int):
    """MemManage Fault Status（CFSR 低字节）。"""
    r = []
    if mfsr & 0x01: r.append("IACCVIOL: 指令访问违例（XN 区执行/函数指针错）")
    if mfsr & 0x02: r.append("DACCVIOL: 数据访问违例（MPU）")
    if mfsr & 0x08: r.append("MUNSTKERR: 出栈错误")
    if mfsr & 0x10: r.append("MSTKERR: 入栈错误")
    if mfsr & 0x80: r.append("MMARVALID: MMFAR 有效")
    return r


def decode_bfsr(bfsr: int):
    """BusFault Status（CFSR 次字节）。"""
    r = []
    if bfsr & 0x01: r.append("IBUSERR: 指令预取错误（跳转非法地址/Flash损坏）")
    if bfsr & 0x02: r.append("PRECISERR: 精确数据错误（BFAR 有效）")
    if bfsr & 0x04: r.append("IMPRECISERR: 非精确数据错误（BFAR 无效）")
    if bfsr & 0x08: r.append("UNSTKERR: 出栈错误")
    if bfsr & 0x10: r.append("STKERR: 入栈错误")
    if bfsr & 0x80: r.append("BFARVALID: BFAR 有效")
    return r


def decode_ufsr(ufsr: int):
    """UsageFault Status（CFSR 高半字）。"""
    r = []
    if ufsr & 0x01: r.append("UNDEFINSTR: 未定义指令")
    if ufsr & 0x02: r.append("INVSTATE: 无效状态（Thumb 位错）")
    if ufsr & 0x04: r.append("INVPC: 无效 PC 加载（EXC_RETURN 非法）")
    if ufsr & 0x08: r.append("NOCP: 协处理器访问（FPU 不可用）")
    if ufsr & 0x100: r.append("UNALIGNED: 未对齐访问")
    if ufsr & 0x200: r.append("DIVBYZERO: 除零")
    return r


def decode_cfsr(cfsr: int):
    """解码 CFSR（= MFSR | BFSR<<8 | UFSR<<16）。返回 dict。"""
    return {
        "raw": cfsr,
        "MFSR": decode_mfsr(cfsr & 0xFF),
        "BFSR": decode_bfsr((cfsr >> 8) & 0xFF),
        "UFSR": decode_ufsr((cfsr >> 16) & 0xFFFF),
    }


def decode_hfsr(hfsr: int):
    """解码 HFSR（HardFault Status）。"""
    r = []
    if hfsr & 0x80000000: r.append("FORCED: 由 MemManage/BusFault/UsageFault 升级（看下层 status）")
    if hfsr & 0x40000000: r.append("VECTTBL: 读向量表失败")
    return r


def decode_dfsr(dfsr: int):
    """解码 DFSR（Debug Fault Status）。"""
    r = []
    if dfsr & 0x01: r.append("HALTED")
    if dfsr & 0x02: r.append("BKPT")
    if dfsr & 0x04: r.append("DWTTRAP")
    if dfsr & 0x08: r.append("VCATCH")
    if dfsr & 0x10: r.append("EXTERNAL")
    return r


def format_fault(cfsr=None, hfsr=None, mmfar=None, bfar=None, dfsr=None):
    """格式化 fault 寄存器解读。任一为 None 则跳过。"""
    lines = ["  ### Cortex-M Fault Status"]
    any_val = False
    if hfsr is not None:
        any_val = True
        lines.append(f"    HFSR = 0x{hfsr:08X}: " + ("; ".join(decode_hfsr(hfsr)) or "(no bits)"))
    if cfsr is not None:
        any_val = True
        d = decode_cfsr(cfsr)
        lines.append(f"    CFSR = 0x{cfsr:08X}")
        for k in ("MFSR", "BFSR", "UFSR"):
            if d[k]:
                lines.append(f"      {k}: " + "; ".join(d[k]))
    if mmfar is not None:
        any_val = True
        lines.append(f"    MMFAR = 0x{mmfar:08X} (MemManage 故障地址)")
    if bfar is not None:
        any_val = True
        lines.append(f"    BFAR  = 0x{bfar:08X} (BusFault 故障地址)")
    if dfsr is not None:
        any_val = True
        lines.append(f"    DFSR  = 0x{dfsr:08X}: " + ("; ".join(decode_dfsr(dfsr)) or "(no bits)"))
    if not any_val:
        lines.append("    (无 fault 寄存器值——ASSERT 场景无硬件 fault，或 store 未转储)")
    return "\n".join(lines)


def format_exception_frame(frame, elf=None):
    """格式化异常栈帧。"""
    if not frame:
        return "  ### Exception frame\n    (无栈帧)"
    lines = ["  ### Exception frame (hardware-pushed)"]
    for nm in FRAME_REGS:
        v = frame[nm]
        extra = ""
        if elf and nm in ("PC", "LR") and elf.is_code(v):
            loc = elf.locate(v)
            if loc["symbol"]:
                extra = f"  -> {loc['symbol']}+0x{loc['sym_offset']:X}"
                if loc["file"]:
                    extra += f" [{loc['file']}:{loc['line']}]"
        lines.append(f"    {nm:5} = 0x{v:08X}{extra}")
    return "\n".join(lines)
