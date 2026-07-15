#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 task/thread analyzer: enumerate all osThread TCBs by SIGNATURE SCAN
over PSRAM-BSS + IRAM (does NOT rely on the tlist linked list — which may itself
be corrupted in a crash), and check each thread's stack watermark for overflow.

Covers crash scenarios the heap/trace analyzers miss:
  - stack overflow (watermark > 90%)
  - deadlock / starvation (all threads SUSPEND, or a high-prio thread blocked)
  - identifying the interrupted task and what every task was doing

Usage:  python threads.py <dump_dir> <ap.elf>
"""
import os, sys, struct
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

STAT = {0: "INIT", 1: "READY", 2: "SUSPEND", 3: "RUNNING", 4: "CLOSE"}


def stack_watermark(mem, stack_addr, stack_size):
    """Return used bytes (high-water) via the RT-Thread magic-fill method.
    Unused stack is filled with a magic byte; scan from the bottom (stack_addr)
    upward counting the leading magic run. used = stack_size - unused.
    """
    try:
        sample = mem.read(stack_addr, 32)
    except Exception:
        return None, None
    # magic = most common non-zero byte in the sample (the fill pattern)
    cnt = Counter(b for b in sample if b != 0)
    if not cnt:
        return 0, None
    magic, _ = cnt.most_common(1)[0]
    # scan from stack_addr upward while byte == magic
    unused = 0
    step = 64
    a = stack_addr
    try:
        # scan whole stack in chunks; count leading magic bytes
        buf = mem.read(stack_addr, stack_size)
    except Exception:
        return None, magic
    for b in buf:
        if b == magic:
            unused += 1
        else:
            break
    used = stack_size - unused
    return used, magic


# ---------------------------------------------------------------------------
# Importable TCB enumeration (shared by unwind_cfi.py / assert_reason.py).
# Mirrors the scan + signature logic inside main(), but module-level so callers
# get the same false-positive-resistant thread list without re-implementing it.
# ---------------------------------------------------------------------------
SCAN_REGIONS = [
    (0x80160000, 0x801c0000),   # PSRAM BSS (static TCBs, heap-adjacent)
    (0xc0200000, 0xc0280000),   # IRAM (task stacks + TCBs)
    (0x801a0000, 0x80280000),   # PSRAM heap (dynamically-created threads)
]


def tcb_name(mem, O, tcb):
    """Decode tcb->name if it points to a plausible task identifier, else ''."""
    try:
        name_p = mem.u32(tcb + O["name"])
    except Exception:
        return ""
    if not (0x00008000 <= name_p < 0x100000000):
        return ""
    try:
        s = mem.cstr(name_p, 20)
    except Exception:
        return ""
    if not s or len(s) < 2 or not all(33 <= ord(c) < 127 for c in s):
        return ""
    if "." in s or "/" in s or len(set(s)) <= 1:
        return ""
    return s


def is_valid_tcb(mem, O, tcb):
    """Strong osThread_t signature: printable name + sane stack size + stack
    base in RAM + stat valid + SP inside its own stack region. Returns name or ''."""
    name = tcb_name(mem, O, tcb)
    if not name:
        return ""
    try:
        ss = mem.u32(tcb + O["stackSize"]); sa = mem.u32(tcb + O["stackAddr"])
        sp = mem.u32(tcb + O["sp"])
        stat = (mem.u32(tcb + O["stat"]) & 0xf) if "stat" in O else 0
    except Exception:
        return ""
    if not (256 <= ss <= (1 << 16)):
        return ""
    if not (0x00010000 <= sa < 0x80400000 or 0xc0200000 <= sa < 0xc0280000):
        return ""
    if stat > 4:
        return ""
    if not (sa <= sp <= sa + ss):
        return ""
    return name


def enumerate_tcbs(mem, O):
    """Return [(tcb_addr, name), ...] by signature scan (robust to list
    corruption). Ordered by address."""
    out = []
    found = set()
    for lo, hi in SCAN_REGIONS:
        a = lo
        while a < hi:
            try:
                name_p = mem.u32(a + O["name"])
            except Exception:
                a += 8; continue
            if 0x00008000 <= name_p < 0x100000000:
                nm = is_valid_tcb(mem, O, a)
                if nm and a not in found:
                    found.add(a)
                    out.append((a, nm))
            a += 8
    out.sort(key=lambda x: x[0])
    return out


def find_thread_by_stack(mem, O, addr):
    """Return (tcb, name) of the thread whose stack region contains addr, or None."""
    if not addr:
        return None
    for tcb, nm in enumerate_tcbs(mem, O):
        sa = mem.u32(tcb + O["stackAddr"]); ss = mem.u32(tcb + O["stackSize"])
        if sa <= addr < sa + ss:
            return tcb, nm
    return None


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    S = lambda n: syms.lookup(n)[0]
    o = syms.struct_offsets("osThread_t")
    if not o:
        print("osThread_t DWARF offsets not found; cannot enumerate threads."); return
    O = {k: o[k] for k in ["name", "tlist", "sp", "stackAddr", "stackSize", "stat",
                            "currentPriority", "initPriority", "remainingTick", "entry"] if k in o}
    O_TLIST = O["tlist"]

    print("=" * 92)
    print(" 任务列表 + 栈水位（TASK / THREAD LIST）")
    print("=" * 92)
    ct = mem.try_u32(S("g_osCurrentThread") or 0)
    print("g_osCurrentThread -> 0x%08x   # 当前任务（ISR 上下文时=被中断的任务）" % (ct or 0))

    def read_name(tcb):
        """Return decoded name string if (tcb+name_off) holds a valid pointer
        to a printable string, else None."""
        try:
            name_p = mem.u32(tcb + O["name"])
        except Exception:
            return None
        if not (0x00008000 <= name_p < 0x100000000):
            return None
        try:
            s = mem.cstr(name_p, 20)
        except Exception:
            return None
        if not s or len(s) < 2 or not all(33 <= ord(c) < 127 for c in s):
            return None
        # reject file names and stack-fill patterns (false positives)
        if "." in s or "/" in s:
            return None
        if len(set(s)) <= 1:                    # e.g. "####", "ZZZZ"
            return None
        return s

    def is_tcb(tcb):
        """Strong TCB signature: printable task name + sane stack size + stack
        base in RAM + SP currently inside its own stack region."""
        name = read_name(tcb)
        if not name:
            return None
        try:
            ss = mem.u32(tcb + O["stackSize"])
            sa = mem.u32(tcb + O["stackAddr"])
            sp = mem.u32(tcb + O["sp"])
            stat = mem.u32(tcb + O["stat"]) & 0xf if "stat" in O else 0
        except Exception:
            return None
        if not (256 <= ss <= (1 << 16)):         # task stacks: 256..64KB
            return None
        if not (0x00010000 <= sa < 0x80400000 or 0xc0200000 <= sa < 0xc0280000):
            return None
        if stat > 4:                             # INIT/READY/SUSPEND/RUNNING/CLOSE
            return None
        # SP must lie within this thread's own stack region (kills most false positives)
        if not (sa <= sp <= sa + ss):
            return None
        return name

    # Enumerate TCBs by SCAN (robust — does not depend on list integrity).
    # TCBs live in PSRAM BSS and the IRAM task-stack area. Scan both, 8-byte
    # aligned, checking the osThread_t signature.
    threads = []   # (tcb_addr, name)
    found = set()
    SCAN_REGIONS = [
        (0x80160000, 0x801c0000),   # PSRAM BSS (static TCBs, heap-adjacent)
        (0xc0200000, 0xc0280000),   # IRAM (task stacks + TCBs)
        (0x801a0000, 0x80280000),   # PSRAM heap (dynamically-created threads)
    ]
    for lo, hi in SCAN_REGIONS:
        a = lo
        while a < hi:
            try:
                # quick prefilter: name slot must look like a pointer
                name_p = mem.u32(a + O["name"])
            except Exception:
                a += 8; continue
            if 0x00008000 <= name_p < 0x100000000:
                nm = is_tcb(a)
                if nm and a not in found:
                    found.add(a)
                    threads.append((a, nm))
            a += 8

    # 当前任务排第一
    threads.sort(key=lambda x: (0 if x[0] == ct else 1, x[0]))
    print("扫描法枚举到 %d 个任务（不依赖任务链表完整性）\n" % len(threads))

    hdr = "%-20s %-8s %-6s %-12s %-12s %-12s %s" % (
        "任务名", "状态", "优先级", "sp(栈顶)", "栈底", "已用/总", "水位")
    print(hdr)
    print("  " + "-" * (len(hdr)))
    print("  # 状态: INIT=初始 READY=就绪 SUSPEND=挂起 RUNNING=运行 CLOSE=关闭")
    print("  # 水位 = 栈已用比例；>90% = 栈溢出风险（可能破坏相邻堆/TCB）")
    rows = []
    for tcb, name0 in threads:
        try:
            name = name0 or read_name(tcb) or "?"
            stat = mem.try_u32(tcb + O["stat"]) & 0xf if "stat" in O else 0
            stat_s = STAT.get(stat, str(stat))
            prio = (mem.try_u32(tcb + O["currentPriority"]) & 0xff) if "currentPriority" in O else 0
            sp = mem.try_u32(tcb + O["sp"])
            sa = mem.try_u32(tcb + O["stackAddr"])
            ss = mem.try_u32(tcb + O["stackSize"])
        except Exception:
            continue
        wm = None
        used = 0
        if sa and ss:
            u, magic = stack_watermark(mem, sa, ss)
            if u is not None:
                used = u
                wm = 100.0 * used / ss if ss else 0
        rows.append((name, stat_s, prio, sp, sa, ss, used, wm if wm is not None else 0))
        flag = ""
        if wm is not None and wm > 90:
            flag = "  *** 栈溢出风险"
        if tcb == ct:
            flag += "  <- 被中断的当前任务"
        print("  %-20s %-8s %-6d 0x%08x 0x%08x %-5d/%-5d %5.1f%%%s" %
              (name[:20], stat_s, prio, sp or 0, sa or 0, used, ss or 0,
               wm if wm is not None else 0, flag))

    # 状态汇总
    if rows:
        from collections import Counter as C
        states = C(r[1] for r in rows)
        print("\n状态汇总: " + ", ".join("%s=%d" % (k, v) for k, v in states.items()))
        print("  # 若全 SUSPEND 无 READY -> 疑似死锁；某高优先级长期 RUNNING -> 疑似饿死低优先级")
        hi = [r for r in rows if r[7] > 80]
        if hi:
            print("高水位(>80%)任务（需关注栈溢出风险）:")
            for name, st, pr, sp, sa, ss, used, wm in sorted(hi, key=lambda x: -x[7]):
                print("  %-20s %5.1f%% （已用 %d / 总 %d 字节）" % (name[:20], wm, used, ss))


if __name__ == "__main__":
    main()
