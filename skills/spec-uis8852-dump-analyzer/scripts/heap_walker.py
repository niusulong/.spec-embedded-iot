#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8852 heap physical walker — decides EXHAUSTED vs CORRUPTED.

Walks g_osApSystemMem from `base` to `base+total`, stepping chunk by chunk via
`chunksize = p->size & ~3` (SIZE_BITS = PREV_INUSE|IS_MMAPPED). Validates every
chunk's size, then classifies:

  - all chunks valid + gap==0 + top very small (<64B)  => EXHAUSTED (heap intact)
  - some chunk has illegal size / overruns              => MEMORY OVERWRITE
  - heap intact but a free chunk's fd/bk points to a
    non-bin-header, non-heap address                     => FREE-LIST LINKAGE CORRUPTION

Also checks the DTCM `av_` bin-header array integrity (bin_at(i)=av_+8i) and
distinguishes NORMAL bin-header pointers from real corruption.

Usage:  python heap_walker.py <dump_dir> <ap.elf>
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
# Optional: --victim-ptr 0xPTR  (spotlight a freed pointer's block for double-free triage)
VICTIM_PTR = None
for _i, _a in enumerate(sys.argv[3:], start=3):
    if _a.startswith("--victim-ptr="):
        VICTIM_PTR = int(_a.split("=", 1)[1], 0)
    elif _a == "--victim-ptr" and _i + 1 < len(sys.argv):
        VICTIM_PTR = int(sys.argv[_i + 1], 0)

PREV_INUSE = 0x1
IS_MMAPPED = 0x2
SIZE_BITS = PREV_INUSE | IS_MMAPPED   # 0x3
MINSIZE = 16
DTCM_LO, DTCM_HI = 0x00010000, 0x00014000   # AP DTCM range (av_ bin headers live here)


