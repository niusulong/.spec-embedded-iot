#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 RAM Dump 分析器主入口。

平台：Unisoc QCX216 / Neoway N706D，ARM Cortex-M3 + FreeRTOS。
采集：Unisoc DTools（RamDumpData_*.bin + comdb.txt + ap_*.elf）。
工具链假设：无 ARM binutils / 无 capstone；用 pyelftools 完成 符号解析 + DWARF 行号映射。

子命令：
  full-analyze <dump> --elf <elf>   一键全流程（异常 + 任务 + 栈 + 根因）
  parse-excep  <dump> --elf <elf>   仅解析 excepInfoStore
  resolve      <addr>... --elf <elf>  地址 -> 符号 / 源码行
  scan-stacks  <dump> --elf <elf>   任务栈溢出扫描
"""
import argparse
import os
import sys

# Windows 终端默认 GBK，强制 stdout/stderr 用 UTF-8，避免中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from qcx216_common import DumpReader, u32, make_mem  # noqa: E402
from qcx216_elf import ElfReader  # noqa: E402
from qcx216_excep import parse_excep, format_excep  # noqa: E402
from qcx216_tasks import format_tasks, backtrace, format_backtrace  # noqa: E402
from qcx216_disasm import ThumbDisasm, find_assert_failure_point  # noqa: E402
from qcx216_heap import format_heap  # noqa: E402
from qcx216_fault import format_fault  # noqa: E402
from qcx216_osa_pool import format_osa_pool  # noqa: E402


def banner(title: str) -> str:
    line = "=" * 72
    return f"{line}\n {title}\n{line}"


def format_header(args, elf, dump_size: int) -> str:
    lines = [banner("QCX216 RAM Dump Analysis")]
    lines.append(f"  Dump      : {args.dump}")
    lines.append(f"  Dump size : {dump_size} bytes (0x{dump_size:X})")
    lines.append(f"  ELF       : {args.elf}")
    lines.append(f"  Arch      : {elf.arch}   entry=0x{elf.entry:08X}")
    # 只展示较大的段（>=4KB），过滤掉 .rodata/向量表等零碎小段避免刷屏
    big_code = [(a, b) for a, b in elf.code_ranges if b - a >= 0x1000]
    big_ram = [(a, b) for a, b in elf.ram_ranges if b - a >= 0x1000]
    lines.append("  Code ranges: " +
                 ", ".join(f"0x{a:08X}-0x{b:08X}" for a, b in big_code))
    lines.append("  RAM ranges : " +
                 ", ".join(f"0x{a:08X}-0x{b:08X}" for a, b in big_ram))
    return "\n".join(lines)


def format_summary(res: dict, elf, data: bytes) -> str:
    lines = [banner("Root-Cause Summary")]
    etype = res.get("type")
    a = res.get("assert") or {}

    if etype == "ASSERT" and (a.get("func") or a.get("line")):
        lines.append(f"  Crash type : ASSERT  (context: {a.get('context') or '?'})")
        lines.append(f"  Function   : {a.get('func')}   (assert @ line {a.get('line')})")
        lines.append(f"  Values     : {a.get('val')}")
        # 在寄存器快照区里找与 assert Func 同名的代码地址，作为触发点
        trig = None
        for _off, addr in res.get("code_addrs", []):
            loc = elf.locate(addr)
            if loc["symbol"] and a.get("func") and a["func"] in loc["symbol"]:
                trig = loc
                break
        if trig:
            lines.append(
                f"  Trigger    : 0x{trig['addr']:08X} -> {trig['symbol']}+0x{trig['sym_offset']:X}"
                + (f"   [{trig['file']}:{trig['line']}]" if trig["file"] else "")
            )
        lines.append("")
        lines.append("  说明：OsaCreateFastSignal / OsaCreateIsrSignal 等 OSA API 属 Unisoc")
        lines.append("        协议栈二进制库，源码通常不在仓内。需结合 Val 值与中断/任务")
        lines.append("        调用上下文，对照协议栈行为进一步定位（参见 references）。")
    elif etype == "HardFault":
        lines.append("  Crash type : HardFault (Cortex-M fault)")
        lines.append("  解析建议：检查寄存器快照区代码地址作为 PC/LR/调用链候选，")
        lines.append("            并结合 reset 原因与 fault status 进一步判断。")
    else:
        lines.append(f"  Crash type : {etype}（excepInfoStore 无有效 magic 或无 assert 文本）")
        lines.append("  可能是静默复位 / 看门狗 / 无异常数据，需结合 EPAT 日志排查。")
    return "\n".join(lines)


def find_trigger_addr(res, elf):
    """从异常结果里找出触发点地址（与 assert Func 同名的代码地址，否则首个候选）。"""
    if not res:
        return None
    a = res.get("assert")
    if a and a.get("func"):
        for _off, addr in res.get("code_addrs", []):
            loc = elf.locate(addr)
            if loc["symbol"] and a["func"] in loc["symbol"]:
                return addr
    if res.get("code_addrs"):
        return res["code_addrs"][0][1]
    return None


def _deepest_sp(sp_cands):
    """选最深的 MSP（地址最小）作为调用链回溯起点；无 MSP 则取最小 RAM SP。"""
    msps = [sp for _o, sp, k in sp_cands if k == "MSP"]
    if msps:
        return min(msps)
    rams = [sp for _o, sp, k in sp_cands if k == "RAM"]
    return min(rams) if rams else None


def format_backtrace_section(data, elf, sp, sp_top) -> str:
    lines = [banner("Call Chain (stack backtrace)")]
    chain = backtrace(data, elf, sp, sp_top)
    lines.append(format_backtrace(chain, elf, sp, title=f"from SP=0x{sp:08X}"))
    # 中断源语义：从调用链里的 ISR 符号识别
    isr_sem = {
        "XIC_IntHandler": "外部中断控制器(XIC)分发",
        "ACIpcAlone0Isr": "AC 核间通信(IPC) 中断0",
        "ACIpcAlone1Isr": "AC 核间通信(IPC) 中断1 (CP→AP 消息)",
        "MPDMA_interruptHandler": "通用 DMA 中断",
        "CO_USART_IRQHandler": "USART 串口中断",
    }
    isr_hits = []
    for _o, addr in chain:
        loc = elf.locate(addr)
        if loc["symbol"]:
            base = loc["symbol"].split("+")[0]
            if base in isr_sem or "Isr" in base or "IRQHandler" in base or "_IntHandler" in base:
                sem = isr_sem.get(base, "中断处理函数")
                if base not in [h[0] for h in isr_hits]:
                    isr_hits.append((base, sem))
    if isr_hits:
        lines.append("")
        lines.append("  ### Interrupt source")
        for base, sem in isr_hits:
            lines.append(f"    {base} -> {sem}")
    return "\n".join(lines)


def format_reset_reason(data, elf) -> str:
    lines = [banner("Reset / Watchdog Context")]
    syms = ["ramRstReason", "gPendingReset", "hibresetcnt",
            "gWdtDataBase", "gCmiAppWatchdogTimer"]
    any_found = False
    for n in syms:
        s = elf.find_symbol(n)
        if s:
            v = u32(data, s.addr)
            lines.append(f"  {n:26} = 0x{v:08X}" if v is not None else f"  {n:26} = (n/a)")
            any_found = True
    rr = elf.find_symbol("ramRstReason")
    if rr:
        v = u32(data, rr.addr)
        if v is not None and v > 0x1000:
            lines.append(f"  注: ramRstReason=0x{v:08X} 非小整数，可能被异常转储覆盖或为 magic/校验和")
    if not any_found:
        lines.append("  (无 reset reason 符号)")
    return "\n".join(lines)


def format_disasm_section(dis, trigger, elf) -> str:
    lines = [banner("Disassembly around trigger")]
    lines.append(f"  trigger = 0x{trigger:08X}  (Thumb 地址最低位已对齐)")
    lines.append(dis.format_around(trigger, before_words=4, after_words=6,
                                   sym_resolver=elf.locate))
    return "\n".join(lines)


def cmd_full_analyze(args):
    elf = ElfReader(args.elf)
    dr = DumpReader(args.dump)
    data = dr.data
    out = [format_header(args, elf, dr.size)]

    store_sym = elf.find_symbol("excepInfoStore")
    res = None
    if store_sym:
        res = parse_excep(data, elf, store_sym.addr)
        out.append(format_excep(res, elf))
        out.append(format_summary(res, elf, data))
    else:
        out.append("\n[!] excepInfoStore symbol not found in ELF; exception parse skipped.")

    # 触发点附近反汇编（纯 Python Thumb-2，无需 capstone）
    trigger = find_trigger_addr(res, elf)
    if trigger:
        out.append(format_disasm_section(ThumbDisasm(make_mem(dr, elf)), trigger, elf))

    # assert 失败点推理（P1）：反汇编 assert 函数，定位「BL X → CBZ r0 → assert」的真正失败调用
    if res and res.get("type") == "ASSERT" and trigger:
        loc = elf.locate(trigger)
        if loc.get("sym_base"):
            fps = find_assert_failure_point(ThumbDisasm(make_mem(dr, elf)), loc["sym_base"])
            if fps:
                a = res.get("assert") or {}
                hit_line = a.get("line")
                lines = [banner("ASSERT Failure Point (inferred)")]
                lines.append(f"  assert 函数入口: 0x{loc['sym_base']:08X} ({loc['symbol']})")
                for bl_tgt, bl_txt, cbz_txt, bl_addr, aline in fps:
                    fp_loc = elf.locate(bl_tgt)
                    fp_sym = fp_loc["symbol"] or "?"
                    mark = " ★ 本次触发" if (hit_line and aline == hit_line) else ""
                    ln = f" @line {aline}" if aline else ""
                    lines.append(f"  {bl_txt} @0x{bl_addr:08X} -> {fp_sym}{ln}{mark}")
                    if cbz_txt:
                        lines.append(f"    判定: {cbz_txt}  (r0==0 进入 assert)")
                out.append("\n".join(lines))

    # 调用链回溯：从异常 SP 扫描栈里的代码地址，还原完整调用链
    if res and res.get("sp_candidates"):
        sp = _deepest_sp(res["sp_candidates"])
        top = elf.find_symbol("__StackTop")
        if sp and top:
            out.append(format_backtrace_section(data, elf, sp, top.addr))

    # Reset / WDT 上下文
    out.append(format_reset_reason(data, elf))

    # Heap 利用率
    out.append(format_heap(data, elf))

    # OSA 协议栈专用内存池（OsaCreate*Signal / OsaMemPoolIdAlloc 用，独立于主 TLSF 堆）
    # 从 assert Val 第一个值提取 sigBodySize，推理本次用的 poolId
    sigbody = None
    if res and res.get("assert"):
        val = res["assert"].get("val")
        if val:
            try:
                sigbody = int(str(val).split(",")[0], 0)
            except (ValueError, IndexError):
                pass
    out.append(format_osa_pool(data, elf, sigbody_size=sigbody))

    out.append(format_tasks(data, elf))
    print("\n\n".join(out))
    elf.close()


def cmd_parse_excep(args):
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    store_sym = elf.find_symbol("excepInfoStore")
    if not store_sym:
        print("[!] excepInfoStore symbol not found in ELF.")
        elf.close()
        return
    res = parse_excep(data, elf, store_sym.addr)
    print(format_excep(res, elf))
    print()
    print(format_summary(res, elf, data))
    elf.close()


def cmd_resolve(args):
    elf = ElfReader(args.elf)
    for a in args.addrs:
        addr = int(a, 0)
        loc = elf.locate(addr)
        sym = loc["symbol"] or "?"
        off = f"+0x{loc['sym_offset']:X}" if loc["sym_offset"] is not None else ""
        src = f"   [{loc['file']}:{loc['line']}]" if loc["file"] else ""
        flag = " (code)" if loc["is_code"] else (" (data)" if not loc["is_code"] and loc["symbol"] else "")
        print(f"0x{addr:08X} -> {sym}{off}{src}{flag}")
    elf.close()


def cmd_scan_stacks(args):
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    print(format_tasks(data, elf))
    elf.close()


def cmd_scan_osa_pool(args):
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    print(format_osa_pool(data, elf))
    elf.close()


def cmd_disasm(args):
    elf = ElfReader(args.elf)
    dr = DumpReader(args.dump)
    dis = ThumbDisasm(make_mem(dr, elf))
    addr = int(args.addr, 0) & ~1
    # 仅解析跳转目标的符号名（不构建 DWARF 行号表 → 秒级返回）
    def sym_only(a):
        s = elf.sym_at(a)
        return {"symbol": s.name, "sym_offset": a - s.addr} if s else None

    print(f"  disasm around 0x{addr:08X}")
    print(dis.format_around(addr, before_words=args.before, after_words=args.after,
                            sym_resolver=sym_only))
    elf.close()


def main():
    p = argparse.ArgumentParser(
        description="QCX216 (Unisoc Cortex-M3 + FreeRTOS) RAM dump analyzer")
    sub = p.add_subparsers(dest="cmd", required=True)

    pa = sub.add_parser("full-analyze", help="一键全流程分析（异常+任务+栈+根因）")
    pa.add_argument("dump", help="RamDumpData_*.bin 路径")
    pa.add_argument("--elf", required=True, help="崩溃固件 ap_*.elf 路径")
    pa.set_defaults(func=cmd_full_analyze)

    pe = sub.add_parser("parse-excep", help="仅解析 excepInfoStore")
    pe.add_argument("dump"); pe.add_argument("--elf", required=True)
    pe.set_defaults(func=cmd_parse_excep)

    rs = sub.add_parser("resolve", help="地址 -> 符号 / 源码行")
    rs.add_argument("addrs", nargs="+"); rs.add_argument("--elf", required=True)
    rs.set_defaults(func=cmd_resolve)

    ss = sub.add_parser("scan-stacks", help="任务栈溢出扫描")
    ss.add_argument("dump"); ss.add_argument("--elf", required=True)
    ss.set_defaults(func=cmd_scan_stacks)

    op = sub.add_parser("scan-osa-pool", help="OSA 协议栈专用内存池扫描 (signal 池耗尽/泄漏)")
    op.add_argument("dump"); op.add_argument("--elf", required=True)
    op.set_defaults(func=cmd_scan_osa_pool)

    ds = sub.add_parser("disasm", help="反汇编地址附近指令 (纯 Python Thumb-2，无需 capstone)")
    ds.add_argument("addr", help="目标地址 (崩溃 PC/LR 等)")
    ds.add_argument("--dump", required=True, help="dump 路径 (提供代码字节)")
    ds.add_argument("--elf", required=True)
    ds.add_argument("--before", type=int, default=4, help="目标前反汇编半字数")
    ds.add_argument("--after", type=int, default=6, help="目标后反汇编字数")
    ds.set_defaults(func=cmd_disasm)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
