#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 OSA trace binary decoder.

Decodes gTraceBuf (binary trace ring) into a readable pre-crash event stream.
The trace format was reverse-engineered from osiTraceBufInit/Put/prvTraceIdEx:
each record is [counter|total_len|0x0098|0x9198|len-8|tick|trace_id|payload].
The payload embeds formatted trace text (file:line func + message) + binary args.

Record validation requires the magic PAIR (0x0098@+6 AND 0x9198@+8) plus a
consistent len-8 field, because 0x9198 also occurs inside payload data.

Usage:  python trace_decode.py <dump_dir> <ap_elf> [--last N] [--no-kw]
"""
import os, sys, struct, re, collections, argparse
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_ctx


def main():
    ap = argparse.ArgumentParser(description="UIS8850 OSA trace decoder")
    ap.add_argument("dump_dir")
    ap.add_argument("ap_elf")
    ap.add_argument("--last", type=int, default=60, help="show last N readable events")
    ap.add_argument("--kw", action=argparse.BooleanOptionalAction, default=True,
                    help="show keyword-matched events (default on; --no-kw to suppress)")
    args = ap.parse_args()

    mem, syms, *_ = load_ctx(args.dump_dir, args.ap_elf)

    # gTraceBuf 地址/大小从 ELF 符号读 (非硬编码)
    tb_addr = syms.lookup("gTraceBuf")[0]
    tb_size = syms.lookup("gTraceBuf")[1]
    if not tb_addr:
        print("gTraceBuf 符号不存在, 无法解码 trace")
        return
    tb = mem.read(tb_addr, tb_size)

    print("=" * 80)
    print(" UIS8850 OSA trace 解码 (gTraceBuf @0x%08x, %d KB)" % (tb_addr, tb_size // 1024))
    print("=" * 80)

    def u32(o): return struct.unpack("<I", tb[o:o+4])[0]
    def u16(o): return struct.unpack("<H", tb[o:o+2])[0]

    # gTraceCtx: 24 个 8KB sub-buffer 的写位置 (看哪个最新)
    tc_addr = syms.lookup("gTraceCtx")[0]
    if tc_addr:
        print("\n活跃 trace buffer (pos 接近 8192=满):")
        for i in range(24):
            base = tc_addr + 0x40 + i * 0x14
            buf = mem.try_u32(base)
            pos = mem.try_u32(base + 4)
            if buf and pos and pos > 1000:
                print("  buf[%2d] @0x%08x pos=%d" % (i, buf, pos))

    # ---- 严格 record 识别 ----
    recs = []
    for o in range(8, tb_size - 0x30, 2):
        if u16(o) != 0x9198: continue
        if u16(o - 2) != 0x0098: continue      # magic 对
        rs = o - 8
        tlen = u16(rs + 4)
        lenm8 = u16(rs + 0xa)
        if tlen < 0x18 or tlen > 0x400: continue
        if lenm8 != (tlen - 8) & 0xffff: continue
        counter = u32(rs)
        tick = u32(rs + 0xc)
        tid = u32(rs + 0x10)
        if counter > 0x100000: continue
        if tick > 2000000: continue             # 过滤噪声 tick
        if rs + tlen > tb_size: continue
        recs.append((rs, counter, tlen, tick, tid))
    print("\n严格识别 %d 条 record" % len(recs))

    # ---- 提取文本 ----
    events = []
    for rs, counter, tlen, tick, tid in recs:
        payload = tb[rs + 0x14: rs + tlen]
        runs = []
        cur = ""
        for b in payload:
            if 32 <= b < 127:
                cur += chr(b)
            else:
                if len(cur) >= 4: runs.append(cur)
                cur = ""
        if len(cur) >= 4: runs.append(cur)
        text = "".join(runs)
        events.append((tick, counter, tid, text))   # 保留 bit31: 0=明文(可解码), 1=带ID(需TDB)
    events.sort()

    readable = [e for e in events if len(e[3]) >= 6]
    ticks = [e[0] for e in events]
    if ticks:
        print("tick 范围: %d ~ %d (崩溃前最新 = %d)" % (min(ticks), max(ticks), max(ticks)))
    n_plain = sum(1 for e in events if not (e[2] & 0x80000000))
    print("共 %d 条 record: 明文(可解码) %d, 带ID(需TDB) %d" % (len(events), n_plain, len(events)-n_plain))
    print("可读文本事件 %d" % len(readable))

    # ---- 输出崩溃前事件流 ----
    print("\n--- 崩溃前 trace 事件流 (最后 %d 条可读) ---" % args.last)
    print("%-9s %-6s %-5s %-11s %s" % ("tick", "ctr", "类型", "trace_id", "文本"))
    for tick, counter, tid, text in readable[-args.last:]:
        kind = "明文" if not (tid & 0x80000000) else "ID"
        print("%-9d %-6d %-5s 0x%08x  %s" % (tick, counter, kind, tid & 0x7fffffff, text[:90]))

    # ---- trace_id 频次 ----
    print("\n--- trace_id 频次 Top 12 (最活跃 trace 点) ---")
    tid_cnt = collections.Counter(e[2] for e in events)
    tid_sample = {}
    for e in events:
        if e[3] and e[2] not in tid_sample:
            tid_sample[e[2]] = e[3]
    for tid, cnt in tid_cnt.most_common(12):
        kind = "明文" if not (tid & 0x80000000) else "带ID(需TDB)"
        print("  0x%08x [%s] (%3d次): %s" % (tid & 0x7fffffff, kind, cnt, (tid_sample.get(tid, "") or "(纯二进制, 需TDB解码)")[:65]))

    # ---- 关键词 (--no-kw 可关闭) ----
    if args.kw:
        print("\n--- 关键 trace (ftp/fota/malloc/socket/assert/panic/error/download) ---")
        kw = re.compile(r'(ftp|fota|download|malloc|socket|sock|dss_read|assert|panic|'
                        r'error|abort|stack|overflow|heap|netconn|pbuf|connect|NV|fupdate)', re.I)
        kw_evs = [e for e in events if e[3] and kw.search(e[3])]
        for tick, counter, tid, text in kw_evs:
            print("  tick=%-8d 0x%08x %s" % (tick, tid, text[:95]))
        print("共 %d 条关键命中" % len(kw_evs))

    print("\n" + "=" * 80)
    print(" 解读: trace 文本含 [文件:行 函数] 源码位置, 还原崩溃前调用活动。")
    print("       关注崩溃前最后的事件 (tick 最大) — 往往指向触发死机的代码路径。")
    print("=" * 80)


if __name__ == "__main__":
    main()
