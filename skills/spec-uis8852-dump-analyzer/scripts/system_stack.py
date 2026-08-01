#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 system/interrupt stack analyzer.

Why this script exists
----------------------
UIS8852 AP reserves ONE stack in DTCM (__stack_start .. __stack_end, size
__stack_size) that serves DUAL duty — it is BOTH:

  * the **system/boot stack**: `__start` sets SP = __stack_end before the
    scheduler runs (and pre-fills the whole region with a repeating fill word);
  * the **interrupt stack**: `switch_irq_sp` (reached from `irq_entry`) saves
    the current thread SP and resets SP = __stack_end on EVERY IRQ, so all ISRs
    run here.

So "system stack" and "interrupt stack" are the SAME physical DTCM region,
just two roles. It is NOT a thread stack → threads.py (which walks osThread_t
TCBs) does NOT cover it. A single deep IRQ (e.g. a DEBUG-build dlmalloc
do_check chain, or SLOG backtrace formatting) can fill it by itself.

The silent failure mode: the stack grows DOWN from __stack_end toward
__stack_start. Overflowing past __stack_start clobbers the BSS global placed
immediately below it (the last symbol before __stack_start — e.g. g_tRlcUeEntity)
with stack-frame content (return addresses, heap/SLOG pointers, locals).
threads.py's "no thread stack > 90%" gives false reassurance here.

Build-output driven (no magic addresses)
----------------------------------------
Everything that could change between builds is read from the ELF, not hardcoded:
  * stack base/top/size  -> ELF symbols __stack_start / __stack_end / __stack_size
  * "is this value a code pointer" -> Symbols.func_containing() (value lands
    inside a known function's [addr, addr+size) — adapts to any memory layout,
    no per-region address magic)
  * exact-symbol labels   -> ELF symbol table (e.g. g_osApSystemMem)
Only the stack fill word uses a platform constant (see STACK_INIT_FILLS), and
even then detect_fill() picks the actual fill from the dump.

Usage:  python system_stack.py <dump_dir> <ap.elf>
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

# ---- analysis heuristics (NOT build values; tunable) ----
MARGIN_DANGER = 64          # bytes of fill left below deepest frame: < this = near-overflow
MARGIN_WARN = 256           # < this = stack running high
VICTIM_LOOKUP_BELOW = 0x2000   # search window for the adjacent global below __stack_start
FRAME_SCAN_BYTES = 0x200       # how far up from the deepest frame to list the call chain
OVERLAP_SCAN_BYTES = 0x80      # stack bytes sampled for the value-overlap test
SMALL_INT_MAX = 0x1000         # values <= this (and >0) shown as "small int"

# Platform stack-init fill candidates. UIS8852 __start fills the system stack
# with 0x24242424 (reset vector: lui t0,0x24242 ; add t0,t0,1060). detect_fill()
# still auto-detects from the dump; this only biases toward known RT-Thread/OS
# fill patterns when several are present.
STACK_INIT_FILLS = (0x24242424, 0xA5A5A5A5, 0xCCCCCCCC, 0xDEADBEEF, 0x23232323)


