#!/usr/bin/env python3
"""[DEPRECATED] 知识库归档工具（薄壳代理）。

本脚本已弃用，保留为兼容入口。请改用：
  python kb.py archive --project {项目} --type {类型} --all
  python kb.py verify --project {项目} --type {类型}

LLM-Wiki 路线后，归档/校验逻辑全部迁移到：
  - wiki/archiver.py  （多文件保真归档）
  - kb.py             （统一 CLI）

本文件仅保留：
  1. ensure_summary_field_row 等纯函数（test_archiver.py 依赖，不删）
  2. CLI main() 转发到 kb.py

旧向量时代的归档/合并/索引实现已全部删除。
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import SUMMARY_HEADING_RE


# ── 保留的纯函数（test_archiver.py 依赖）─────────────────

def ensure_summary_field_row(content, label, value):
    """若「结构化摘要」表格缺少 label 行，则在表头分隔线后插入 | **label** | value |。

    已存在该字段则原样返回（不覆盖已有值）；找不到摘要表格则原样返回。

    保留本函数是为了：
    1. test_archiver.py 的测试用例依赖它
    2. wiki 维护时 agent 偶尔可能需要类似逻辑（向原文表格注入字段）
       不过 LLM-Wiki 路线下，raw 区不应被修改——agent 应改 wiki/entries/ 而非 raw/。
    """
    lines = content.split("\n")
    try:
        head = next(i for i, ln in enumerate(lines)
                    if SUMMARY_HEADING_RE.search(ln))
    except StopIteration:
        return content
    # 节边界：下一个 ## 标题
    end = len(lines)
    for j in range(head + 1, len(lines)):
        if re.match(r"^##\s", lines[j]):
            end = j
            break
    section = lines[head:end]
    pat = re.compile(r"\|\s*\*{0,2}\s*" + re.escape(label) + r"\s*\*{0,2}\s*\|")
    if any(pat.search(ln) for ln in section):
        return content  # 已有该字段，不覆盖
    # 找表头分隔线（首个 |---| 行），其后插入
    try:
        sep = next(i for i, ln in enumerate(section, start=head)
                   if re.match(r"^\|[\s:|-]+\|\s*$", ln))
    except StopIteration:
        return content  # 无表格，不强制注入
    lines.insert(sep + 1, f"| **{label}** | {value} |")
    return "\n".join(lines)


# ── CLI 薄壳代理 ──────────────────────────────────────────

def main():
    """[DEPRECATED] 转发到 kb.py。"""
    print("[DEPRECATED] knowledge_archiver.py 已弃用，请改用 'python kb.py archive/verify'。"
          "本入口将在 2-3 个版本后移除。", file=sys.stderr)

    from kb import main as kb_main

    # argv 改写：knowledge_archiver.py archive --project X --type bug --all
    #        →  kb.py archive --project X --type bug --all
    # knowledge_archiver.py verify --project X --type bug
    #        →  kb.py verify --project X --type bug
    # 子命令名一致，参数透传即可
    if len(sys.argv) >= 2 and sys.argv[1] in ("archive", "verify", "status"):
        kb_main(sys.argv[1:])
    elif len(sys.argv) >= 2 and sys.argv[1] == "index":
        # 旧 index 命令（生成 index.md）在新路线下被 wiki/INDEX.md 取代
        print("旧 'index' 命令已被 wiki/INDEX.md 取代。归档后让 agent 维护 wiki。",
              file=sys.stderr)
        print("如要查看现有平铺 index.md，直接用 Read 打开 raw/{type}/index.md。",
              file=sys.stderr)
        sys.exit(2)
    else:
        print("用法: python knowledge_archiver.py {archive|verify} [options]", file=sys.stderr)
        print("（建议直接使用 python kb.py ...）", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyboardInterrupt) as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)
