#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run every UIS8852 dump analyzer script, archive outputs to
<out_dir>/analysis/ (numbered, in analysis-flow order), and write:

  - <out_dir>/analysis/_meta.json   — run context (dump path, ELF, firmware,
                                      crash PC, time, scripts) so results stay
                                      traceable to the raw dump
  - <out_dir>/analysis/INDEX.md     — human index: each script's purpose +
                                      auto-extracted key conclusions + how to
                                      verify against the raw .bin
  - <dump_dir>/_analysis_pointer.txt — lightweight reverse link from the raw
                                      dump dir back to this analysis (so you can
                                      find the conclusions while staring at data)

Results live in the bug dir (.spec/bug/<id>/analysis/) for persistence; the
two pointers + _meta.json bridge bug dir <-> raw dump for cross-checking.

Usage:
  python run_all.py <dump_dir> <ap.elf> <out_dir> [--pc 0xc026cb94]
"""
import os, sys, subprocess, argparse, datetime, json, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (name, purpose, script, key-line regexes used to auto-extract conclusions)
SCRIPTS = [
    ("uis8852_analyze", "起点：蓝屏/异常现场、寄存器、当前中断 IRQ、启发式回溯、源码定位",
     "uis8852_analyze.py",
     [r"g_osErrorLog", r"AbortType", r"g_osIrqNo", r"mcause", r"源码定位"]),
    ("unwind", "帧感知栈回溯 + 当前中断身份权威确认（g_osIrqNo）+ prologue 表",
     "unwind.py",
     [r"内部IRQ", r"中断上下文 ISR|任务上下文", r"栈扫描候选", r"prologue 表"]),
    ("threads", "任务列表 + 每任务栈水位（溢出/死锁/调度异常）",
     "threads.py",
     [r"枚举到", r"栈溢出风险", r"状态汇总", r"被中断的当前任务"]),
    ("trace_history", "任务/中断切换历史（崩溃前调度序列 + IRQ 风暴 — WDT/死锁关键）",
     "trace_history.py",
     [r"切换频率", r"中断频率", r"风暴", r"<- 当前任务", r"WRAPPED|环形已满"]),
    ("heap_state", "AP 堆状态（使用率）+ malloc trace ring 堆消耗户 + SLOG ISR 日志",
     "heap_state.py",
     [r"堆使用率", r"记录数", r"分配调用者排行", r"限流 THROTTLED", r"g_slogIsrLogTotalLen"]),
    ("heap_walker", "堆物理遍历：判定 耗尽 / 元数据损坏 / 越界写（av_ bin-header 完整性）",
     "heap_walker.py",
     [r"结论 VERDICT", r"遍历.*chunk", r"非法地址", r"bin-header|合法 ok"]),
    ("wdt_reset", "复位原因（gResetReson 位掩码）+ WDT 状态/寄存器 + 蓝屏 vs 真复位区分",
     "wdt_reset.py",
     [r"gResetReson", r"结论 VERDICT", r"复位原因判定", r"看门狗状态"]),
    ("code_compare", "代码完整性（ELF .itcm/.iram2/.psram/.xip_text 段 vs dump）— EXCEPTION 必查",
     "code_compare.py",
     [r"结论：", r"INTACT", r"损坏 CORRUPTED", r"未加载 NOT LOADED"]),
    ("unwind_cfi", "DWARF .debug_frame CFI 确定性回溯（比启发式干净；崩溃链 + 全任务逐线程链）",
     "unwind_cfi.py",
     [r"已索引.*FDE", r"归属线程", r"to_thread", r"栈溢出风险", r"各挂起线程"]),
    ("assert_reason", "断言模式推理（scheduler 栈检查 → 精准判定溢出线程+根因链）",
     "assert_reason.py",
     [r"断言模式", r"栈溢出的线程", r"哨兵字节", r"结论（VERDICT）", r"根因在"]),
    ("double_free_detect", "double-free 自动判定（dlmalloc.c:2066 downflow）：取被 free 指针，查块状态（下一块 PREV_INUSE + 空闲链表回指），区分 double-free vs 越界写；非 2066 场景自动跳过",
     "double_free_detect.py",
     [r"VERDICT", r"DOUBLE-FREE", r"HEAP OVERFLOW", r"PREV_INUSE\(bit0\)", r"受害块 .*FREE|受害块 .*INUSE"]),
]


def firmware_version(dump_dir):
    """Read gBuildRevision from dtools.log (best-effort)."""
    p = os.path.join(dump_dir, "dtools.log")
    try:
        for line in open(p, encoding="utf-8", errors="replace"):
            m = re.search(r"gBuildRevision in _elf_\s*:\s*(\S+)", line)
            if m:
                return m.group(1)
    except Exception:
        pass
    return "(unknown)"


def run_one(dump, elf, script, analysis_dir, seq, name, extra_args):
    out_file = os.path.join(analysis_dir, "%02d_%s.txt" % (seq, name))
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__), script), dump, elf] + extra_args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=240,
                           encoding="utf-8", errors="replace")
        out = r.stdout
        if r.returncode != 0:
            out += "\n\n[ERROR rc=%d]\n%s\n" % (r.returncode, r.stderr[-2000:])
    except Exception as e:
        out = "[FAILED to run: %s]\n" % e
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(out)
    return out, "%02d_%s.txt" % (seq, name)


def extract_conclusions(text, regexes):
    import re
    hits = []
    for rx in regexes:
        for line in text.splitlines():
            if re.search(rx, line) and line.strip() and len(hits) < 12:
                hits.append(line.strip())
    seen = set(); out = []
    for h in hits:
        if h not in seen:
            seen.add(h); out.append(h)
    return out[:8]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("elf")
    ap.add_argument("out_dir", help="analysis root (e.g. .spec/bug/<id>_desc/)")
    ap.add_argument("--pc", default=None, help="crash PC, passed to code_compare")
    args = ap.parse_args()

    dump_abs = os.path.abspath(args.dump_dir)
    elf_abs = os.path.abspath(args.elf)
    out_abs = os.path.abspath(args.out_dir)
    analysis_dir = os.path.join(out_abs, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    fw = firmware_version(dump_abs)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("UIS8852 dump analysis run — %s" % stamp)
    print("dump   : %s" % dump_abs)
    print("elf    : %s" % elf_abs)
    print("fw     : %s" % fw)
    print("output : %s\n" % analysis_dir)

    results = []   # (seq, name, purpose, script, out_file, conclusions)
    extra = ["--pc", args.pc] if args.pc else []
    for seq, (name, purpose, script, regexes) in enumerate(SCRIPTS, 1):
        print("  running [%02d] %-18s ... " % (seq, script), end="", flush=True)
        out, out_file = run_one(dump_abs, elf_abs, script, analysis_dir, seq, name,
                                extra if script == "code_compare.py" else [])
        concl = extract_conclusions(out, regexes)
        results.append((seq, name, purpose, script, out_file, concl))
        print("ok (%d lines, %d key lines)" % (len(out.splitlines()), len(concl)))

    # ---- _meta.json (traceability: results -> raw dump) ----
    meta = {
        "run_time": stamp,
        "dump_dir": dump_abs,
        "elf": elf_abs,
        "firmware_version": fw,
        "crash_pc": args.pc or "(not specified)",
        "bug_out_dir": out_abs,
        "scripts": [
            {"seq": seq, "name": name, "purpose": purpose,
             "script": script, "output": "analysis/%s" % out_file}
            for seq, name, purpose, script, out_file, _ in results
        ],
        "note": "Results archived in bug dir for persistence. Use dump_dir above "
                "to cross-check against raw .bin; see INDEX.md 'how to verify'.",
    }
    with open(os.path.join(analysis_dir, "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---- INDEX.md ----
    idx = os.path.join(analysis_dir, "INDEX.md")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("# UIS8852 Dump 脚本输出索引\n\n")
        f.write("## 📌 核对原始数据\n\n")
        f.write("- **dump 目录**（核对 `.bin`/`.elf` 原始数据）: `%s`\n" % dump_abs)
        f.write("- **ELF**: `%s`\n" % elf_abs)
        f.write("- **固件版本**: `%s`\n" % fw)
        f.write("- **crash PC**: `%s`\n" % (args.pc or "(未指定)"))
        f.write("- **运行时间**: %s\n" % stamp)
        f.write("- **完整上下文**: `analysis/_meta.json`\n\n")
        f.write("> **如何核对**：每个结论可对照 dump 目录的 `.bin`（文件名=基址）。"
                "例：堆 base=0x8019ace0 → 看 `80000000.bin` @ offset `0x1face0`；"
                "`g_osIrqNo` @0x10818 → 看 `00010000.bin` @ offset `0x818`。\n\n")
        f.write("---\n\n## 各脚本输出（按分析流程顺序）\n\n")
        f.write("> 完整输出见同目录 `NN_<脚本名>.txt`。下方为**功能**与自动提取的**关键结论**，"
                "便于跨次分析对比/追溯。\n\n")
        for seq, name, purpose, script, out_file, concl in results:
            f.write("### %02d. %s — %s\n\n" % (seq, name, purpose))
            f.write("- **脚本**: `%s` ｜ **完整输出**: `analysis/%s`\n\n" % (script, out_file))
            if concl:
                f.write("**关键结论（自动提取）**:\n\n```\n")
                f.write("\n".join(concl)); f.write("\n```\n\n")
            else:
                f.write("**关键结论**: (无自动匹配——请人工查看输出)\n\n")
        f.write("\n---\n\n> 重新分析新 dump 时，对同名 `NN_*.txt` 做 `diff` 可快速发现差异：\n")
        f.write("> `diff <bug1>/analysis/06_heap_walker.txt <bug2>/analysis/06_heap_walker.txt`\n")

    # ---- reverse pointer in the raw dump dir (data side -> conclusions) ----
    try:
        ptr = os.path.join(dump_abs, "_analysis_pointer.txt")
        with open(ptr, "w", encoding="utf-8") as f:
            f.write("UIS8852 dump analysis — pointer to archived results\n\n")
            f.write("Bug dir  : %s\n" % out_abs)
            f.write("INDEX    : %s\n" % os.path.join(out_abs, "analysis", "INDEX.md"))
            f.write("Run time : %s\n" % stamp)
            f.write("Firmware : %s\n" % fw)
            f.write("Crash PC : %s\n\n" % (args.pc or "(not set)"))
            f.write("(Results are kept in the bug dir for persistence. "
                    "This file is a navigation aid so you can find them from the dump side.)\n")
    except Exception as e:
        print("  (could not write dump-side pointer: %s)" % e)

    print("\nINDEX : %s" % idx)
    print("meta  : %s" % os.path.join(analysis_dir, "_meta.json"))
    print("ptr   : %s" % os.path.join(dump_abs, "_analysis_pointer.txt"))
    print("done. %d scripts archived to %s" % (len(results), analysis_dir))


if __name__ == "__main__":
    main()
