#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 heap state + malloc-trace ring analysis.

Reads the OSA heap descriptor (gOsiDefaultHeap) for heap range/usage, and the
malloc-trace ring (gOsiMemRecords) to find the top heap consumers. Heap
exhaustion is a high-frequency crash root cause; this script localizes it.

Usage:  python heap_state.py <dump_dir> <ap.elf>
"""
import os, sys, collections
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import parse_dump_args, load_ctx


def main():
    args = parse_dump_args("UIS8850 heap state + malloc-trace analyzer")
    mem, syms, *_ = load_ctx(args.dump_dir, args.ap_elf)
    print("=" * 80)
    print(" UIS8850 堆状态 + malloc trace 分析")
    print("=" * 80)

    # ---- 1. 堆描述符 ----
    print("\n--- 堆描述符 (gOsiDefaultHeap / gOsiHeaps) ---")
    dh_addr = syms.lookup("gOsiDefaultHeap")[0]
    heaps_addr, heaps_sz = syms.lookup("gOsiHeaps")
    hs = syms.lookup("__heap_start")[0]
    he = syms.lookup("__heap_end")[0]
    print("gOsiDefaultHeap @0x%08x, gOsiHeaps @0x%08x (size %d)" % (dh_addr, heaps_addr, heaps_sz))
    print("__heap_start=0x%08x __heap_end=0x%08x (堆区 %d KB)" % (hs, he, (he - hs) // 1024))
    desc = mem.try_u32(dh_addr)
    print("*gOsiDefaultHeap -> 0x%08x (默认堆描述符)" % (desc or 0))
    if desc:
        print("  堆描述符前 0x20 字节:")
        fields = {}
        for i in range(0, 0x20, 4):
            v = mem.try_u32(desc + i)
            fields[i] = v or 0
            print("    +0x%-2x = 0x%08x" % (i, v or 0))
        # ⚠ 推测: dlmalloc 包装的 heap 描述符布局未在 DWARF 暴露, 以下偏移
        #    (+0x4 base / +0x8 size / +0x10 end / +0x14 used) 是经验值, 不同版本
        #    可能变。若 base/end 不合理请人工核对描述符实际字段。
        base = fields.get(4)
        end = fields.get(0x10)
        size_field = fields.get(8)
        used_field = fields.get(0x14)
        if base and end and end > base:
            total = end - base
            print("\n  堆范围推测: [0x%08x, 0x%08x) 总 %d KB  (字段偏移为推测)" % (base, end, total // 1024))
            if used_field and used_field < total:
                print("  已用推测 (+0x14): %d KB (%.1f%%)" % (
                    used_field // 1024, 100.0 * used_field / total))
                print("  空闲推测: %d KB" % ((total - used_field) // 1024))
                if used_field > total * 0.9:
                    print("  >>> 堆使用率 >90%%, 高度疑似堆耗尽! (基于推测偏移, 请交叉核对)")
        # gOsiHeaps 数组: 多个堆 region 指针
        print("\n  gOsiHeaps 数组 (各堆 region 指针):")
        for i in range(0, heaps_sz, 4):
            v = mem.try_u32(heaps_addr + i)
            if v and v != desc:
                print("    [%d] -> 0x%08x" % (i // 4, v))
            elif v == desc:
                print("    [%d] -> 0x%08x (= 默认堆)" % (i // 4, v))

    # CP 堆
    cpb = syms.lookup("cp_heap_base")[0]
    cpl = syms.lookup("cp_heap_limit")[0]
    if cpb and cpl:
        print("\n  CP 堆: [0x%08x, 0x%08x) %d KB" % (
            mem.try_u32(cpb) or 0, mem.try_u32(cpl) or 0,
            ((mem.try_u32(cpl) or 0) - (mem.try_u32(cpb) or 0)) // 1024))

    # ---- 2. malloc trace ring (gOsiMemRecords) ----
    print("\n--- malloc trace ring (gOsiMemRecords) ---")
    rec_addr, rec_sz = syms.lookup("gOsiMemRecords")
    pos_addr = syms.lookup("gOsiMemRecordPos")[0]
    pos = mem.try_u32(pos_addr) or 0
    print("gOsiMemRecords @0x%08x size=%d, 写入位置 gOsiMemRecordPos=%d" % (rec_addr, rec_sz, pos))
    # 每条记录: {caller(u32), ptr(u32)} = 8 字节; caller 存储时 >>1
    rec_struct = 8
    n_total = rec_sz // rec_struct
    caller_count = collections.Counter()
    caller_addrs = collections.Counter()
    valid = 0
    for i in range(n_total):
        caller_raw = mem.try_u32(rec_addr + i * rec_struct)
        ptr = mem.try_u32(rec_addr + i * rec_struct + 4)
        if not caller_raw or caller_raw < 0x1000:
            continue
        # 防御性归一: 若原始值不是代码地址, 但左移1位(还原被剥掉 Thumb 位的存储)后
        # 是代码地址, 则按 <<1 还原。直接地址存储的正常记录不受影响。
        real = caller_raw
        if not syms.is_exec_code(real) and syms.is_exec_code(real << 1):
            real = real << 1
        fn, _ = syms.resolve(real)
        caller_count[fn] += 1
        caller_addrs[fn] = real
        valid += 1
    print("有效记录 %d 条, 按调用者(堆消耗户)统计 Top 20:" % valid)
    print("  %-52s %-12s %s" % ("调用者函数", "地址", "次数"))
    for fn, cnt in caller_count.most_common(20):
        print("  %-52s 0x%08x   %d" % (fn[:52], caller_addrs[fn], cnt))
    print("\n  解读: 高频调用者 = 崩溃前堆分配最频繁的代码路径。")
    print("        若含协议栈数据通路(pbuf/PDCP/lwip/PS data)+大流量, 结合堆使用率判断是否堆耗尽。")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    main()
