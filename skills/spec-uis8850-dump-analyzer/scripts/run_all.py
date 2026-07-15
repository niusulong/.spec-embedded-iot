#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""UIS8850 run-all: run every analysis script, archive numbered outputs,
generate INDEX.md + _meta.json, write dump reverse-link.

Usage:  python run_all.py <dump_dir> <ap_elf> <bug_out_dir> [--elf2 <elf>] [--map <map>]
"""
import os, sys, subprocess, argparse, json, datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import Mem, Symbols, find_toolchain, PSRAM_BASE

SCRIPTS = [
    ("01_uis8850_analyze", "uis8850_analyze.py"),
    ("02_unwind", "unwind.py"),
    ("03_threads", "threads.py"),
    ("04_cp_assert", "cp_assert.py"),
    ("05_heap_state", "heap_state.py"),
    ("06_wdt_reset", "wdt_reset.py"),
    ("07_trace_decode", "trace_decode.py"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump_dir")
    ap.add_argument("ap_elf")
    ap.add_argument("bug_out_dir")
    ap.add_argument("--elf2", default=None)
    ap.add_argument("--map", default=None)
    args = ap.parse_args()

    dump_dir = os.path.abspath(args.dump_dir)
    ap_elf = os.path.abspath(args.ap_elf)
    out_dir = os.path.abspath(args.bug_out_dir)
    analysis_dir = os.path.join(out_dir, "analysis")
    os.makedirs(analysis_dir, exist_ok=True)

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    tc = find_toolchain(dump_dir)
    py = sys.executable

    print("=" * 80)
    print(" UIS8850 一键分析 + 归档")
    print(" dump_dir : %s" % dump_dir)
    print(" ap_elf   : %s" % ap_elf)
    print(" out_dir  : %s" % out_dir)
    print(" toolchain: %s" % tc)
    print("=" * 80)

    # 版本信息
    syms = Symbols(ap_elf)
    mem = Mem(dump_dir)
    rev_addr = syms.lookup("gBuildRevision")[0]
    firmware = mem.cstr(rev_addr, 80).strip() if rev_addr else "?"

    results = {}
    for name, script in SCRIPTS:
        out_path = os.path.join(analysis_dir, name + ".txt")
        cmd = [py, os.path.join(scripts_dir, script), dump_dir, ap_elf]
        if script == "uis8850_analyze.py" and args.elf2:
            cmd += ["--elf2", args.elf2]
        print("\n>>> 运行 %s ..." % script)
        with open(out_path, "w", encoding="utf-8") as f:
            try:
                subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT,
                               timeout=300, check=False)
                results[name] = "ok"
                print("    -> %s" % out_path)
            except subprocess.TimeoutExpired:
                f.write("\n[TIMEOUT]\n")
                results[name] = "timeout"
                print("    -> TIMEOUT")
            except Exception as e:
                f.write("\n[ERROR: %s]\n" % e)
                results[name] = "error: %s" % e
                print("    -> ERROR: %s" % e)

    # ---- INDEX.md ----
    index_path = os.path.join(analysis_dir, "INDEX.md")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# UIS8850 Dump 分析产物索引\n\n")
        f.write("## 📌 核对原始数据\n\n")
        f.write("- **Dump 目录**: `%s`\n" % dump_dir)
        f.write("- **AP ELF**: `%s`\n" % ap_elf)
        if args.elf2:
            f.write("- **第二版本 ELF**: `%s`\n" % args.elf2)
        f.write("- **工具链**: `%s`\n" % (tc or "未找到"))
        f.write("- **固件版本**: `%s`\n\n" % firmware)
        f.write("## 脚本输出\n\n")
        f.write("| 文件 | 脚本 | 状态 |\n|---|---|---|\n")
        for name, script in SCRIPTS:
            f.write("| %s.txt | %s | %s |\n" % (name, script, results.get(name, "?")))
        f.write("\n## 关键结论核对方法\n\n")
        f.write("- 版本: `80000000.bin` offset `0x%x` (=gBuildRevision-0x%08x) 起 = 版本串\n" % (
            (rev_addr - PSRAM_BASE) if rev_addr else 0, PSRAM_BASE))
        f.write("- gIsPanic/gBlueScreenAbortType/gBlueScreenRegs: 见 01_uis8850_analyze.txt\n")
        f.write("- 栈溢出任务 + magic: 见 03_threads.txt\n")
        f.write("- CP assert 寄存器 + CP PC 反汇编: 见 04_cp_assert.txt\n")
        f.write("- 调用链 + call site 验证: 见 02_unwind.txt\n")
    print("\n>>> INDEX.md -> %s" % index_path)

    # ---- _meta.json ----
    meta = {
        "platform": "UIS8850",
        "arch": "ARM (EM_ARM, Cortex-R, FreeRTOS)",
        "dump_dir": dump_dir,
        "ap_elf": ap_elf,
        "elf2": args.elf2,
        "toolchain": tc,
        "firmware": firmware,
        "run_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scripts": {name: results.get(name) for name, _ in SCRIPTS},
    }
    meta_path = os.path.join(analysis_dir, "_meta.json")
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(">>> _meta.json -> %s" % meta_path)

    # ---- dump 反向 link ----
    ptr_path = os.path.join(dump_dir, "_analysis_pointer.txt")
    with open(ptr_path, "w", encoding="utf-8") as f:
        f.write("本 dump 的分析结论归档于:\n  %s\n\n" % out_dir)
        f.write("  analysis/        脚本输出 + INDEX.md + _meta.json\n")
        f.write("  Dump分析.md      主报告 (若有)\n\n")
        f.write("固件版本: %s\n" % firmware)
    print(">>> 反向链接 -> %s" % ptr_path)

    print("\n" + "=" * 80)
    print(" 完成. 归档目录: %s" % analysis_dir)
    print("=" * 80)


if __name__ == "__main__":
    main()
