#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run ASR1603 dump analysis scripts, archive outputs to <out_dir>/analysis/ and write:

  - <out_dir>/analysis/_meta.json   — run context (dump dir, map, axf, PC/LR, time, scripts)
  - <out_dir>/analysis/INDEX.md     — human index: each script's purpose +
                                      auto-extracted key conclusions + how to verify

Orchestrates: full-analyze → ddr_code_compare → stack_analysis → axf_disasm.
full-analyze covers most analysis but does NOT call the latter 3; run_all bridges
by extracting PC/LR/DDR_BASE from full-analyze output and feeding downstream.

Usage:
  python run_all.py <dump_dir> <out_dir> [--map <map>] [--axf <axf>]
  # 例: python run_all.py .spec/.../dump/ .spec/bug/<id>_desc/ --map firmware.map --axf app.axf
"""
import os
import sys
import glob
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


def find_ddr_file(dump_dir):
    """在 dump_dir 下查找 DDR dump 文件（用于 ddr_code_compare）"""
    candidates = []
    for pat in ("*.bin", "*.ddr", "*ddr*", "*DDR*", "*psram*", "*PSRAM*"):
        candidates.extend(glob.glob(os.path.join(dump_dir, pat)))
    # 排除 hbuf/wdt/rti 等非 DDR 文件，优先大文件
    ddr_files = [f for f in candidates if not re.search(r"(?i)(hbuf|wdt|rti|\.elf|\.axf|\.map)", f)]
    if ddr_files:
        return max(ddr_files, key=os.path.getsize)
    return None


def extract_from_output(text):
    """从 full-analyze 输出提取 PC/LR/SP/DDR_BASE（跨脚本数据传递）"""
    result = {"pc": None, "lr": None, "sp": None, "ddr_base": None}
    # PC 0x%08X -> ...（取第一个）
    m = re.search(r"\bPC\s+0x([0-9a-fA-F]+)\s*->", text)
    if m:
        result["pc"] = "0x" + m.group(1).lower()
    m = re.search(r"\bLR\s+0x([0-9a-fA-F]+)\s*->", text)
    if m:
        result["lr"] = "0x" + m.group(1).lower()
    m = re.search(r"\bSP:\s*0x([0-9a-fA-F]+)", text)
    if m:
        result["sp"] = "0x" + m.group(1).lower()
    m = re.search(r"VERIFIED DDR base address:\s*0x([0-9a-fA-F]+)", text)
    if m:
        result["ddr_base"] = "0x" + m.group(1).lower()
    return result


def run_subprocess(cmd, timeout=300):
    """运行命令，返回 (stdout, returncode)"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           encoding="utf-8", errors="replace")
        out = r.stdout
        if r.returncode != 0:
            out += "\n\n[ERROR rc=%d]\n%s\n" % (r.returncode, r.stderr[-2000:])
        return out, r.returncode
    except Exception as e:
        return "[FAILED to run: %s]\n" % e, -1


def archive(analysis_dir, seq, name, content):
    """归档脚本输出到 NN_name.txt，返回文件名"""
    out_file = "%02d_%s.txt" % (seq, name)
    with open(os.path.join(analysis_dir, out_file), "w", encoding="utf-8") as f:
        f.write(content)
    return out_file


def extract_conclusions(text):
    """提取关键结论行（>> 前缀 + CONCLUSION/Result 段）"""
    hits = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.match(r"^>>\s+(VERDICT|ROOT CAUSE|STACK OVERFLOW)", s):
            hits.append(s)
        elif re.match(r"^Result:\s+(OVERFLOW|within)", s, re.I):
            hits.append(s)
        elif "CONCLUSION" in s and len(hits) < 10:
            hits.append(s)
    # 去重保序
    seen = set()
    out = []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out[:10]


