#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QCX216 RAM Dump 分析器主入口。

平台：Unisoc QCX216 / Neoway N706D，ARM Cortex-M3 + FreeRTOS。
采集：Unisoc DTools（RamDumpData_*.bin + comdb.txt + ap_*.elf）。
工具链：优先用仓库自带 arm-none-eabi objdump/nm（PLAT/tools/gcc，权威反汇编+源码行），
       pyelftools 兜底符号/DWARF；无工具链时降级 capstone/纯 Python Thumb-2 反汇编。

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
import qcx216_toolchain as arm_tc  # noqa: E402  ARM objdump/nm 工具链（优先反汇编后端）


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
    lines.append(f"  Toolchain : {arm_tc.toolchain_status()}")
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
        fr = res.get("frame")
        if fr:
            loc = elf.locate(fr["pc"] & ~1)
            sym = loc["symbol"] or "?"
            off = loc.get("sym_offset") or 0
            src = f"  [{loc['file']}:{loc['line']}]" if loc["file"] else ""
            lines.append(f"  Faulting PC: 0x{fr['pc']:08X} -> {sym}+0x{off:X}{src}")
            if fr.get("lr"):
                lloc = elf.locate(fr["lr"] & ~1)
                if lloc and lloc["symbol"]:
                    lines.append(f"  Caller LR : 0x{fr['lr']:08X} -> {lloc['symbol']}  (调用者)")
            lines.append(f"  Context   : {fr.get('mode')}  (EXC_RETURN=0x{fr['exc_return']:08X})")
            lines.append("  下一步：disasm <PC> 看崩溃指令语义；code-compare 排除代码损坏；")
            lines.append("        链表损坏取证（若 PC 在 vListInsert/uxListRemove 等，见 heap-corruption-guide）。")
        else:
            lines.append("  （未还原出异常帧；用寄存器快照区代码地址作 PC/LR 候选，")
            lines.append("   结合 reset 原因排查；可能是静默复位/WDT）")
    else:
        lines.append(f"  Crash type : {etype}（excepInfoStore 无有效 magic 或无 assert 文本）")
        lines.append("  可能是静默复位 / 看门狗 / 无异常数据，需结合 EPAT 日志排查。")
    return "\n".join(lines)


def find_trigger_addr(res, elf):
    """触发点地址：HardFault 优先异常帧 PC（崩溃指令铁证，最准）；ASSERT 用 Func 同名代码地址。"""
    if not res:
        return None
    # HardFault：异常帧 PC = 崩溃指令（比 code_addrs[0]=可能 LR 更准）
    fr = res.get("frame")
    if fr and fr.get("pc"):
        return fr["pc"] & ~1
    # ASSERT：与 Func 同名的代码地址
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


def render_disasm_around(elf, dr, center, before_words=4, after_words=6):
    """反汇编 center 附近，统一后端选择：objdump（权威+源码行）→ capstone → 纯 Python。

    返回 (text, backend)。objdump 优先（无 capstone 依赖、正确解 ITE/MSR/宽指令），
    其失败时降级 ThumbDisasm（capstone 或纯 Python）。
    """
    center &= ~1
    ins = arm_tc.objdump_disasm(elf.path, max(0, center - before_words * 2),
                                center + after_words * 2 + 4, with_line=True)
    if ins:
        lines = []
        last_fl = None
        hit = False
        for addr, mnem, ops, fl in ins:
            if fl and fl != last_fl:
                lines.append("    [%s]" % fl)
                last_fl = fl
            star = "  << crash" if addr == center else ""
            if addr == center:
                hit = True
            lines.append("    0x%08X:  %s %s%s" % (addr, mnem, ops, star))
        if not hit:
            lines.append("    (注: 目标 0x%08X 不在 objdump 反汇编范围)" % center)
        return ("\n".join(lines), "objdump")
    # 降级 capstone / 纯 Python（format_around 会触发 DWARF 行号构建，较慢）
    dis = ThumbDisasm(make_mem(dr, elf))
    txt = dis.format_around(center, before_words=before_words,
                            after_words=after_words, sym_resolver=elf.locate)
    return (txt, "capstone" if dis.use_cs else "python")