def main():
    mem = Mem(DUMP)
    syms = Symbols(ELF)
    S = lambda n: syms.lookup(n)[0]

    heap_addr = S("g_osApSystemMem")
    offs = syms.struct_offsets("osDlmalloc_t") or {"base": 0x04, "total": 0x08}
    base = mem.try_u32(heap_addr + offs.get("base", 0x04))
    total = mem.try_u32(heap_addr + offs.get("total", 0x08))
    end = base + total
    print("=" * 92)
    print(" HEAP PHYSICAL WALK  (g_osApSystemMem)")
    print("=" * 92)
    print("  base=0x%08x  total=0x%x(%d)  end=0x%08x" % (base, total, total, end))

    p = base
    chunks = []
    corrupt_at = None
    steps = 0
    while p < end:
        steps += 1
        try:
            size_raw = mem.u32(p + 4)
        except Exception as ex:
            print("  read error @0x%08x: %s" % (p, ex)); break
        sz = size_raw & ~SIZE_BITS
        flags = size_raw & SIZE_BITS
        bad = False; reason = ""
        if sz == 0: bad, reason = True, "size=0"
        elif sz < MINSIZE: bad, reason = True, "sz(%d)<MINSIZE" % sz
        elif sz > total: bad, reason = True, "sz(0x%x)>total" % sz
        elif (sz & 7) != 0: bad, reason = True, "sz misaligned(0x%x)" % sz
        elif p + sz > end: bad, reason = True, "overruns end (p+sz=0x%x)" % (p + sz)
        if bad:
            corrupt_at = p
            prev_size = mem.try_u32(p)
            print("\n  *** CORRUPT CHUNK @0x%08x  prev_size=0x%08x size_raw=0x%08x  (%s)"
                  % (p, prev_size or 0, size_raw, reason))
            try:
                ctx = mem.read(p, 0x20)
                print("   bytes: " + " ".join("%02x" % b for b in ctx))
            except Exception:
                pass
            break
        # in-use? look at next chunk's PREV_INUSE
        np = p + sz
        next_raw = mem.try_u32(np + 4)
        inuse = bool(next_raw & PREV_INUSE) if next_raw is not None else True
        fd = mem.try_u32(p + 8) if not inuse else 0
        bk = mem.try_u32(p + 12) if not inuse else 0
        chunks.append((p, sz, flags, inuse, fd, bk))
        p += sz
        if steps > 500000:
            print("  step cap"); break

    n_total = len(chunks)
    n_free = sum(1 for c in chunks if not c[3])
    n_inuse = n_total - n_free
    free_bytes = sum(c[1] for c in chunks if not c[3])
    inuse_bytes = sum(c[1] for c in chunks if c[3])
    top = chunks[-1] if chunks else None

    print("\n  遍历 %d 个 chunk（%d 在用 / %d 空闲），停在 @0x%08x" % (n_total, n_inuse, n_free, p))
    print("  已用 inuse=0x%x   空闲 free=0x%x   合计=0x%x (堆总 total=0x%x, 缺口 gap=0x%x)"
          % (inuse_bytes, free_bytes, inuse_bytes + free_bytes, total, total - (inuse_bytes + free_bytes)))
    print("  # gap=0 表示堆内存物理完整（无空洞/越界）；top 是堆顶剩余块，越小越接近耗尽")
    if top:
        print("  堆顶 chunk(top): @0x%08x sz=0x%x(%d字节) inuse=%s   # top 很小=堆几乎满" % (top[0], top[1], top[1], top[3]))

    frees = sorted([c for c in chunks if not c[3]], key=lambda c: -c[1])[:5]
    print("\n  最大 5 个空闲块（最大块<请求大小=无法满足分配）:")
    for c in frees:
        print("    @0x%08x sz=0x%x(%d字节)  fd=0x%08x bk=0x%08x" % (c[0], c[1], c[1], c[4], c[5]))

    # ---- fd/bk 合法性：区分 bin-header(DTCM，正常) vs 损坏 ----
    print("\n  空闲块 fd/bk 链表指针检查:")
    bin_hdr_ptrs = 0; bad_ptrs = 0; heap_ptrs = 0
    bad_examples = []
    for c in chunks:
        if c[3]:
            continue
        for v in (c[4], c[5]):
            if v == 0:
                continue
            if DTCM_LO <= v < DTCM_HI:
                bin_hdr_ptrs += 1     # likely bin header in av_ array
            elif base <= v < end:
                heap_ptrs += 1        # another free chunk in heap (normal)
            else:
                bad_ptrs += 1
                if len(bad_examples) < 8:
                    bad_examples.append((c[0], c[1], v))
    print("    fd/bk -> DTCM（指向 bin header，正常）: %d" % bin_hdr_ptrs)
    print("    fd/bk -> 堆内（指向另一空闲块，正常）: %d" % heap_ptrs)
    print("    fd/bk -> 非法地址（<堆基址 或 越界）: %d   # 非0=空闲链表损坏" % bad_ptrs)
    for ca, cs, v in bad_examples:
        print("       空闲块 @0x%08x (sz=0x%x) 指向非法 0x%08x" % (ca, cs, v))

    # ---- 结论 ----
    print("\n  结论 VERDICT: ", end="")
    if corrupt_at:
        print("堆内存被越界写破坏 MEMORY OVERWRITE @0x%08x（chunk size 字段损坏）" % corrupt_at)
    elif bad_ptrs > 0:
        print("空闲链表链接损坏 FREE-LIST LINKAGE CORRUPTION（%d 个非法 fd/bk；堆内存本身完整）" % bad_ptrs)
    elif top and top[1] < 64:
        print("堆耗尽 EXHAUSTED — 堆物理结构完整，但堆顶仅剩 %d 字节" % top[1])
    else:
        print("堆物理完整（未检测到损坏）")

    # ---- bin-header (av_) 完整性扫描 ----
    # av_ 只有约 1KB（128 bin × 8 字节）。扫整个 16KB DTCM 会因其他全局量误报。
    # 精确定位 av_：bin[0].fd == top chunk 地址。av_ 起点 = 该位置前 8 字节，跨 128 bin。
    print("\n" + "=" * 92)
    print(" bin-header（av_ 数组）完整性检查")
    print("=" * 92)
    av_base = None
    if top:
        # The top chunk's value can appear in DTCM more than once by coincidence.
        # Find ALL occurrences and pick the one that looks like a real av_ head:
        # bin[0].fd==top AND the next bins are self-referential or point into heap.
        candidates = [a for a in range(DTCM_LO, DTCM_HI, 4) if mem.try_u32(a) == top[0]]
        for a in candidates:
            head = a - 8   # bin_at(0) header
            # validate: bin[0].bk and bin[1] should be self/heap/DTCM, not random
            b0bk = mem.try_u32(a + 4)
            b1fd = mem.try_u32(a + 8); b1bk = mem.try_u32(a + 12)
            def sane(v):
                return v is not None and (base <= v < end or DTCM_LO <= v < DTCM_HI or v == 0)
            if sane(b0bk) and sane(b1fd) and sane(b1bk):
                av_base = head
                break
        if av_base is None and candidates:
            av_base = candidates[0] - 8   # fallback to first occurrence
            print("  (warning: av_ located by first occurrence only — verify suspicious below)")
    if av_base is None:
        print("  could not locate av_ (no DTCM word == top chunk 0x%08x); skipping" % (top[0] if top else 0))
    else:
        NAV = 128
        av_end = av_base + NAV * 8
        print("  av_ 定位 @0x%08x..0x%08x（128 个 bin）" % (av_base, av_end))
        ok = bad = 0; bad_list = []
        for i in range(NAV):
            a = av_base + i * 8
            fd = mem.try_u32(a); bk = mem.try_u32(a + 4)
            if fd is None or bk is None:
                continue
            if fd == 0 and bk == 0:
                continue   # empty slot
            fd_heap = base <= fd < end
            bk_heap = base <= bk < end
            fd_bin = av_base <= fd < av_end
            bk_bin = av_base <= bk < av_end
            if (fd_heap and bk_heap) or (fd == bk and (fd_bin or fd_heap)) or (fd_bin and bk_bin):
                ok += 1
            else:
                bad += 1
                if len(bad_list) < 10:
                    bad_list.append((a, fd, bk))
        print("  合法 ok=%d  可疑 suspicious=%d   # suspicious 高=bin header 可能损坏" % (ok, bad))
        for a, fd, bk in bad_list:
            print("    bin@0x%08x fd=0x%08x bk=0x%08x" % (a, fd, bk))
        if top:
            print("  (bin[0].fd @0x%08x = top chunk 0x%08x)" % (av_base + 8, top[0]))

    # ---- victim pointer spotlight (double-free triage for dlmalloc.c:2066) ----
    if VICTIM_PTR:
        print("\n" + "=" * 92)
        print(" 受害指针 spotlight（dlmalloc.c:2066 double-free 排查）: ptr = 0x%08x" % VICTIM_PTR)
        print("=" * 92)
        vc = None
        for (p, sz, flags, inuse, fd, bk) in chunks:
            if p <= VICTIM_PTR < p + sz:
                vc = (p, sz, flags, inuse, fd, bk); break
        if vc is None:
            print("  ptr 不在堆任何 chunk 内（非堆指针 / 已被合并到更大空闲块）")
        else:
            p, sz, flags, inuse, fd, bk = vc
            np = p + sz
            nraw = mem.try_u32(np + 4)
            pi = (nraw & 1) if nraw is not None else -1
            print("  所在 chunk @0x%08x  size=0x%x(%d)  inuse=%s" % (p, sz, sz, inuse))
            print("  inuse 判据：下一块 @0x%08x size+flags=0x%08x PREV_INUSE=%d" % (np, nraw or 0, pi))
            if not inuse:
                print("  ★ 该块已 FREE —— 若 ptr 正被 free()/osFreeTrace 释放，高度怀疑 DOUBLE-FREE")
                print("    （trace.size 已被第一次 free 的 fd/bk 覆盖成 bin 头地址；")
                print("     trace.magic 合法是因 dlfree 不清 magic —— 不能据 magic 排除 double-free）")
                print("    => 权威判定: python double_free_detect.py <dump> <elf> --ptr 0x%08x" % VICTIM_PTR)
            else:
                print("  该块 INUSE —— 若此处 2066 downflow 断言，疑为越界写踩坏 osMemTrace_t 头")


if __name__ == "__main__":
    main()