def main():
    ap = argparse.ArgumentParser(description="ASR1603 dump 一键分析 + 归档")
    ap.add_argument("dump_dir", help="dump 文件目录")
    ap.add_argument("out_dir", help="分析输出根目录（bug 目录，如 .spec/bug/<id>_<desc>/）")
    ap.add_argument("--map", help="MAP file")
    ap.add_argument("--axf", help="AXF/ELF file（ddr_code_compare/stack_analysis/disasm 需要）")
    args = ap.parse_args()

    dump_abs = os.path.abspath(args.dump_dir)
    out_abs = os.path.abspath(args.out_dir)
    analysis_dir = os.path.join(out_abs, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    py = sys.executable

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("ASR1603 dump analysis run — %s" % stamp)
    print("dump_dir: %s" % dump_abs)
    print("map     : %s" % (args.map or "(none)"))
    print("axf     : %s" % (args.axf or "(none)"))
    print("output  : %s\n" % analysis_dir)

    results = []  # (seq, name, purpose, out_file, conclusions)

    # ---- 1. full-analyze（主报告 + 初步根因）----
    print("  running [01] full-analyze ... ", end="", flush=True)
    cmd = [py, os.path.join(script_dir, "dump_analyzer.py"), "full-analyze",
           "--dump-dir", dump_abs]
    if args.map:
        cmd += ["--map", os.path.abspath(args.map)]
    out, _ = run_subprocess(cmd, timeout=300)
    of = archive(analysis_dir, 1, "full_analyze", out)
    extracted = extract_from_output(out)
    concl = extract_conclusions(out)
    results.append((1, "full_analyze", "起点：版本校验 + 异常头解析 + PC/LR + DDR栈分析 + 线程扫描 + 堆扫描 + 代码完整性 + 初步根因", of, concl))
    print("ok (%d lines)" % len(out.splitlines()))
    print("    extracted: PC=%s LR=%s SP=%s DDR_BASE=%s" % (
        extracted["pc"], extracted["lr"], extracted["sp"], extracted["ddr_base"]))

    # ---- 2. ddr_code_compare（代码完整性深度对比，full-analyze 未覆盖）----
    if args.axf and extracted["pc"]:
        ddr_file = find_ddr_file(dump_abs)
        if ddr_file:
            print("  running [02] ddr_code_compare ... ", end="", flush=True)
            cmd = [py, os.path.join(script_dir, "ddr_code_compare.py"),
                   os.path.abspath(args.axf), ddr_file,
                   "--pc", extracted["pc"], "--size", "64"]
            if extracted["ddr_base"]:
                cmd += ["--base", extracted["ddr_base"]]
            out, _ = run_subprocess(cmd, timeout=120)
            of = archive(analysis_dir, 2, "code_compare", out)
            concl = extract_conclusions(out)
            results.append((2, "code_compare", "AXF vs DDR 代码完整性对比（段归属 + 字节级 diff + PSRAM损坏/DataAbort判定）", of, concl))
            print("ok (%d lines)" % len(out.splitlines()))
        else:
            print("  skipping [02] ddr_code_compare ... (no DDR dump file found)")
            results.append((2, "code_compare", "AXF vs DDR 代码完整性对比", "(skipped: no DDR file)", []))
    else:
        print("  skipping [02] ddr_code_compare ... (need --axf and PC from step 1)")
        results.append((2, "code_compare", "AXF vs DDR 代码完整性对比", "(skipped: no --axf)", []))

    # ---- 3. stack_analysis（静态峰值栈深度，full-analyze 未覆盖）----
    if args.axf and args.map and extracted["pc"]:
        print("  running [03] stack_analysis ... ", end="", flush=True)
        cmd = [py, os.path.join(script_dir, "stack_analysis.py"),
               os.path.abspath(args.axf), os.path.abspath(args.map),
               "--addr", extracted["pc"], "--size", "64"]
        out, _ = run_subprocess(cmd, timeout=120)
        of = archive(analysis_dir, 3, "stack_depth", out)
        concl = extract_conclusions(out)
        results.append((3, "stack_depth", "静态峰值栈深度分析（调用链最深处的栈消耗，判溢出）", of, concl))
        print("ok (%d lines)" % len(out.splitlines()))
    else:
        print("  skipping [03] stack_analysis ... (need --axf --map and PC)")
        results.append((3, "stack_depth", "静态峰值栈深度分析", "(skipped: missing args)", []))

    # ---- 4. axf_disasm（反汇编上下文，full-analyze 未覆盖）----
    if args.axf and extracted["pc"]:
        print("  running [04] axf_disasm ... ", end="", flush=True)
        cmd = [py, os.path.join(script_dir, "axf_disasm.py"),
               os.path.abspath(args.axf),
               "--address", extracted["pc"], "--size", "32", "--context", "8"]
        if args.map:
            cmd += ["--map", os.path.abspath(args.map)]
        out, _ = run_subprocess(cmd, timeout=120)
        of = archive(analysis_dir, 4, "disasm", out)
        results.append((4, "disasm", "PC 处反汇编上下文（指令级，配合源码行号理解崩溃点）", of, []))
        print("ok (%d lines)" % len(out.splitlines()))
    else:
        print("  skipping [04] axf_disasm ... (need --axf and PC)")
        results.append((4, "disasm", "PC 处反汇编上下文", "(skipped: missing args)", []))

    # ---- _meta.json ----
    meta = {
        "run_time": stamp,
        "dump_dir": dump_abs,
        "map": os.path.abspath(args.map) if args.map else None,
        "axf": os.path.abspath(args.axf) if args.axf else None,
        "extracted": extracted,
        "bug_out_dir": out_abs,
        "scripts": [
            {"seq": seq, "name": name, "purpose": purpose, "output": "analysis/%s" % of}
            for seq, name, purpose, of, _ in results
        ],
    }
    with open(os.path.join(analysis_dir, "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---- INDEX.md ----
    idx = os.path.join(analysis_dir, "INDEX.md")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("# ASR1603 Dump 脚本输出索引\n\n")
        f.write("## 📌 核对原始数据\n\n")
        f.write("- **dump 目录**: `%s`\n" % dump_abs)
        f.write("- **MAP**: `%s`\n" % (os.path.abspath(args.map) if args.map else "(未提供)"))
        f.write("- **AXF**: `%s`\n" % (os.path.abspath(args.axf) if args.axf else "(未提供)"))
        f.write("- **提取的崩溃寄存器**: PC=%s LR=%s SP=%s DDR_BASE=%s\n" % (
            extracted["pc"], extracted["lr"], extracted["sp"], extracted["ddr_base"]))
        f.write("- **运行时间**: %s\n" % stamp)
        f.write("- **完整上下文**: `analysis/_meta.json`\n\n")
        f.write("---\n\n## 各脚本输出（按分析流程顺序）\n\n")
        f.write("> 完整输出见同目录 `NN_<脚本名>.txt`。下方为**功能**与自动提取的**关键结论**。\n\n")
        for seq, name, purpose, of, concl in results:
            f.write("### %02d. %s — %s\n\n" % (seq, name, purpose))
            f.write("- **完整输出**: `analysis/%s`\n\n" % of)
            if concl:
                f.write("**关键结论（自动提取）**:\n\n```\n")
                f.write("\n".join(concl))
                f.write("\n```\n\n")
            else:
                f.write("**关键结论**: (无自动匹配——请人工查看输出)\n\n")
        f.write("\n---\n\n> 重新分析新 dump 时，对同名 `NN_*.txt` 做 `diff` 可快速发现差异。\n")

    print("\nINDEX : %s" % idx)
    print("meta  : %s" % os.path.join(analysis_dir, "_meta.json"))
    print("done. %d scripts archived to %s" % (len(results), analysis_dir))


if __name__ == "__main__":
    main()