def format_disasm_section(elf, dr, trigger) -> str:
    lines = [banner("Disassembly around trigger")]
    lines.append(f"  trigger = 0x{trigger:08X}  (Thumb 地址最低位已对齐)")
    txt, backend = render_disasm_around(elf, dr, trigger, before_words=4, after_words=6)
    lines.append(txt)
    lines.append(f"  (backend: {backend})")
    return "\n".join(lines)


def format_crash_ptrs_block_status(frame: dict, elf, data: bytes) -> str:
    """HardFault：崩溃帧 R0-R3 指向地址的 MM_DEBUG 块状态（哪些是悬空/越界块）。

    崩溃指令（STR/LDR）的访存基址多在 R0/R1/R2。查这些地址的块状态：
    FREE 块 → 悬空/use-after-free；USED → 正常，可看分配者。无需单独子命令，full-analyze 自动。
    """
    from qcx216_heap import find_enclosing_block
    lines = [banner("Crash registers -> block status (find_enclosing_block)")]
    found = False
    for nm in ("r0", "r1", "r2", "r3"):
        v = frame.get(nm)
        if not v or v >= len(data) or v < 0x100:
            continue
        blk = find_enclosing_block(data, v)
        if not blk:
            continue
        found = True
        st = "FREE (已释放)" if blk["free"] else "USED"
        own = ""
        if not blk["free"]:
            fp = blk["owner"] & 0xFFFFFF
            tn = (blk["owner"] >> 24) & 0xFF
            s = elf.sym_at(fp)
            nm2 = s.name if s and s.name else ("0x%X" % fp)
            own = "  owner=%s (task %d)" % (nm2, tn)
        flag = "  ⚠️ 悬空/use-after-free" if blk["free"] else ""
        lines.append(f"  {nm.upper():<3} = 0x{v:08X} -> 块@0x{blk['hdr']:08X} size=0x{blk['size']:X}"
                     f" 偏移+0x{blk['off']:X}  {st}{own}{flag}")
    if not found:
        lines.append("  (R0-R3 均不落在任何 MM_DEBUG 块内)")
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

    # 触发点附近反汇编（优先 objdump 权威反汇编+源码行，降级 capstone/纯 Python）
    trigger = find_trigger_addr(res, elf)
    if trigger:
        out.append(format_disasm_section(elf, dr, trigger))

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

    # Heap 利用率（含物理完整性 head_bound 校验：越界写/double-free 会破坏块头）
    out.append(format_heap(data, elf))

    # HardFault：崩溃帧寄存器指向地址的块状态（悬空/越界线索）
    if res and res.get("type") == "HardFault" and res.get("frame"):
        out.append(format_crash_ptrs_block_status(res["frame"], elf, data))

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


def cmd_frame(args):
    """单独输出 Cortex-M 异常帧（HardFault 快速查看崩溃 PC/LR/寄存器）。"""
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    store_sym = elf.find_symbol("excepInfoStore")
    if not store_sym:
        print("[!] excepInfoStore symbol not found in ELF.")
        elf.close()
        return
    res = parse_excep(data, elf, store_sym.addr)
    fr = res.get("frame")
    if not fr:
        print(banner("Cortex-M Exception Frame"))
        print(f"  未还原出异常帧（Exception Type = {res.get('type')}）。")
        print("  ASSERT 为软件触发（无硬件帧）；HardFault 无 EXC_RETURN 则 store 布局可能变异，")
        print("  人工查 excepInfoStore 头部 0xFFFFFFFD/F9/F1 + 栈范围（见 cortex-m-exception-guide）。")
        elf.close()
        return
    print(format_excep({"store_addr": store_sym.addr, "magic1": res["magic1"],
                        "magic2": res["magic2"], "valid": res["valid"],
                        "type": res["type"], "frame": fr}, elf))
    elf.close()