def detect_fill(mem, ss, se):
    """Pick the init-fill word from the dump itself: prefer a STACK_INIT_FILLS
    value that actually appears; fall back to the most frequent word. Returns
    (fill_or_None, count)."""
    n = (se - ss) // 4
    counts = {}
    for i in range(n):
        v = mem.try_u32(ss + i * 4)
        if v is not None:
            counts[v] = counts.get(v, 0) + 1
    if not counts:
        return None, 0
    for f in STACK_INIT_FILLS:
        if counts.get(f, 0) > 0:
            return f, counts[f]
    f = max(counts, key=counts.get)
    return f, counts[f]


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)

    ss = syms.lookup("__stack_start")[0]
    se = syms.lookup("__stack_end")[0]
    print("=" * 92)
    print(" 系统/中断栈分析（SYSTEM / INTERRUPT STACK — 同一块 DTCM 栈，两角色）")
    print("=" * 92)
    if ss is None or se is None:
        print("  未找到 __stack_start/__stack_end 符号（ELF 可能被 strip 或链接脚本未 PROVIDE），跳过。")
        print("  可改用 .map 查 __stack_start/__stack_end 后人工核对。")
        return
    size = se - ss
    print("  __stack_start = 0x%08x   __stack_end = 0x%08x   size = 0x%x (%d 字节)  [均取自 ELF 符号]"
          % (ss, se, size, size))
    print("  角色①系统/启动栈：__start 设 SP=__stack_end，并预填栈")
    print("  角色②中断栈    ：switch_irq_sp(irq_entry) 每次中断把 SP 重置为 __stack_end，所有 ISR 跑在此")
    print("  注：这是【非线程栈】，threads.py 不覆盖；线程栈水位正常不代表此栈安全")

    fill, fill_cnt = detect_fill(mem, ss, se)
    nwords = size // 4
    print("\n[填充模式] fill = 0x%08x （栈中共 %d/%d 字）[dump 自检测]" % (fill if fill is not None else 0, fill_cnt, nwords))

    # ---- watermark: lowest non-fill word (stack grows DOWN from se) ----
    deepest = se
    for i in range(nwords):
        v = mem.try_u32(ss + i * 4)
        if v is not None and v != fill:
            deepest = ss + i * 4
            break
    used = se - deepest
    pct = used * 100.0 / size if size else 0
    margin = deepest - ss
    print("\n[水位]（fill-scan 法：从栈底向上找首个非填充字）")
    print("  最深写入地址(最低非填充) = 0x%08x" % deepest)
    print("  已用 = 0x%x (%d) / 0x%x  =>  %.1f%%" % (used, used, size, pct))
    print("  fill-scan 余量 = %d 字节（最深写入字距 __stack_start）" % margin)
    # fill-scan 余量是【上界】：cm.push 类帧只把 ra 写在帧顶，帧底部留空(仍为填充)，
    # 扫描会把"帧内未写底部"误当未用栈，使余量偏大、真实最深 SP 可能已到栈底。
    # 是否真溢出以下方[溢出溅射检测]为准（看受害全局里有没有越过栈底的返回地址）。
    print("  注：此余量是【上界】——cm.push 帧底部不写时偏大；真实溢出判定见[溢出溅射检测]")
    if margin == 0:
        print("  ⚠️  fill-scan 余量=0：最深写入字已贴栈底")

    # ---- adjacent BSS global immediately below __stack_start (overflow victim) ----
    # build-derived: closest ELF symbol below __stack_start
    allsyms = sorted((addr, sz, nm) for nm, (addr, sz) in syms.tab.items()
                     if addr is not None and 0 < addr < ss)
    victim = None
    for addr, sz, nm in reversed(allsyms):
        if ss - addr <= VICTIM_LOOKUP_BELOW:
            victim = (addr, sz, nm)
            break
    print("\n[__stack_start 下方紧邻全局（溢出受害点）]")
    if not victim:
        print("  (未找到紧邻全局)")
        codehits = overlap = 0
    else:
        va, vsz, vnm = victim
        vsz = vsz if vsz and vsz > 0 else min(64, ss - va)
        vsz = min(vsz, ss - va)
        print("  %s @0x%08x size=0x%x (%d B)  → 距 __stack_start %d 字节" %
              (vnm, va, vsz, vsz, ss - va - vsz))

        sym_by_addr = {}
        for nm, (addr, _sz) in syms.tab.items():
            if addr:
                sym_by_addr.setdefault(addr, nm)

        words = []
        codehits = 0
        for off in range(0, vsz, 4):
            v = mem.try_u32(va + off)
            if v is None:
                words.append((off, None, "")); continue
            tag = ""
            if v == 0:
                tag = "0"
            elif v in sym_by_addr:
                tag = "= %s" % sym_by_addr[v]            # exact symbol (heap desc / boundary)
            else:
                fc = syms.func_containing(v)             # build-derived code test
                if fc:
                    tag = "代码→%s+0x%x" % (fc[0], v - fc[1]); codehits += 1
                elif 0 < v <= SMALL_INT_MAX:
                    tag = "小整数"
            words.append((off, v, tag))

        # value-overlap with deepest stack frames
        stack_vals = set()
        for off in range(0, OVERLAP_SCAN_BYTES, 4):
            tv = mem.try_u32(deepest + off)
            if tv is not None:
                stack_vals.add(tv)
        overlap = sum(1 for _, v, _ in words if v in stack_vals)

        print("  受害全局内容（0x%08x..0x%08x）:" % (va, va + vsz))
        for off, v, tag in words:
            print("    +0x%02x = 0x%08x  %s" % (off, v if v is not None else 0, tag))
        print("  → 代码指针 %d 个；与最深栈帧取值相同的有 %d 个" % (codehits, overlap))

    # ---- 溢出溅射取证（决定性）：跨栈底扫描，判定帧是否穿透 __stack_start 进入受害全局 ----
    # 比 fill-scan 水位更可靠：直接看受害全局(BSS)里有没有"只有栈帧才该有的"返回地址，
    # 以及越过栈底后帧的背景是否由栈填充(0x24242424)切换为 BSS 零值。BSS 不可能自发产生
    # 代码地址，故栈底以下只要出现 1 个 ra 即确证栈帧越过栈底。
    print("\n[溢出溅射检测]（跨栈底 0x%08x 扫描；以 ra 越栈底 + 背景填充指纹为准）" % ss)
    spill_lo = max((va if victim else ss - 0x80), ss - 0x200)
    spill_hi = ss + 0x80
    ras_below = []   # 受害全局里的返回地址 = 栈帧越过栈底的直接物证
    ras_above = []
    fill_below = fill_above = zero_below = zero_above = other_below = other_above = 0
    a = spill_lo
    while a < spill_hi:
        v = mem.try_u32(a)
        if v is None:
            a += 4; continue
        below = a < ss
        fc = syms.func_containing(v)
        if fc:
            (ras_below if below else ras_above).append((a, v, fc[0], v - fc[1]))
        elif v == fill:
            if below: fill_below += 1
            else: fill_above += 1
        elif v == 0:
            if below: zero_below += 1
            else: zero_above += 1
        else:
            if below: other_below += 1
            else: other_above += 1
        a += 4
    print("  扫描范围 [0x%08x, 0x%08x) 跨栈底 0x%08x：" % (spill_lo, spill_hi, ss))
    print("    栈底以上(栈区): 填充0x%08x ×%d, zero×%d, 其它×%d" % (fill or 0, fill_above, zero_above, other_above))
    print("    栈底以下(受害全局/BSS): 填充×%d, zero×%d, 其它×%d" % (fill_below, zero_below, other_below))
    if fill_above and fill_below == 0 and (zero_below > 0 or other_below > 0):
        print("    → 背景指纹：填充(0x%08x)只存于栈区、栈底以下为 BSS 零值/数据——与'栈帧越过栈底进 BSS'一致"
              % (fill or 0))
    print("  栈底以下(受害全局)的返回地址 ra [%d] = 栈越过 __stack_start 的直接物证：" % len(ras_below))
    for ra_a, ra_v, fn, off in ras_below[:8]:
        print("    @0x%08x 0x%08x  %s+0x%x" % (ra_a, ra_v, fn, off))
    if not ras_below:
        print("    (无——未发现 ra 越过栈底)")
    print("  栈底以上(当前快照深链)的返回地址 ra [%d]：" % len(ras_above))
    for ra_a, ra_v, fn, off in ras_above[:8]:
        print("    @0x%08x 0x%08x  %s+0x%x" % (ra_a, ra_v, fn, off))
    # 溢出确证：受害全局里出现返回地址(BSS 不可能自发产生代码地址) 即栈帧越过栈底；
    # 或保留原启发式(代码指针+重叠值) 作兜底。
    spilled = (len(ras_below) >= 1) or (victim and codehits >= 2 and overlap >= 2)

    # ---- deepest stack frames (what deep chain ran here) — build-derived code test ----
    print("\n[最深栈帧内的代码地址（=当时在中断栈上运行的深调用链）]")
    shown = 0
    for off in range(0, FRAME_SCAN_BYTES, 4):
        addr = deepest + off
        if addr >= se:
            break
        v = mem.try_u32(addr)
        if v is not None:
            fc = syms.func_containing(v)
            if fc:
                print("  sp=0x%08x: 0x%08x  %s+0x%x" % (addr, v, fc[0], v - fc[1]))
                shown += 1
                if shown >= 16:
                    break
    if shown == 0:
        print("  (栈中无可解析代码地址)")

    # ---- verdict ----
    print("\n[结论 VERDICT]")
    if spilled:
        print("  🚨 系统/中断栈溢出（已确证）：栈帧越过 __stack_start 进入紧邻全局 %s。"
              % (victim[2] if victim else "?"))
        if ras_below:
            print("     物证：栈底以下(受害全局)发现 %d 个返回地址（BSS 不可能自发产生代码地址）；"
                  "背景填充只存于栈区、栈底以下为 BSS 零值。" % len(ras_below))
        print("     注：上方 fill-scan '余量 %dB' 此处不可信——cm.push 帧底部不写使水位偏大；以本溅射检测为准。" % margin)
        print("     根因方向：增大 __stack_size；排查中断/回调深链（动态分配/SLOG backtrace/DEBUG 校验）；"
              "在 __stack_start 前加 guard。")
    elif margin == 0:
        print("  ⚠️  fill-scan 余量=0：最深写入字贴栈底，但未在受害全局确证 ra——疑溢出，建议人工复核[溢出溅射检测]。")
    elif margin < MARGIN_DANGER:
        print("  ⚠️  系统/中断栈高危（fill-scan 余量 %dB）。未确证已踩全局，但单次稍深中断即可溢出。" % margin)
        print("     建议增大 __stack_size；关注上方最深栈帧所示的深调用链。")
    elif victim and (codehits >= 2 or overlap >= 2):
        print("  ⚠️  紧邻全局 %s 含栈帧特征值（代码指针/重叠值），疑被系统/中断栈溢出污染，请人工复核。" % victim[2])
    elif margin < MARGIN_WARN:
        print("  系统/中断栈偏高（fill-scan 余量 %dB，水位 %.1f%%）。未确证溢出；若崩溃点全局异常，仍建议排查此栈。" % (margin, pct))
    else:
        print("  ✓ 系统/中断栈正常（水位 %.1f%%，fill-scan 余量 %dB）。排除系统/中断栈溢出。" % (pct, margin))


if __name__ == "__main__":
    main()
