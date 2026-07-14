#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Run EC626 dump analysis scripts, archive outputs to <out_dir>/analysis/ and write:

  - <out_dir>/analysis/_meta.json   — run context (dump path, map, elf, time, scripts)
  - <out_dir>/analysis/INDEX.md     — human index: each script's purpose +
                                      auto-extracted key conclusions + how to verify

Results live in the bug dir (.spec/bug/<id>/analysis/) for persistence; _meta.json
bridges bug dir <-> raw dump for cross-checking.

Usage:
  python run_all.py <dump_bin> <out_dir> [--map <map>] [--elf <elf>] [--tcb-addr 0x...]
  # 例: python run_all.py RamDumpData_*.bin .spec/bug/6974423486_hardfault/ --map firmware.map
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

# (seq, name, purpose, subcommand, extra_args_builder, key-line regexes)
# extra_args_builder: function(extra) -> list of CLI args appended after dump
SCRIPTS = [
    ("full_analyze", "起点：excep_store 解析 + 异常类型 + PC/LR + 寄存器 + 任务 + 栈 + memp + heap + 调用链 + 根因结论",
     "full-analyze",
     lambda extra: [],
     [r"## Root Cause Summary", r"## Exception Type", r"OVERFLOW", r"EXHAUSTED",
      r"CRITICAL", r"Likely LEAK", r"FULL"]),
    ("memp_verbose", "LWIP memp 池详细分配（verbose：每元素持有者，full-analyze 只给汇总）",
     "scan-memp",
     lambda extra: ["--verbose", "--util-threshold", "80"],
     [r"Summary:.*EXHAUSTED", r"Likely LEAK", r"EXHAUSTED"]),
    ("heap_verbose", "FreeRTOS heap 详细分配（verbose：所有活跃 trace_node，full-analyze 只给汇总）",
     "scan-heap",
     lambda extra: ["--verbose"],
     [r"CRITICAL", r"trace_node array FULL", r"HIGH USAGE"]),
]


def run_one(dump, script_subcmd, analysis_dir, seq, name, extra_args, extra):
    """运行 ec_dump_analyzer.py 的一个子命令，归档 stdout"""
    out_file = os.path.join(analysis_dir, "%02d_%s.txt" % (seq, name))
    cmd = [sys.executable,
           os.path.join(os.path.dirname(__file__), "ec_dump_analyzer.py"),
           script_subcmd, dump] + extra_args + extra
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


def extract_conclusions(text, regexes):
    """从输出中提取关键结论行"""
    hits = []
    in_summary = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        # Root Cause Summary 段整段提取
        if re.search(r"## Root Cause Summary", line):
            in_summary = True
            hits.append(stripped)
            continue
        if in_summary:
            if re.match(r"^##\s", line):  # 下一个 section
                in_summary = False
            else:
                hits.append(stripped)
                if len(hits) >= 20:
                    break
                continue
        # 关键标志行
        for rx in regexes:
            if re.search(rx, line) and stripped not in hits and len(hits) < 20:
                hits.append(stripped)
                break
    return hits[:15]


def main():
    ap = argparse.ArgumentParser(description="EC626 dump 一键分析 + 归档")
    ap.add_argument("dump", help="RAM dump .bin 文件")
    ap.add_argument("out_dir", help="分析输出根目录（bug 目录，如 .spec/bug/<id>_<desc>/）")
    ap.add_argument("--map", help="MAP file（scan-memp/scan-heap 必需）")
    ap.add_argument("--elf", help="ELF file（memp_desc + 源码行号映射）")
    ap.add_argument("--tcb-addr", help="手动指定 pxCurrentTCB 地址（full-analyze 用）")
    ap.add_argument("--store-addr", help="手动指定 excep_store 地址")
    args = ap.parse_args()

    dump_abs = os.path.abspath(args.dump)
    out_abs = os.path.abspath(args.out_dir)
    analysis_dir = os.path.join(out_abs, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print("EC626 dump analysis run — %s" % stamp)
    print("dump   : %s" % dump_abs)
    print("map    : %s" % (args.map or "(none)"))
    print("elf    : %s" % (args.elf or "(none)"))
    print("output : %s\n" % analysis_dir)

    # 构建各脚本共享的 extra 参数
    extra = []
    if args.map:
        extra += ["--map", args.map]
    if args.elf:
        extra += ["--elf", args.elf]
    if args.tcb_addr:
        extra += ["--tcb-addr", args.tcb_addr]
    if args.store_addr:
        extra += ["--store-addr", args.store_addr]

    results = []
    for seq, (name, purpose, subcmd, arg_builder, regexes) in enumerate(SCRIPTS, 1):
        # scan-memp/scan-heap 需要 --map
        if subcmd in ("scan-memp", "scan-heap") and not args.map:
            print("  skipping [%02d] %-18s ... (no --map, required for %s)" % (seq, subcmd, subcmd))
            results.append((seq, name, purpose, subcmd, "(skipped: no --map)", []))
            continue
        script_args = arg_builder(extra)
        print("  running [%02d] %-18s ... " % (seq, subcmd), end="", flush=True)
        out, out_file = run_one(dump_abs, subcmd, analysis_dir, seq, name, script_args, extra)
        concl = extract_conclusions(out, regexes)
        results.append((seq, name, purpose, subcmd, out_file, concl))
        print("ok (%d lines, %d key lines)" % (len(out.splitlines()), len(concl)))

    # ---- _meta.json ----
    meta = {
        "run_time": stamp,
        "dump_bin": dump_abs,
        "map": os.path.abspath(args.map) if args.map else None,
        "elf": os.path.abspath(args.elf) if args.elf else None,
        "tcb_addr": args.tcb_addr,
        "store_addr": args.store_addr,
        "bug_out_dir": out_abs,
        "scripts": [
            {"seq": seq, "name": name, "purpose": purpose,
             "subcommand": subcmd, "output": "analysis/%s" % of}
            for seq, name, purpose, subcmd, of, _ in results
        ],
        "note": "Results archived in bug dir for persistence. Cross-check against raw dump.",
    }
    with open(os.path.join(analysis_dir, "_meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    # ---- INDEX.md ----
    idx = os.path.join(analysis_dir, "INDEX.md")
    with open(idx, "w", encoding="utf-8") as f:
        f.write("# EC626 Dump 脚本输出索引\n\n")
        f.write("## 📌 核对原始数据\n\n")
        f.write("- **dump 文件**（核对原始 RAM dump）: `%s`\n" % dump_abs)
        f.write("- **MAP**: `%s`\n" % (os.path.abspath(args.map) if args.map else "(未提供)"))
        f.write("- **ELF**: `%s`\n" % (os.path.abspath(args.elf) if args.elf else "(未提供)"))
        f.write("- **运行时间**: %s\n" % stamp)
        f.write("- **完整上下文**: `analysis/_meta.json`\n\n")
        f.write("---\n\n## 各脚本输出（按分析流程顺序）\n\n")
        f.write("> 完整输出见同目录 `NN_<脚本名>.txt`。下方为**功能**与自动提取的**关键结论**。\n\n")
        for seq, name, purpose, subcmd, out_file, concl in results:
            f.write("### %02d. %s — %s\n\n" % (seq, name, purpose))
            f.write("- **子命令**: `ec_dump_analyzer.py %s` ｜ **完整输出**: `analysis/%s`\n\n" % (subcmd, out_file))
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