def cmd_resolve(args):
    elf = ElfReader(args.elf)
    addrs = [int(a, 0) for a in args.addrs]
    # 先 pyelftools locate（主），无源码行的代码地址收集后批量 objdump 补
    rows, need_objdump = [], []
    for addr in addrs:
        loc = elf.locate(addr)
        sym = loc["symbol"] or "?"
        off = f"+0x{loc['sym_offset']:X}" if loc["sym_offset"] is not None else ""
        srcsrc = (loc["file"], loc["line"]) if loc["file"] else None
        is_code = loc["is_code"]
        if not srcsrc and is_code:
            need_objdump.append(addr)
        rows.append((addr, sym, off, srcsrc, is_code))
    # 一次 objdump 批量补源码行（无 addr2line，用 objdump -d -l）
    obj_map = arm_tc.objdump_addr2line_batch(args.elf, need_objdump) if need_objdump else {}
    for (addr, sym, off, srcsrc, is_code) in rows:
        if not srcsrc and addr in obj_map:
            p, _, ln = obj_map[addr].rpartition(":")
            srcsrc = (p, ln)
        src = f"   [{srcsrc[0]}:{srcsrc[1]}]" if srcsrc else ""
        flag = " (code)" if is_code else (" (data)" if sym and not is_code else "")
        print(f"0x{addr:08X} -> {sym}{off}{src}{flag}")
    elf.close()


def cmd_scan_stacks(args):
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    print(format_tasks(data, elf))
    elf.close()


def cmd_wdt_reset(args):
    """复位原因 + WDT 状态：区分蓝屏(异常转储) vs 真复位(看门狗/上电)。"""
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    print(format_reset_reason(data, elf))
    elf.close()


def cmd_scan_osa_pool(args):
    elf = ElfReader(args.elf)
    data = DumpReader(args.dump).data
    print(format_osa_pool(data, elf))
    elf.close()


def cmd_disasm(args):
    elf = ElfReader(args.elf)
    dr = DumpReader(args.dump)
    addr = int(args.addr, 0) & ~1
    txt, backend = render_disasm_around(elf, dr, addr, args.before, args.after)
    print(f"  disasm around 0x{addr:08X}  (backend: {backend})")
    print(txt)
    elf.close()


def cmd_code_compare(args):
    import qcx216_code_compare as cc
    argv = [args.dump, args.elf]
    if args.pc:
        argv += ["--pc", args.pc]
    cc.main(argv)


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

    fr = sub.add_parser("frame", help="Cortex-M 异常帧（崩溃 PC/LR/寄存器，HardFault 快查）")
    fr.add_argument("dump"); fr.add_argument("--elf", required=True)
    fr.set_defaults(func=cmd_frame)

    rs = sub.add_parser("resolve", help="地址 -> 符号 / 源码行")
    rs.add_argument("addrs", nargs="+"); rs.add_argument("--elf", required=True)
    rs.set_defaults(func=cmd_resolve)

    ss = sub.add_parser("scan-stacks", help="任务栈溢出扫描")
    ss.add_argument("dump"); ss.add_argument("--elf", required=True)
    ss.set_defaults(func=cmd_scan_stacks)

    th = sub.add_parser("threads", help="任务列表 + 栈水位 (同 scan-stacks，UIS8852 风格命名)")
    th.add_argument("dump"); th.add_argument("--elf", required=True)
    th.set_defaults(func=cmd_scan_stacks)

    wr = sub.add_parser("wdt-reset", help="复位原因 + WDT 状态（蓝屏 vs 真复位 / WDT 超时）")
    wr.add_argument("dump"); wr.add_argument("--elf", required=True)
    wr.set_defaults(func=cmd_wdt_reset)

    op = sub.add_parser("scan-osa-pool", help="OSA 协议栈专用内存池扫描 (signal 池耗尽/泄漏)")
    op.add_argument("dump"); op.add_argument("--elf", required=True)
    op.set_defaults(func=cmd_scan_osa_pool)

    ds = sub.add_parser("disasm", help="反汇编地址附近指令 (优先 objdump 权威反汇编+源码行，降级 capstone/纯Python)")
    ds.add_argument("addr", help="目标地址 (崩溃 PC/LR 等)")
    ds.add_argument("--dump", required=True, help="dump 路径 (提供代码字节)")
    ds.add_argument("--elf", required=True)
    ds.add_argument("--before", type=int, default=4, help="目标前反汇编半字数")
    ds.add_argument("--after", type=int, default=6, help="目标后反汇编字数")
    ds.set_defaults(func=cmd_disasm)

    cc = sub.add_parser("code-compare", help="代码完整性：ELF 代码段 vs dump (HardFault 排除代码损坏)")
    cc.add_argument("dump"); cc.add_argument("--elf", required=True)
    cc.add_argument("--pc", default=None, help="崩溃 PC，spotlight 该指令 ELF vs dump")
    cc.set_defaults(func=cmd_code_compare)


    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
