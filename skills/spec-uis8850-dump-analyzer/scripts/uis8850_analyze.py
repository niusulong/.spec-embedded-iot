#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 (ARM) AP crash-dump entry-point analyzer.

Reconstructs the panic context from a DTools ramdump + AP ELF:
  - firmware version (dual-ELF + PSRAM string search)
  - gIsPanic, gBlueScreenAbortType, gBlueScreenRegs (ARM register context)
  - osiPanic mechanism (udf #255)
  - FreeRTOS stack-overflow detection point (vTaskSwitchContext)
  - pxCurrentTCB + task name

Usage:  python uis8850_analyze.py <dump_dir> <ap.elf> [--elf2 <other_elf>] [--map <map>]
"""
import os, sys, re
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import (Symbols, addr2line_batch, objdump_range, thumb_real, cpsr_decode,
                    parse_dump_args, load_ctx, tcb_offsets,
                    FREERTOS_STACK_MAGIC, STACK_SCAN_WINDOW)


def main():
    args = parse_dump_args("UIS8850 AP crash-dump entry-point analyzer",
                           want_elf2=True, want_map=True)
    mem, syms, addr2line, objdump, tc = load_ctx(args.dump_dir, args.ap_elf)
    print("(toolchain: %s)" % tc)
    print("(ELF: %s)" % args.ap_elf)

    def S(n):
        return syms.lookup(n)[0]

    print("=" * 92)
    print(" UIS8850 / N706-STD  AP 死机现场分析（ARM Cortex-R + FreeRTOS）")
    print("=" * 92)

    # ---- 0. 内存布局（从 ELF PT_LOAD, 非硬编码）----
    print("\n--- 内存布局 (ELF PT_LOAD, 非硬编码) ---")
    for v, sz in syms.ptload_ranges():
        print("  PT_LOAD vaddr=0x%08x memsz=0x%-8x" % (v, sz))
    print("  已注册 dump .bin 区段数: %d" % len(mem.maps))

    # ---- 1. 版本判定 ----
    print("\n--- 固件版本 ---")
    rev_addr = S("gBuildRevision")
    if rev_addr:
        print("gBuildRevision @0x%08x : %s" % (rev_addr, mem.cstr(rev_addr, 80).strip()))
    # PSRAM 全文搜版本串（不依赖 ELF，最可靠）
    try:
        psram = open(os.path.join(args.dump_dir, "80000000.bin"), "rb").read()
        # 从 gBuildRevision 串里取前缀特征
        prefix = None
        if rev_addr:
            full = mem.cstr(rev_addr, 80)
            # 取 "xxx-release-" 或 "xxx-svn" 前的 product 名
            for sep in [b"-release", b"-svn", b"_release"]:
                idx = full.encode(errors="replace").find(sep)
                if idx > 0:
                    prefix = full[:idx].encode(errors="replace")
                    break
        if prefix is None:
            prefix = b"cat1bis"
        pos = 0
        found = set()
        while True:
            i = psram.find(prefix, pos)
            if i < 0: break
            end = psram.find(b"\x00", i)
            if end < 0: end = i + 100
            s = psram[i:end]
            try: txt = s.decode("utf-8", "replace")
            except: txt = repr(s)
            if len(txt) > 10:
                found.add(txt)
            pos = i + 1
        print("  PSRAM 搜到 %d 种版本串:" % len(found))
        for t in sorted(found):
            print("    %s" % t[:90])
    except Exception as e:
        print("  PSRAM 搜串失败: %s" % e)
    # 双 ELF 比对
    if args.elf2:
        try:
            syms2 = Symbols(args.elf2)
            a2 = syms2.lookup("gBuildRevision")[0]
            if a2:
                print("  [elf2 %s] gBuildRevision @0x%08x : %s" % (
                    os.path.basename(os.path.dirname(args.elf2)), a2, mem.cstr(a2, 80).strip()))
        except Exception as e:
            print("  elf2 读取失败: %s" % e)

    # ---- 2. panic 标志 + abort 类型 ----
    ispanic = mem.try_u32(S("gIsPanic") or 0)
    abort_u8 = mem.try_u8(S("gBlueScreenAbortType")) if S("gBlueScreenAbortType") else None
    print("\n--- 死机类型 ---")
    print("gIsPanic            : %s   # =1 蓝屏 panic" % ispanic)
    if abort_u8 is not None:
        at = abort_u8
        if at == 0xFE:
            kind = "ASSERT(0xFE, 软件断言)"
        elif at == 0xCA:
            kind = "0xCA (osiPanic udf 触发的蓝屏 abort 码)"
        else:
            kind = "0x%02x (异常码或 abort 类型)" % at
        print("gBlueScreenAbortType: 0x%02x -> %s" % (at, kind))

    # ---- 3. gBlueScreenRegs (ARM 现场) ----
    bsr_addr = S("gBlueScreenRegs")
    if bsr_addr:
        print("\n--- gBlueScreenRegs ARM 蓝屏现场 @0x%08x ---" % bsr_addr)
        bsr = syms.struct_offsets("gBlueScreenRegs") or {}
        # 找 r[] 起始、cpsr 偏移
        # struct 第一个成员通常是 r (数组), 偏移 0; cpsr 在某偏移
        cpsr_off = bsr.get("cpsr", 0x140)
        r_off = bsr.get("r", 0)
        regs = {}
        for i in range(16):
            regs["r%d" % i] = mem.try_u32(bsr_addr + r_off + i * 4)
        sp = regs.get("r13")
        lr = regs.get("r14")
        pc = regs.get("r15")
        cpsr = mem.try_u32(bsr_addr + cpsr_off)
        for i in range(16):
            key = "r%d" % i
            if i == 13: disp = "sp "
            elif i == 14: disp = "lr "
            elif i == 15: disp = "pc "
            else: disp = "%-3s" % key
            v = regs.get(key)
            tag = ""
            if v and syms.is_exec_code(v):
                tag = " <- CODE"
            print("  %-4s = 0x%08x%s" % (disp, v or 0, tag))
        print("  cpsr= 0x%08x  # %s" % (cpsr or 0, cpsr_decode(cpsr)))
        # gBlueScreenRegs 其他模式 sp/lr
        for fld in ["sp_usr","lr_usr","sp_svc","lr_svc","sp_abt","lr_abt",
                    "sp_und","lr_und","sp_irq","lr_irq","spsr_svc","spsr_abt"]:
            o = bsr.get(fld)
            if o is not None:
                v = mem.try_u32(bsr_addr + o)
                if v:
                    print("  %-8s (+0x%-3x) = 0x%08x" % (fld, o, v))

        # ---- 4. PC/LR 源码定位 ----
        print("\n--- PC/LR/SP 源码定位 ---")
        code_addrs = [v for v in [pc, lr, regs.get("r10"), regs.get("r4"), regs.get("r12")] if v]
        r = addr2line_batch(addr2line, args.ap_elf, code_addrs)
        for name, v in [("pc", pc), ("lr", lr), ("r10", regs.get("r10")),
                        ("r4", regs.get("r4")), ("r12", regs.get("r12"))]:
            if v:
                info = r.get(thumb_real(v), ("?", "?"))
                print("  %-4s 0x%08x -> %s  %s" % (name, v, info[1], info[0]))

        # ---- 5. osiPanic 机制确认 ----
        print("\n--- osiPanic 机制确认 ---")
        osiPanic_addr = S("osiPanic")
        if osiPanic_addr:
            print("osiPanic @0x%08x (osi_blue_screen.c):" % osiPanic_addr)
            dis = objdump_range(objdump, args.ap_elf, osiPanic_addr, osiPanic_addr + 0x30)
            for line in dis.splitlines():
                s = line.strip()
                if s.startswith(("0x", "0010", "100")) and ":" in s:
                    mark = "  <- udf 触发异常" if "udf" in s.lower() or "deff" in s else ""
                    print("  " + s + mark)
            if pc and thumb_real(pc) >= thumb_real(osiPanic_addr) and \
               thumb_real(pc) < thumb_real(osiPanic_addr) + 0x30:
                print("  >>> AP PC 在 osiPanic 内 -> 软件主动 panic, 根因在调用者(栈回溯)")
            else:
                print("  >>> AP PC 不在 osiPanic -> 可能硬件异常或别的 panic 路径")

        # ---- 6. 栈溢出检测 (vTaskSwitchContext + magic 0xa5a5a5a5) ----
        print("\n--- 栈溢出检测点 (vTaskSwitchContext) ---")
        vsw = S("vTaskSwitchContext")
        if vsw:
            dis = objdump_range(objdump, args.ap_elf, vsw, vsw + 0x600)
            hook_callsite = None
            magic_found = False
            magic_hex = "%08x" % FREERTOS_STACK_MAGIC
            for line in dis.splitlines():
                s = line.strip()
                if magic_hex in s.lower():
                    if not magic_found:
                        print("  [栈溢出检测] " + s)
                        magic_found = True
                if "vApplicationStackOverflowHook" in s or "StackOverflowHook" in s:
                    print("  [栈溢出钩子] " + s)
                    # 提取调用点地址
                    if ":" in s:
                        addr_part = s.split(":")[0].strip()
                        try: hook_callsite = int(addr_part, 16)
                        except: pass
            if not magic_found:
                print("  (未在 vTaskSwitchContext 范围内找到 %s 检测, 可能函数更大)" % magic_hex)
            # vApplicationStackOverflowHook 是否 tail-call osiPanic
            hook = S("vApplicationStackOverflowHook")
            if hook:
                dis2 = objdump_range(objdump, args.ap_elf, hook, hook + 0x40)
                for line in dis2.splitlines():
                    s = line.strip()
                    if s.startswith(("0x", "600e")) and ":" in s and ("osiPanic" in s or "b.w" in s or "bl" in s):
                        print("  [StackOverflowHook] " + s)
                        if "osiPanic" in s:
                            print("  >>> vApplicationStackOverflowHook -> osiPanic (tail-call 确认栈溢出 panic)")

        # ---- 7. 栈回溯: osiPanic 调用者 (sp+4 = push 的 lr) ----
        print("\n--- osiPanic 调用者 (栈回溯) ---")
        if sp:
            print("  gBlueScreenRegs.SP = 0x%08x (osiPanic push{r3,lr} 后)" % sp)
            print("  osiPanic push {r3,lr} -> 调用者 LR 在 sp+4:")
            for off in [0x4, 0x0, 0x8, 0xc, 0x24, 0x374, 0x378, 0x380, 0x384, 0x1e4]:
                v = mem.try_u32(sp + off)
                if v and syms.is_exec_code(v):
                    info = addr2line_batch(addr2line, args.ap_elf, [v]).get(thumb_real(v), ("?", "?"))
                    print("    sp+0x%-4x = 0x%08x -> %s  %s" % (off, v, info[1], info[0]))

            # ---- 7b. 双 ELF 代码差异版本判定 (--elf2 时, gBuildRevision 不可区分的降级方案) ----
            # 原理: 崩溃返回地址必须匹配"运行版本"代码布局 — caller-4 处 bl 的目标在崩溃路径上
            if args.elf2:
                caller = mem.try_u32(sp + 4)
                if caller:
                    print("\n--- 版本代码差异交叉验证 (osiPanic 调用者 0x%08x) ---" % caller)
                    print("  原理: 两版代码布局不同时, caller-4 处 bl 的目标不同; 运行版本目标在崩溃路径")
                    try:
                        tgts = {}
                        for tag, elf_path in [("主ELF", args.ap_elf), ("elf2", args.elf2)]:
                            info = addr2line_batch(addr2line, elf_path, [caller]).get(thumb_real(caller), ("?", "?"))
                            dis = objdump_range(objdump, elf_path, thumb_real(caller) - 4, thumb_real(caller) + 2)
                            lines = [l for l in dis.splitlines() if re.match(r'\s*[0-9a-f]+:', l)]
                            bl_tgt, bl_fn = None, "?"
                            if lines:
                                m = re.search(r'\t(bl|blx|b\.w|bx|b)\t([0-9a-f]+)', lines[0])
                                if m:
                                    bl_tgt = int(m.group(2), 16)
                                    bl_fn = addr2line_batch(addr2line, elf_path, [bl_tgt]).get(bl_tgt, ("?", "?"))[0]
                            tgts[tag] = (bl_tgt, bl_fn)
                            short = info[1].split('/')[-1] if '/' in info[1] else info[1]
                            print("  %s(%s): caller-4 bl%s -> %s  [caller@ %s %s]" % (
                                tag, os.path.basename(os.path.dirname(elf_path)),
                                " 0x%08x" % bl_tgt if bl_tgt else "(非bl)",
                                bl_fn[:40], short, info[0][:22]))
                        a_tgt, a_fn = tgts["主ELF"]
                        b_tgt, b_fn = tgts["elf2"]
                        if a_tgt and b_tgt and a_tgt != b_tgt:
                            print("  两版 bl 目标不同 (代码布局差异确认):")
                            kw = re.compile(r'(stackoverflow|stack_overflow|panic|assert|abort|hook|osiPanic)', re.I)
                            ap, bp = bool(kw.search(a_fn)), bool(kw.search(b_fn))
                            if ap and not bp:
                                print("  >>> 运行版本 = 主ELF (目标 %s 在崩溃路径)" % a_fn)
                            elif bp and not ap:
                                print("  >>> 运行版本 = elf2 (目标 %s 在崩溃路径)" % b_fn)
                            else:
                                print("  (目标均含/均不含崩溃关键词, 综合 caller addr2line 行号人工判定)")
                        elif a_tgt == b_tgt and a_tgt:
                            print("  两版 bl 目标相同, 该地址无法区分 (取栈上其他代码地址再验)")
                        else:
                            print("  (某版 caller-4 非 bl, call site 验证: 主ELF=%s elf2=%s)" % (
                                "bl" if a_tgt else "非bl", "bl" if b_tgt else "非bl"))
                    except Exception as e:
                        print("  版本验证异常: %s" % e)
            # 扫栈上所有代码地址 (前 STACK_SCAN_WINDOW)
            print("  --- 栈上代码地址 (扫 sp~sp+0x%x) ---" % STACK_SCAN_WINDOW)
            seen = set()
            code_list = []
            for i in range(0, STACK_SCAN_WINDOW, 4):
                v = mem.try_u32(sp + i)
                if v and syms.is_exec_code(v) and v not in seen:
                    seen.add(v)
                    code_list.append((i, v))
            r2 = addr2line_batch(addr2line, args.ap_elf, [v for _, v in code_list])
            for off, v in code_list[:25]:
                info = r2.get(thumb_real(v), ("?", "?"))
                print("    sp+0x%-4x 0x%08x -> %s  %s" % (off, v, info[1], info[0]))

    # ---- 8. 当前任务 (pxCurrentTCB) ----
    print("\n--- 当前任务 (pxCurrentTCB) ---")
    pxc = S("pxCurrentTCB")
    if pxc:
        tcb = mem.try_u32(pxc)
        print("  pxCurrentTCB @0x%08x -> 0x%08x" % (pxc, tcb or 0))
        if tcb:
            # TCB 偏移优先 DWARF, 与 threads.py 同源; 不再硬编码 0x34/0x0/0x30
            t_offs = tcb_offsets(syms)
            name = mem.cstr(tcb + t_offs["pcTaskName"], 16).strip("\x00")
            top = mem.try_u32(tcb + t_offs["pxTopOfStack"])
            pstack = mem.try_u32(tcb + t_offs["pxStack"])
            print("  任务名 (TCB+0x%x) = %r" % (t_offs["pcTaskName"], name))
            print("  pxTopOfStack (+0x%x) = 0x%08x" % (t_offs["pxTopOfStack"], top or 0))
            print("  pxStack (+0x%x)    = 0x%08x  (栈底)" % (t_offs["pxStack"], pstack or 0))
            if top and pstack:
                if top < pstack:
                    print("  >>> pxTopOfStack < pxStack -> 栈顶越过栈底边界 = 该任务【自己】栈溢出!")
                    print("      溢出深度约 %d 字节 (pxStack - pxTopOfStack)" % (pstack - top))
                else:
                    print("  栈顶在栈底之上, 栈方向正常 (溢出需看栈底 magic)")
                # 栈底 magic 检查
                print("  栈底 magic 检查 (pxStack 处应为 0x%08x):" % FREERTOS_STACK_MAGIC)
                for i in range(0, 0x20, 4):
                    v = mem.try_u32((pstack or 0) + i)
                    mk = " (magic OK)" if v == FREERTOS_STACK_MAGIC else (" <- 被破坏!" if v else "")
                    print("    [pxStack+0x%x] = 0x%08x%s" % (i, v or 0, mk))

    # ---- 9. FOTA / 系统状态 ----
    print("\n--- FOTA / 系统状态 ---")
    for n in ["gfupdateStat", "g_CpMdVersion", "bWakeupFromDeep",
              "gIsOpenCPUPmMode", "gSysWdtDisable"]:
        a = S(n)
        if a:
            v = mem.try_u32(a)
            print("  %-22s @0x%08x = 0x%x" % (n, a, v or 0))

    # ---- 10. 运行时长 (xTickCount) ----
    print("\n--- 运行时长 ---")
    a = S("xTickCount")
    if a:
        tick = mem.try_u32(a)
        print("  xTickCount @0x%08x = %d (0x%x)" % (a, tick or 0, tick or 0))
        # FreeRTOS tick 通常 1ms; 区分上电即死 vs 运行一段时间死
        if tick:
            secs = tick // 1000
            if secs < 5:
                print("  >>> 设备仅运行约 %d 秒 (上电/启动阶段即死, 排查初始化)" % secs)
            elif secs < 60:
                print("  >>> 设备运行约 %d 秒" % secs)
            else:
                print("  >>> 设备运行较久 (约 %d 秒, %d 分钟) — 运行态死机" % (secs, secs // 60))
            print("  (注: tick 32位会溢出, 49天后回绕; 压测场景可能多次重启)")

    print("\n" + "=" * 92)
    print(" 下一步: 若 PC=osiPanic 且调用者=vTaskSwitchContext/StackOverflowHook -> 栈溢出,")
    print("        跑 threads.py 枚举所有任务栈水位; 若有 CP Assert 文本 -> 跑 cp_assert.py")
    print("=" * 92)


if __name__ == "__main__":
    main()
