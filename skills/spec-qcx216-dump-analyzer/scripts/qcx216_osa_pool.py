#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 OSA 协议栈专用内存池分析。

Unisoc OSA 的 `OsaCreate*Signal` 不走主 TLSF 堆，而调 `OsaMemPoolIdAlloc(poolId, ...)`
从 `osaMemPoolDescList[3]` 专用池分配。**满/空判定用 desc 簿记（非 slot 内容）**。

⚠️ 关键认知（capstone 反汇编 OsaCreateFastSignal + OsaMemPoolIdAlloc 还原，曾因纯 Python
反汇编器漏解 ITE 条件块而误判 poolId）：

  1. **poolId 动态选择**（OsaCreateFastSignal 内 ITE LS）：
       r8 = sigBodySize + 4
       if r8 <= 36: poolId = 1  (pool[1], blksize=36, 小信号)
       else:        poolId = 2  (pool[2], blksize=132, Fast Signal)
     故 OsaCreateFastSignal(sigBodySize=12) → poolId=1（不是 2）！

  2. **满判定**（OsaMemPoolIdAlloc）：
       freeHead = desc[poolId] + 0x10   (u32)
       if freeHead == 0 (NULL) → 返回 NULL → 触发 osasig.c:146 assert
     即 desc+0x10 是空闲链头；NULL = 池满。desc+4(u16) 是在用计数（=total 时满）。

  3. **block 布局（实测，读 slot 归类堆积用）**：stride = blksize + 4。每块：
       block+0 magic u16=0xD5E9(固定,★非free标记★)  +2 poolId u8  +3 flag u8(1=free/2=alloc)
       +4 sigId u16(alloc)/next u32(free)  +6 sigBodyLen u16  +8.. sigBody
     free/alloc 看 block+3 flag（不是 magic 0xD5E9）；满/空看 desc 簿记（不遍历 slot）。
     ⚠️ 曾误把 magic 当 free 标记、block+4(sigId) 当 next 指针 → "链表损坏"错论，实际簿记与物理一致。
  4. **sigId 可由 IPC msgId 动态计算**（如 IpcC2AMsg2Cms: sigId=(msgId-0x27BD)&0xFFFF），
     故代码段搜不到 sigId 立即数时，反汇编创建函数看计算逻辑（7031160371: sigId=0x0949←msgId=0x3106）。

desc 结构（osaMemPoolDescList[3] @0x425550，每池 24B）：
  u16(+0)=blksize  u16(+2)=total  u16(+4)=在用计数  u32(+8)=base  u32(+C)=end
  u32(+10)=freeHead(空闲链头,NULL=满)  u32(+14)=tail
