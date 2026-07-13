#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 reset-reason + watchdog analyzer.

Distinguishes crash scenarios by combining:
  - gIsPanic / gBlueScreenAbortType (is this a blue-screen ASSERT/EXCEPTION,
    or did the device already reset and we caught the reboot?)
  - gResetReson bit-mask (hardware reset cause: power-on / ext / sw / AP-WDT / CP-WDT)
  - WDT enable/period/state globals + WDT register snapshot (e0002000)

Covers: WDT-timeout crashes, reset-cause forensics, "device rebooted mystery" cases.

Usage:  python wdt_reset.py <dump_dir> <ap.elf>
"""
import os, sys, struct
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

# gResetReson bit mask (drv_pmu.h)
RESET_FLAGS = [
    (0x01, "POR5_RSTN",      "Power-On reset (上电复位)"),
    (0x02, "EXT_RSTN",       "External reset pin (复位键)"),
    (0x04, "G_SW_RSTN",      "Global software reset (软件复位)"),
    (0x08, "APSS_WDT_RSTN",  "AP subsystem watchdog reset (AP WDT)"),
    (0x10, "CPSS_WDT_RSTN",  "CP subsystem watchdog reset (CP WDT)"),
]


def main():
    mem = Mem(DUMP, scan_all_peripherals=True)
    syms = Symbols(ELF)
    S = lambda n: syms.lookup(n)[0]
    u32 = lambda a: mem.try_u32(a)

    print("=" * 92)
    print(" 复位原因 + 看门狗（RESET REASON + WATCHDOG）")
    print("=" * 92)

    # ---- 蓝屏 vs 真复位 ----
    ispanic = u32(S("gIsPanic") or 0)
    abort = mem.try_u8(S("gBlueScreenAbortType")) if S("gBlueScreenAbortType") else None
    print("\n[gIsPanic=%s  gBlueScreenAbortType=%s]" % (ispanic, "0x%02x" % abort if abort is not None else "?"))
    if ispanic:
        print("  >> 蓝屏 BLUE SCREEN（死机后抓的 dump，未复位）。本次崩溃原因看 g_osAssert/g_osErrorLog")
        print("     注意：下方 gResetReson 是【上一次启动】的复位原因（历史），不是本次崩溃因")
    else:
        print("  >> 设备已复位（无 panic 标志）。下方 gResetReson 【就是】本次重启的原因")

    # ---- gResetReson（复位原因位掩码）----
    rr = u32(S("gResetReson") or 0) or 0
    print("\n[gResetReson = 0x%x]" % rr)
    if rr == 0:
        print("  (无标志/未记录)")
    for bit, name, desc in RESET_FLAGS:
        if rr & bit:
            print("  bit%d %-16s : %s  <-- 置位" % (bit.bit_length() - 1, name, desc))
        else:
            print("  bit%d : （未触发）" % (bit.bit_length() - 1))
    wdt = bool(rr & 0x18)
    por = bool(rr & 0x01)
    if rr:
        print("  >> 复位原因判定: " +
              ("AP/CP 看门狗超时 WDT" if wdt else
               "上电复位 POR5" if por else
               "外部复位/软件复位"))

    # ---- 看门狗全局量 ----
    print("\n[看门狗状态]")
    for nm, interp in [
        ("gSysnvSysWdtEnable",    "系统WDT使能(NV)"),
        ("gSysnvSysWdtFeedPeriod", "系统WDT喂狗周期(ms)"),
        ("gSysWdtDisable",         "运行时WDT禁用标志"),
        ("gSysnvPmicWdtFeedPeriod", "PMIC WDT喂狗周期"),
        ("g_hardWdtInfo",          "硬WDT信息/trace"),
        ("g_wdtTraceOffset",       "WDT trace环位置"),
        ("g_hardWdtCheckEnable",   "硬WDT检查使能"),
    ]:
        a = S(nm)
        if a is not None:
            v = u32(a)
            extra = ""
            if "FeedPeriod" in nm and v:
                extra = "  (%d ms)" % v
            print("  %-26s @0x%08x = %s  # %s%s" % (nm, a, "0x%08x" % v if v is not None else "?", interp, extra))

    # ---- WDT 寄存器快照（e0002000）----
    print("\n[WDT 看门狗寄存器快照 @0xe0002000]")
    try:
        raw = mem.read(0xe0002000, 0x20)
        for i in range(0, 0x20, 4):
            print("  +0x%02x: 0x%08x" % (i, struct.unpack("<I", raw[i:i + 4])[0]))
    except Exception as e:
        print("  (不可读: %s)" % e)

    # ---- PCU / PMU 复位寄存器（原始值）----
    print("\n[PCU/PMU 复位相关寄存器（原始值）]")
    for name, addr, sz in [("reg_gen_pcu_aon", 0xf1005000, 0x14),
                           ("reg_pmu_ctrl_aon", 0xf1001000, 0x14),
                           ("reg_gen_aon_sys_ctrl", 0xf2809800, 0x14)]:
        try:
            raw = mem.read(addr, sz)
            words = [struct.unpack("<I", raw[i:i + 4])[0] for i in range(0, sz, 4)]
            nz = [w for w in words if w]
            print("  %-22s @0x%08x: 非零=%s" % (name, addr, ["0x%08x" % w for w in nz] if nz else "(全零)"))
        except Exception as e:
            print("  %-22s @0x%08x: 不可读 (%s)" % (name, addr, e))

    # ---- 结论 ----
    print("\n[结论 VERDICT]")
    if ispanic:
        if abort == 0xFE:
            print("  ASSERT 断言崩溃（非复位）。忽略复位寄存器，分析 g_osAssert / 调用栈")
        else:
            print("  EXCEPTION 硬件异常（非复位）。分析 mcause/mepc；用 code_compare.py 查代码完整性")
    elif wdt:
        print("  看门狗超时复位 WDT。排查哪个任务死锁/关中断太久:")
        print("    - 用 threads.py 找异常 RUNNING 的任务")
        print("    - 查 g_wdtTraceOffset / 硬 WDT trace 看最后一次喂狗间隔")
    elif por:
        print("  上电复位（冷启动）。若用户报告死机，设备已重启、现场丢失——需要 panic dump")
    else:
        print("  非 WDT、非 POR 复位（外部引脚/软件）。排查固件是否主动调用了复位")


if __name__ == "__main__":
    main()
