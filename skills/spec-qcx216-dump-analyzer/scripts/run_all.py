#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QCX216 dump 分析运行器：依次跑各分析命令，归档输出到 <out_dir>/analysis/，并生成：

  - <out_dir>/analysis/NN_<名>.txt   — 各命令完整输出（按分析流程编号）
  - <out_dir>/analysis/_meta.json    — 运行上下文（dump/ELF/固件/触发点/时间/脚本），可追溯
  - <out_dir>/analysis/INDEX.md      — 人类索引：每步用途 + 自动提取的关键结论 + 如何对照原始 dump 核对
  - <dump目录>/_analysis_pointer.txt — 从原始 dump 反向链接到归档结果

参考 spec-uis8852-dump-analyzer/run_all.py 设计。结果存 bug 目录留痕，两个指针 + _meta.json
打通 bug 目录 <-> 原始 dump，便于核对。

Usage:
  python run_all.py <RamDumpData_*.bin> <ap_at_command.elf> <out_dir>
  python run_all.py <dump.bin> <elf> .spec/bug/7031160371_xxx/
"""
import os
import sys
import subprocess
import argparse
import datetime
import json
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

# (name, purpose, subcmd, args_template, conclusion_regexes)
SCRIPTS = [
    ("full_analyze", "一体化：异常解析+触发点反汇编+调用链+中断源+heap+OSA池+全任务",
     "full-analyze", ["{dump}", "--elf", "{elf}"],
     [r">> Exception Type", r"Func\s*:", r"Trigger\s*:", r"Failure Point.*->",
      r"MPDMA_interruptHandler|IpcC2AMsg2Errc|ACIpcAlone1Isr|XIC_IntHandler",
      r"判定[::].*堆", r"\*\*\* 满", r"OVERFLOW|WARNING|HIGH RISK"]),
    ("osa_pool", "OSA 信号池状态（poolId 推理 + freeHead 满判定）",
     "scan-osa-pool", ["{dump}", "--elf", "{elf}"],
     [r"poolId\s*=", r"\*\*\* 满", r"freeHead", r"blksize", r"used\(cnt\)"]),
    ("stacks", "全任务栈溢出扫描",
     "scan-stacks", ["{dump}", "--elf", "{elf}"],
     [r"OVERFLOW", r"WARNING", r"NO SENTINEL", r"used%"]),
]


def firmware_version(dump_path):
    """从 dump 目录(含子目录)的 comdb.txt BuildInfo 提取固件版本。"""
    d = os.path.dirname(dump_path) or "."
    for root, _dirs, files in os.walk(d):
        if "comdb.txt" in files:
            p = os.path.join(root, "comdb.txt")
            try:
                for line in open(p, encoding="utf-8", errors="replace"):
                    m = re.search(r"Application Ver\.\s*([\d.]+)", line)
                    if m:
                        return m.group(1)
            except Exception:
                pass
    return "(unknown)"


def run_subcmd(dump, elf, subcmd, args_tmpl, analysis_dir, seq, name):
    out_file = os.path.join(analysis_dir, "%02d_%s.txt" % (seq, name))
    args = [a.format(dump=dump, elf=elf) for a in args_tmpl]
    cmd = [sys.executable, os.path.join(os.path.dirname(__file__),
            "qcx216_dump_analyzer.py"), subcmd] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace")
        out = r.stdout
        if r.returncode != 0:
            out += "\n\n[ERROR rc=%d]\n%s\n" % (r.returncode, r.stderr[-2000:])
    except Exception as e:
        out = "[FAILED to run: %s]\n" % e
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(out)
    return out, "%02d_%s.txt" % (seq, name)


def extract_trigger(full_out):
    """从 full-analyze 输出提取触发点地址。"""
    m = re.search(r"Trigger\s*:?\s*0x([0-9A-Fa-f]+)", full_out)
    if not m:
        m = re.search(r"^>>\s*0x([0-9A-Fa-f]{6,})", full_out, re.M)
    return ("0x" + m.group(1)) if m else None


def extract_conclusions(text, regexes):
    hits = []
    for rx in regexes:
        for line in text.splitlines():
            if re.search(rx, line) and line.strip() and len(hits) < 14:
                hits.append(line.strip())
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:9]


def main():
    ap = argparse.ArgumentParser(description="QCX216 dump 分析运行器：归档输出+索引+可追溯")
    ap.add_argument("dump", help="RamDumpData_*.bin 路径")
    ap.add_argument("elf", help="崩溃固件 ap_at_command.elf 路径")
    ap.add_argument("out_dir", help="输出根目录(如 .spec/bug/<id>_desc/)")
    args = ap.parse_args()

    dump_abs = os.path.abspath(args.dump)
    elf_abs = os.path.abspath(args.elf)
    out_abs = os.path.abspath(args.out_dir)
    analysis_dir = os.path.join(out_abs, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    fw = firmware_version(dump_abs)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("QCX216 dump analysis run — %s" % stamp)
    print("dump   : %s" % dump_abs)
    print("elf    : %s" % elf_abs)
    print("fw     : %s" % fw)
    print("output : %s\n" % analysis_dir)

    results = []
    trigger = None
    for seq, (name, purpose, subcmd, tmpl, regexes) in enumerate(SCRIPTS, 1):
        print("  running [%02d] %-14s ... " % (seq, subcmd), end="", flush=True)
        out, out_file = run_subcmd(dump_abs, elf_abs, subcmd, tmpl, analysis_dir, seq, name)
        if name == "full_analyze":
            trigger = extract_trigger(out)
        concl = extract_conclusions(out, regexes)
        results.append((seq, name, purpose, subcmd, out_file, concl))
        print("ok (%d lines, %d key)" % (len(out.splitlines()), len(concl)))

    # 触发点反汇编（动态：需从 full-analyze 提取的 trigger 地址）
    if trigger:
        seq = len(results) + 1
        print("  running [%02d] disasm %s ... " % (seq, trigger), end="", flush=True)
        out, out_file = run_subcmd(dump_abs, elf_abs, "disasm",
                                   [trigger, "--dump", "{dump}", "--elf", "{elf}",
                                    "--before", "6", "--after", "8"],
                                   analysis_dir, seq, "disasm_trigger")
        concl = extract_conclusions(out, [r">>.*B\s+0x", r"BL.*->", r"PUSH|POP"])
        results.append((seq, "disasm_trigger", "触发点反汇编（assert 现场，capstone 连续反汇编）",
                        "disasm", out_file, concl))
        print("ok")

    # ---- _meta.json (traceability) ----
    meta = {
        "run_time": stamp, "dump": dump_abs, "elf": elf_abs,
        "firmware_version": fw, "trigger": trigger or "(not found)",
        "bug_out_dir": out_abs,
        "scripts": [{"seq": s, "name": n, "purpose": p, "subcmd": c,
                     "output": "analysis/%s" % of} for s, n, p, c, of, _ in results],
        "note": "QCX216 dump base=0x0，偏移==物理地址。核对：结论里的地址直接是 dump 文件偏移。",
    }
    with open(os.path.join(analysis_dir, "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---- INDEX.md ----
    idx = os.path.join(analysis_dir, "INDEX.md")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("# QCX216 Dump 脚本输出索引\n\n## 📌 核对原始数据\n\n")
        f.write("- **dump**: `%s`\n- **ELF**: `%s`\n- **固件版本**: %s\n"
                % (dump_abs, elf_abs, fw))
        f.write("- **触发点**: %s\n- **运行时间**: %s\n- **完整上下文**: `analysis/_meta.json`\n\n"
                % (trigger or "(未找到)", stamp))
        f.write("> **如何核对**：QCX216 dump `base=0x0`，**偏移 == 物理地址**。"
                "结论里的地址直接是 dump 文件偏移。例：触发点 0x0041BB04 → dump offset 0x41BB04；"
                "excepInfoStore @0x4232F4 → offset 0x4232F4；OSA pool[1] base 0x459C90 → offset 0x459C90；"
                "pxCurrentTCB @0x427EAC → offset 0x427EAC。\n\n")
        f.write("---\n\n## 各脚本输出（按分析流程）\n\n")
        f.write("> 完整输出见同目录 `NN_<名>.txt`。下方为**功能**与自动提取的**关键结论**，"
                "便于跨次分析对比/追溯。\n\n")
        for s, n, p, c, of, concl in results:
            f.write("### %02d. %s — %s\n\n" % (s, n, p))
            f.write("- **命令**: `%s` ｜ **完整输出**: `analysis/%s`\n\n" % (c, of))
            if concl:
                f.write("**关键结论（自动提取）**:\n\n```\n%s\n```\n\n" % "\n".join(concl))
            else:
                f.write("**关键结论**: (无自动匹配——请人工查看完整输出)\n\n")
        f.write("\n---\n\n> 重新分析新 dump 时，对同名 `NN_*.txt` 做 `diff` 可快速发现差异：\n")
        f.write("> `diff <bug1>/analysis/01_full_analyze.txt <bug2>/analysis/01_full_analyze.txt`\n")

    # ---- dump 目录反向指针 (data side -> conclusions) ----
    try:
        ptr = os.path.join(os.path.dirname(dump_abs) or ".", "_analysis_pointer.txt")
        with open(ptr, "w", encoding="utf-8") as f:
            f.write("QCX216 dump analysis — pointer to archived results\n\n")
            f.write("Bug dir  : %s\n" % out_abs)
            f.write("INDEX    : %s\n" % os.path.join(out_abs, "analysis", "INDEX.md"))
            f.write("Run time : %s\n" % stamp)
            f.write("Firmware : %s\n" % fw)
            f.write("Trigger  : %s\n\n" % (trigger or "(n/a)"))
            f.write("(Results kept in bug dir for persistence. This file is a navigation aid "
                    "so you can find them from the dump side.)\n")
    except Exception as e:
        print("  (could not write dump-side pointer: %s)" % e)

    print("\nINDEX : %s" % idx)
    print("meta  : %s" % os.path.join(analysis_dir, "_meta.json"))
    print("ptr   : %s" % os.path.join(os.path.dirname(dump_abs) or ".", "_analysis_pointer.txt"))
    print("done. %d outputs archived to %s" % (len(results), analysis_dir))


if __name__ == "__main__":
    main()