"""
from qcx216_common import u16, u32

# poolId 选择阈值（OsaCreateFastSignal: sigBodySize+4 <= 36 → pool1）
POOL1_MAX_BODY = 32      # sigBodySize <= 32 (+4=36) → pool[1]

SIGID_MAP = {
    0x0100: "SIG_TIMER_EXPIRY", 0x0101: "SIG_HIB_TIMER_EXPIRY",
    0x0102: "SIG_OSA_FAST_IPC", 0x0110: "SIG_OSA_SIG_END",
}


def pool_id_for_sigbody(sigbody_size: int) -> int:
    """推理 OsaCreateFastSignal 用的 poolId（基于 sigBodySize）。"""
    return 1 if (sigbody_size + 4) <= (POOL1_MAX_BODY + 4) else 2


def analyze_osa_pool(data: bytes, elf):
    """读 osaMemPoolDescList 三池的分配器簿记（blksize/total/计数/freeHead）。"""
    desc_sym = elf.find_symbol("osaMemPoolDescList")
    if not desc_sym:
        return None
    pools = []
    for p in range(3):
        da = desc_sym.addr + p * 24
        blksize = u16(data, da + 0) or 0
        total = u16(data, da + 2) or 0
        used = u16(data, da + 4) or 0
        base = u32(data, da + 8) or 0
        end = u32(data, da + 12) or 0
        free_head = u32(data, da + 16) or 0
        if not base:
            pools.append({"id": p, "valid": False})
            continue
        full = (free_head == 0) or (total and used >= total)
        pools.append({
            "id": p, "valid": True, "blksize": blksize, "total": total,
            "used": used, "base": base, "end": end, "free_head": free_head,
            "full": full,
        })
    return pools


def analyze_pool_slots(data: bytes, elf, pool_id: int):
    """按正确 block 布局遍历指定池的槽，归类 allocated 信号的 sigId（定位堆积元凶）。
    block: +0 magic(0xD5E9) +2 poolId +3 flag(1=free/2=alloc) +4 sigId(u16) +6 sigBodyLen(u16)
    stride = blksize + 4。返回 {pool_id, stride, flag_dist, alloc_sig_dist}。
    """
    from collections import Counter
    desc_sym = elf.find_symbol("osaMemPoolDescList")
    if not desc_sym:
        return None
    da = desc_sym.addr + pool_id * 24
    blksize = u16(data, da) or 0
    total = u16(data, da + 2) or 0
    base = u32(data, da + 8) or 0
    if not base or not total:
        return None
    stride = blksize + 4
    flag_cnt = Counter()
    sig_cnt = Counter()
    for i in range(total):
        a = base + i * stride
        if a + 8 > len(data):
            break
        flag_cnt[data[a + 3]] += 1
        if data[a + 3] != 1:  # 非 free(1) → allocated，读 sigId@+4
            sig_cnt[u16(data, a + 4)] += 1
    return {"pool_id": pool_id, "stride": stride,
            "flag_dist": dict(flag_cnt),
            "alloc_sig_dist": list(sig_cnt.most_common())}


def format_osa_pool(data: bytes, elf, sigbody_size: int = None) -> str:
    pools = analyze_osa_pool(data, elf)
    lines = ["## OSA Signal Memory Pools (osaMemPoolDescList)"]
    if not pools:
        lines.append("  (未找到 osaMemPoolDescList)")
        return "\n".join(lines)

    # 若给了 sigBodySize，推理本次 assert 用的池
    hit_pool = None
    if sigbody_size is not None:
        hit_pool = pool_id_for_sigbody(sigbody_size)
        lines.append(f"  OsaCreateFastSignal(sigBodySize={sigbody_size}) → "
                     f"+4={sigbody_size+4} {'<=36' if sigbody_size+4<=36 else '>36'} "
                     f"⇒ poolId={hit_pool}")

    lines.append(f"  {'pool':<6} {'blksize':<8} {'total':<6} {'used(cnt)':<10} "
                 f"{'freeHead(+0x10)':<14} 状态")
    for pl in pools:
        if not pl.get("valid"):
            lines.append(f"  pool[{pl['id']}]: (无效)")
            continue
        fh = f"0x{pl['free_head']:08X}" if pl["free_head"] else "NULL(满!)"
        status = "*** 满 ***" if pl["full"] else "有空闲"
        if hit_pool is not None and pl["id"] == hit_pool:
            status = "★★ " + status + " (本次 assert 用此池)"
        lines.append(f"  pool[{pl['id']}] {pl['blksize']:<8} {pl['total']:<6} "
                     f"{pl['used']:<10} {fh:<14} {status}")
        # 满池：按正确 block 布局归类堆积的 sigId（定位元凶）
        if pl["full"]:
            sl = analyze_pool_slots(data, elf, pl["id"])
            if sl and sl["alloc_sig_dist"]:
                lines.append(f"    └ 堆积 sigId 归类 (stride={sl['stride']}, flag分布={sl['flag_dist']}):")
                for sid, c in sl["alloc_sig_dist"]:
                    name = SIGID_MAP.get(sid, "(协议栈私有, 反汇编创建函数查计算逻辑)")
                    lines.append(f"       sigId=0x{sid:04X} × {c}  {name}")

    lines.append("")
    lines.append("  判据: freeHead(desc+0x10)==NULL 或 used>=total 即满（OsaMemPoolIdAlloc 据此返回 NULL）。")
    lines.append("  block 布局: +0 magic(0xD5E9,固定) +2 poolId +3 flag(1=free/2=alloc) "
                 "+4 sigId +6 sigBodyLen, stride=blksize+4。")
    lines.append("  注: 0xD5E9 是 block 固定 magic(非 free 标记)；free/alloc 看 block+3 flag；满/空看 desc 簿记。")
    return "\n".join(lines)
