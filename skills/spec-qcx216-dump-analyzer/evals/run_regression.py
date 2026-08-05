#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""QCX216 dump 分析器端到端回归测试。

对 regression.json 里每个 case：跑 `full-analyze`，校验输出含预期的关键结论（正则）。
用途——锁住脚本重构/优化（如异常帧还原、工具链分层、heap 校验改动）后，关键输出不漂移。

设计要点：
  - dump/elf 在 <repo>/.spec/logs/<id>/（.spec 被 gitignore，Glob 找不到；本脚本用文件系统直查）。
  - 找不到 dump/elf 的 case 自动 SKIP（不报错）——这样源副本（无测试 dump）跑时全 SKIP，
    项目副本（有 dump）跑时 PASS。回归实际在项目本地跑。
  - 工具链定位：设 QCX216_ARM_TOOLCHAIN 指向 <repo>/PLAT/tools/gcc，确保 objdump 后端可用。

用法:
  python run_regression.py                    # 跑全部 case
  python run_regression.py --case 7056787126  # 跑单个
  python run_regression.py --py D:/.../python.exe
"""
import os
import sys
import re
import json
import glob
import subprocess
import argparse

HERE = os.path.dirname(os.path.abspath(__file__))
# evals/ → skill → skills → .claude → repo_root（源副本路径不同，DUMP_ROOT 会指向不存在的
# .spec/logs，case 自动 SKIP，符合预期）
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
SKILL_SCRIPTS = os.path.abspath(os.path.join(HERE, "..", "scripts"))
DUMP_ROOT = os.path.join(REPO, ".spec", "logs")
CASES_FILE = os.path.join(HERE, "regression.json")

DEFAULT_PY = r"C:\Users\20220715012\AppData\Local\Programs\Python\Python312\python.exe"


def find_py():
    if os.path.isfile(DEFAULT_PY):
        return DEFAULT_PY
    return sys.executable


def resolve_files(case):
    """返回 (dump_path, elf_path)，找不到返回 (None, None)。"""
    cdir = os.path.join(DUMP_ROOT, case["id"])
    dumps = glob.glob(os.path.join(cdir, case.get("dump_glob", "RamDumpData_*.bin")))
    elf = os.path.join(cdir, case.get("elf", "ap_at_command.elf"))
    dump = sorted(dumps)[-1] if dumps else None  # 多个 dump 取最新
    return dump, elf


def run_case(py, case):
    dump, elf = resolve_files(case)
    if not dump or not os.path.isfile(elf):
        return ("SKIP", None, "dump/elf 不在 .spec/logs/%s/（.spec 被 gitignore；回归需在含测试 dump 的项目内跑）"
                % case["id"])
    cmd = [py, os.path.join(SKILL_SCRIPTS, "qcx216_dump_analyzer.py"),
           "full-analyze", dump, "--elf", elf]
    env = dict(os.environ)
    env.setdefault("QCX216_ARM_TOOLCHAIN",
                   os.path.join(REPO, "PLAT", "tools", "gcc", "arm-none-eabi", "bin"))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           encoding="utf-8", errors="replace", env=env)
    except Exception as e:
        return ("ERROR", None, "运行失败: %s" % e)
    out = r.stdout + ("\n" + r.stderr if r.stderr else "")
    # 任何 Python 异常都算 ERROR（不应发生）
    if "Traceback (most recent call last)" in out:
        return ("ERROR", out, "脚本抛异常（见输出）")
    fails = []
    for a in case.get("assertions", []):
        pat = a["re"] if isinstance(a, dict) else a
        if not re.search(pat, out):
            fails.append(pat)
    if fails:
        return ("FAIL", out, "未匹配断言 %d 条: %s" % (len(fails), fails))
    return ("PASS", out, "%d 断言全通过" % len(case.get("assertions", [])))


def main():
    ap = argparse.ArgumentParser(description="QCX216 dump 回归测试")
    ap.add_argument("--case", help="只跑指定 case id")
    ap.add_argument("--py", default=None, help="Python 解释器路径")
    args = ap.parse_args()

    with open(CASES_FILE, encoding="utf-8") as f:
        cases = json.load(f).get("cases", [])
    if args.case:
        cases = [c for c in cases if c["id"] == args.case]
    py = args.py or find_py()

    print("QCX216 dump 回归测试")
    print("  repo      : %s" % REPO)
    print("  dump root : %s" % DUMP_ROOT)
    print("  python    : %s\n" % py)

    stats = {"PASS": 0, "FAIL": 0, "SKIP": 0, "ERROR": 0}
    for c in cases:
        status, out, msg = run_case(py, c)
        stats[status] += 1
        mark = {"PASS": "✓", "FAIL": "✗", "SKIP": "–", "ERROR": "!"}[status]
        print("  %s [%-5s] %s — %s" % (mark, status, c["id"], c.get("scenario", "")))
        print("         %s" % msg)
        if status in ("FAIL", "ERROR") and out:
            for line in out.splitlines():
                if any(k in line for k in ("Exception Type", "Faulting PC", "Heap integrity",
                                            "backend", "Traceback", "Error", "Func")):
                    print("         | " + line.strip()[:120])
    print("\n汇总: PASS=%d  FAIL=%d  SKIP=%d  ERROR=%d" % (
        stats["PASS"], stats["FAIL"], stats["SKIP"], stats["ERROR"]))
    sys.exit(0 if stats["FAIL"] == 0 and stats["ERROR"] == 0 else 1)


if __name__ == "__main__":
    main()
