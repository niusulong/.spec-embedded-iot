#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 AP heap-state inspector: g_osApSystemMem stats + malloc trace ring +
SLOG cachedIntPrints peek.

Quantifies heap exhaustion and identifies the top heap consumers by walking
the gOsiMemRecords ring (every osMalloc/osFree records {caller>>1, ptr}).

Usage:  python heap_state.py <dump_dir> <ap.elf>
"""
import os, sys, struct
from collections import Counter
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols, find_toolchain, addr2line_batch

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

DUMP = sys.argv[1] if len(sys.argv) > 1 else "."
ELF = sys.argv[2] if len(sys.argv) > 2 else "8852_cat1bis_op_mdl_4M.elf"

# Empirical osDlmalloc_t offsets (validated on 8852 debug build). DWARF is
# preferred; these are the fallback when the typedef chain can't be resolved.
EMPIRICAL = {"base": 0x04, "total": 0x08}


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    tc = find_toolchain(DUMP)
    addr2line = os.path.join(tc, "riscv64-unknown-elf-addr2line.exe") if tc else ""
    S = lambda n: syms.lookup(n)[0]

    print("=" * 92)
    print(" AP 系统堆状态（g_osApSystemMem）")
    print("=" * 92)
    heap_addr = S("g_osApSystemMem")
    if not heap_addr:
        print("  g_osApSystemMem 不在符号表"); return
    offs = syms.struct_offsets("osDlmalloc_t")
    print("osDlmalloc_t 结构偏移(DWARF): %s" % ("已解析" if offs else "未解析 -> 用经验偏移"))
    if not offs:
        offs = EMPIRICAL

    base = mem.try_u32(heap_addr + offs.get("base", 0x04))
    total = mem.try_u32(heap_addr + offs.get("total", 0x08))
    print("  @0x%08x  base=0x%08x  total=0x%x (%d B)" % (heap_addr, base, total, total))

    # dump first 0x80 bytes so other stats fields (used/free/max_used/top_size)
    # can be read by matching against dlmallocPrint output format
    raw = mem.read(heap_addr, 0x80)
    print("  first 0x80 bytes (match used/free/top_size vs dlmallocPrint):")
    for i in range(0, 0x80, 16):
        vals = " ".join("%08x" % struct.unpack("<I", raw[i + j * 4:i + j * 4 + 4])[0] for j in range(4))
        print("    +%02x: %s" % (i, vals))

    # used/max_used/free/top_size if DWARF gave offsets
    for fld in ("used", "max_used", "free", "top_size", "max_chunk", "free_chunks"):
        if fld in offs:
            v = mem.try_u32(heap_addr + offs[fld])
            if v is not None:
                print("  %-12s = 0x%08x (%d)" % (fld, v, v))
    if base and total and "used" in offs:
        u = mem.try_u32(heap_addr + offs["used"])
        if u is not None:
            print("  >> 堆使用率(结构字段): %.1f%%   # >95%% = 堆耗尽高危" % (100.0 * u / total))

    # If used offset is unknown (DWARF missed the typedef), compute usage by
    # physically walking chunks — this is the authoritative method anyway.
    if base and total and "used" not in offs:
        inuse_b = free_b = nchk = nfree = 0
        pp = base; corrupt = False
        end = base + total
        PREV = 0x1; SBITS = 0x3; MSIZE = 16
        while pp < end and nchk < 500000:
            raw = mem.try_u32(pp + 4)
            if raw is None:
                break
            sz = raw & ~SBITS
            if sz < MSIZE or sz > total or (sz & 7) or pp + sz > end:
                corrupt = True; break
            nxt_raw = mem.try_u32(pp + sz + 4)
            inuse = bool(nxt_raw & PREV) if nxt_raw is not None else True
            if inuse:
                inuse_b += sz
            else:
                free_b += sz; nfree += 1
            pp += sz; nchk += 1
        if not corrupt and nchk:
            print("  >> 堆使用率(物理遍历): %.1f%%  (已用=0x%x / 总=0x%x, %d chunk, %d 空闲, 堆顶剩=0x%x(%dB))"
                  % (100.0 * inuse_b / total, inuse_b, total, nchk, nfree, (end - pp) if pp <= end else 0,
                     (end - pp) if pp <= end else 0))

    # ---- malloc trace ring（osMalloc/osFree 操作环形记录，定位堆消耗户）----
    print("\n" + "=" * 92)
    print(" malloc/free 操作记录环（gOsiMemRecords）— 定位谁在吃堆")
    print("=" * 92)
    recs = S("gOsiMemRecords"); cnt_a = S("gOsiMemRecordCount"); pos_a = S("gOsiMemRecordPos")
    if not (recs and cnt_a and pos_a):
        print("  符号缺失"); return
    cnt = mem.try_u32(cnt_a); pos = mem.try_u32(pos_a)
    print("  环容量=%s  写入位=%s" % (cnt, pos))
    if not cnt:
        return

    # osiMemRecord_t: caller field bit31 = alloc(1)/free(0) flag, [30:0] = caller>>1.
    # Decode the address AND the alloc/free kind; counting only allocs gives the
    # true heap-consumer ranking (frees would otherwise pollute it).
    cc_alloc = Counter(); cc_free = Counter(); seq = []
    for k in range(cnt):
        ra = recs + k * 8
        c = mem.try_u32(ra); ptr = mem.try_u32(ra + 4)
        if c is None:
            continue
        is_alloc = bool(c & 0x80000000)
        caller = ((c & 0x7FFFFFFF) << 1) & 0xFFFFFFFF   # strip flag, undo >>1
        (cc_alloc if is_alloc else cc_free)[caller] += 1
        seq.append((k, caller, ptr, "A" if is_alloc else "F"))

    top = cc_alloc.most_common(15)
    addrs = [a for a, _ in top]
    res = addr2line_batch(addr2line, ELF, addrs)
    n_alloc = sum(cc_alloc.values()); n_free = sum(cc_free.values())
    print("  记录数: %d 次分配(alloc), %d 次释放(free)  # alloc/free 标志在 caller 的 bit31\n" % (n_alloc, n_free))
    print("  分配调用者排行（前15，只统计 alloc = 真正吃堆的）:")
    for a, n in top:
        fn, fl = res.get(a, ("?", "?"))
        print("    %5d  (%5.1f%%)  0x%08x  %-30s %s" % (n, 100.0 * n / max(1, n_alloc), a, fn[:30], fl))

    # 最近 N 条记录（按时间序）— 注意：最后一条不是崩溃那次 alloc
    print("\n  最近 16 条记录(旧->新; A=分配 F=释放)。注意：崩溃那次 osMalloc")
    print("  不在环里（dlMalloc 没返回就没记录）；以下是崩溃前的历史操作:")
    n_dump = min(cnt, 16)
    start = (pos - n_dump) % cnt
    for j in range(n_dump):
        idx = (start + j) % cnt
        k, caller, ptr, kind = seq[idx]
        fn = res.get(caller, syms.resolve(caller)[:1] + ("?",))[0] if caller else "-"
        print("    [%4d] %s caller=0x%08x %-26s ptr=0x%08x" % (idx, kind, caller, fn[:26], ptr))

    # ---- SLOG ISR 日志堆积 ----
    print("\n" + "=" * 92)
    print(" SLOG 中断日志状态（ISR 内打印堆积情况）")
    print("=" * 92)
    for nm in ("g_slogIsrLogTotalLen", "g_slogIsrLogHisMaxLen", "g_slogExpLogTotalLen"):
        a = S(nm)
        if a:
            v = mem.try_u32(a)
            print("  %-24s @0x%08x = %s" % (nm, a, v))
    isr_max = 1024
    tot = mem.try_u32(S("g_slogIsrLogTotalLen") or 0)
    if tot is not None:
        flag = "已限流 THROTTLED — 中断日志生产 > 回收（堆积占堆）" if tot >= isr_max else "正常"
        print("  (SLOG_ISR_LOG_MAX_SIZE 限流阈值=%d; 当前=%s -> %s)" % (isr_max, tot, flag))

    # ---- cachedIntPrints 窥探（ISR 日志缓存队列）----
    pool = S("g_slogBufPool")
    if pool:
        print("\n  g_slogBufPool @0x%08x — 扫描 SLOG_List{count,head}(head 指向堆):" % pool)
        heap_lo = base; heap_hi = base + total
        for o in range(0, 0x180, 4):
            cn = mem.try_u32(pool + o); hd = mem.try_u32(pool + o + 4)
            if cn is None or hd is None:
                continue
            if 0 < cn < 4096 and heap_lo and heap_lo <= hd < heap_hi:
                # walk a few nodes to validate it's a SLOG_CachedIntPrint list
                node = hd; walked = 0; ok = 0
                seen = set()
                while node and node not in seen and walked < cn + 4:
                    seen.add(node)
                    try:
                        nxt = mem.u32(node); sz = mem.u16(node + 4)
                    except Exception:
                        break
                    if 0 < sz < 4096:
                        ok += 1
                    node = nxt; walked += 1
                if ok >= min(cn, 4):
                    print("    +0x%03x : count=%-3d head=0x%08x (walked %d, looks like cachedIntPrints/cachedBlocks)"
                          % (o, cn, hd, ok))


if __name__ == "__main__":
    main()
