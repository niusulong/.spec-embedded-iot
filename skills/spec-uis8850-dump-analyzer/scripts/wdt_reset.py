#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 WDT / reset-reason analysis.

Distinguishes panic (gIsPanic=1, bluescreen) from real reset (gIsPanic=0).
Parses the watchdog register snapshot (50039000.bin) and WDT-related globals
to identify watchdog-timeout resets. When gIsPanic=0, the device already reset
before dump — combine with threads.py to find the deadlocked/starved task.

Usage:  python wdt_reset.py <dump_dir> <ap.elf>
"""
import os, sys, struct
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_dump_args, load_ctx


def main():
    args = parse_dump_args("UIS8850 WDT / reset-reason analyzer")
    mem, syms, *_ = load_ctx(args.dump_dir, args.ap_elf)
    print("=" * 80)
    print(" UIS8850 WDT / 复位原因分析")
    print("=" * 80)

    # ---- 1. panic vs 复位 ----
    ispanic_addr = syms.lookup("gIsPanic")[0]
    ispanic = mem.try_u32(ispanic_addr) if ispanic_addr else None
    print("\ngIsPanic = %s" % ispanic)
    if ispanic == 1:
        print("  -> 设备进入 panic 蓝屏 (抓 dump 时仍停在蓝屏死循环)")
        print("  -> 本次崩溃是 panic, 不是复位。WDT/复位寄存器是历史值。")
    elif ispanic == 0:
        print("  -> 设备已复位 (抓 dump 时已重启)")
        print("  -> 需查复位原因: 是 WDT 超时 / 上电 / 软件复位 / 复位键?")

    # ---- 2. 复位原因全局量 (若有) ----
    print("\n--- 复位原因相关全局量 ---")
    reset_found = False
    for n in ["gResetReson", "gResetReason", "gLastResetReason", "gSysnvResetReason",
              "gPowerOnResetReason", "gResetCause"]:
        a, sz = syms.lookup(n)
        if a:
            v = mem.try_u32(a) if sz and sz >= 4 else mem.try_u8(a)
            print("  %-24s @0x%08x = 0x%x" % (n, a, v or 0))
            # 位掩码解读 (Unisoc 常见: bit0 POR / bit1 EXT / bit2 SW / bit3 APSS_WDT / bit4 CPSS_WDT)
            if v:
                bits = []
                names = {0:"POR(上电)",1:"EXT(复位键)",2:"SW(软件)",
                         3:"APSS_WDT",4:"CPSS_WDT",5:"WDT",6:"JTAG",7:"WATCHDOG"}
                for b, nm in names.items():
                    if v & (1 << b):
                        bits.append("%s(bit%d)" % (nm, b))
                print("    解码: %s" % " | ".join(bits) if bits else "    解码: (未识别位)")
            reset_found = True
    if not reset_found:
        print("  (8850 未找到 gResetReson 等符号 — 复位原因可能在 sysnv/PCU 寄存器, 需另查)")

    # ---- 3. WDT 寄存器 ----
    print("\n--- WDT 寄存器 (50039000.bin) ---")
    wdt_path = os.path.join(args.dump_dir, "50039000.bin")
    if os.path.exists(wdt_path):
        wdt = open(wdt_path, "rb").read()
        print("  共 %d 字节, 关键寄存器:" % len(wdt))
        # 8850 AP WDT 寄存器布局 (常见 Marlin/8850 WDT)
        # +0x00: lock (0x1CA9=unlock magic), +0x04: ctrl (bit0=en),
        # +0x08: val (当前计数值), +0x0c: int_clr, +0x10: int_sts (bit0=WDT 触发)
        regs = ["lock", "ctrl(en)", "val(计数)", "int_clr", "int_sts(触发)",
                "reg5", "reg6", "reg7", "reg8", "reg9", "reg10", "reg11"]
        for i in range(min(12, len(wdt) // 4)):
            v = struct.unpack("<I", wdt[i*4:i*4+4])[0]
            print("    +0x%-2x %-14s = 0x%08x" % (i*4, regs[i] if i < len(regs) else "reg", v))
        ctrl = struct.unpack("<I", wdt[4:8])[0] if len(wdt) >= 8 else 0
        if ctrl & 1:
            print("  >>> WDT 使能中 (ctrl bit0=1)")
        else:
            print("  >>> WDT 未使能 (ctrl bit0=0) — 若 gIsPanic=0 且非上电, 排除 WDT 复位")
        # 检查是否全 0
        if all(b == 0 for b in wdt[:48]):
            print("  >>> WDT 寄存器全 0: WDT 未启用 (panic 场景常见, 蓝屏前可能停了 WDT)")
    else:
        print("  (无 50039000.bin, 跳过 WDT 寄存器)")

    # ---- 4. WDT 相关全局 (喂狗周期/使能) ----
    print("\n--- WDT 相关全局量 ---")
    for n in ["gSysWdtDisable", "gSysWdtFeedPeriod", "gSysWdtEnable",
              "gSysWdtTimeout", "g_hardWdtInfo", "gWdtFeedCnt"]:
        a, sz = syms.lookup(n)
        if a:
            v = mem.try_u32(a) if (sz and sz >= 4) else mem.try_u8(a)
            print("  %-22s @0x%08x = 0x%x" % (n, a, v or 0))

    # ---- 5. 总结 ----
    print("\n--- 判定 ---")
    if ispanic == 1:
        print("  本次为 panic 蓝屏 (非复位). WDT 寄存器为历史, 不代表本次因.")
        print("  根因定位看 gBlueScreenRegs + 栈回溯 (uis8850_analyze.py).")
    else:
        print("  本次为复位 (gIsPanic=0).")
        print("  - 若 WDT int_sts 置位 / ctrl 使能 -> WDT 超时 (找死锁/饿死任务)")
        print("  - 结合 threads.py 看是否有任务全 SUSPEND (死锁) 或某任务长期 RUNNING (饿死)")
        print("  - 跑 uis8850_analyze.py 确认 AP 是否有 panic 记录残留")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
