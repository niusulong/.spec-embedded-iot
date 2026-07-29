#!/usr/bin/env python3
"""spec-embedded-iot 知识库统一 CLI（LLM-Wiki 路线）。

用法:
  # 归档（5 类知识统一入口）
  python kb.py archive --project {项目} --type bug --all
  python kb.py archive --project {项目} --type all --incremental
  python kb.py archive --project {项目} --type bug --name "COAP"

  # 校验
  python kb.py verify --project {项目} --type bug

  # wiki 维护
  python kb.py wiki lint                    # 检查 wiki 一致性
  python kb.py wiki status                  # wiki 覆盖率统计
  python kb.py wiki guide                   # 打印完整维护指南

  # 知识库状态
  python kb.py status

设计（LLM-Wiki 路线，已废弃向量检索）:
  - 归档 = 原文保真拷贝到 raw/platform/{平台}/{类型}/ + 输出 wiki 维护提示词
  - 检索 = agent 读 wiki/INDEX.md 渐进加载（无向量、无 DB）
  - wiki 维护 = agent 按 wiki/guide.py 的 GUIDE_TEXT 生成 entries/concepts
  - 详见 SKILL.md 与 wiki/guide.py
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common import (
    KNOWLEDGE_ROOT, META_FILE,
    load_config, infer_platform,
)


# ── archive 子命令 ────────────────────────────────────────

def _cmd_archive(args):
    """归档命令（转发到 wiki.archiver）。"""
    from wiki.archiver import cmd_archive as _archive
    _archive(args)


def _add_archive_parser(subparsers):
    p = subparsers.add_parser("archive", help="归档 .spec/ 文档到 raw 区")
    p.add_argument("--project", required=True, help="项目根目录路径")
    p.add_argument("--platform", help="平台名（默认从项目路径推断）")
    p.add_argument("--type", required=True,
                   choices=["bug", "requirement", "code-summary", "project-overview", "all"],
                   help="文档类型（all = 归档所有类型）")
    p.add_argument("--name", help="指定条目名称（支持部分匹配，仅 bug/requirement 有效）")
    p.add_argument("--all", action="store_true", help="归档所有条目")
    p.add_argument("--incremental", action="store_true", help="仅归档新增或内容变更的条目")
    p.add_argument("--no-wiki-prompt", action="store_true", dest="no_wiki_prompt",
                   help="跳过输出 wiki 维护提示词给 agent")
    p.set_defaults(func=_cmd_archive)


# ── verify 子命令 ─────────────────────────────────────────

def _cmd_verify(args):
    """校验 raw 区一致性（meta ↔ 文件）。"""
    from wiki.archiver import infer_role
    platform = args.platform or infer_platform(args.project)
    doc_type = args.type

    # bug/requirement: 检查 raw/platform/{plat}/{dest_dir}/ 下 meta 与文件一致性
    if doc_type in ("bug", "requirement"):
        cfg = load_config().get("doc_types", {}).get(doc_type)
        if not cfg:
            print(f"未知 doc_type: {doc_type}")
            return
        # raw 区路径（统一在 raw/platform/{plat}/{type}/ 下）
        dest_type_dir = os.path.join(
            KNOWLEDGE_ROOT, "raw", "platform", platform, cfg["dest_dir"]
        )
        meta_path = os.path.join(dest_type_dir, META_FILE)

        print(f"平台: {platform}")
        print(f"类型: {doc_type}")
        print()

        if not os.path.isfile(meta_path):
            print(f"[ERROR] meta 不存在: {meta_path}")
            print(f"        请先跑: python kb.py archive --project {{项目}} --type {doc_type} --all")
            return

        import json
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        entries = meta.get("entries", {})

        issues = []
        # 检查 1: meta 中的条目是否有对应目录和文件
        for name, info in entries.items():
            entry_dir = os.path.join(dest_type_dir, name)
            if not os.path.isdir(entry_dir):
                issues.append(f"  [缺失目录] {name}")
                continue
            for fmeta in info.get("files", []):
                fpath = os.path.join(entry_dir, fmeta["name"])
                if not os.path.isfile(fpath):
                    issues.append(f"  [缺失文件] {name}/{fmeta['name']}")

        # 检查 2: 目录中的子目录是否有 meta 记录（孤儿条目）
        if os.path.isdir(dest_type_dir):
            meta_names = set(entries.keys())
            for d in os.listdir(dest_type_dir):
                full = os.path.join(dest_type_dir, d)
                if os.path.isdir(full) and d not in meta_names:
                    issues.append(f"  [孤儿目录] {d}/（raw 区有目录但 meta 无记录）")

        if issues:
            print(f"发现 {len(issues)} 个问题:")
            for i in issues:
                print(i)
        else:
            print("[OK] raw 区完整性校验通过")

    elif doc_type == "code-summary":
        print("code-summary verify 暂未实现（TODO：meta ↔ 模块目录一致性）")
    elif doc_type == "project-overview":
        print("project-overview verify 暂未实现")


def _add_verify_parser(subparsers):
    p = subparsers.add_parser("verify", help="校验 raw 区完整性")
    p.add_argument("--project", help="项目根目录路径")
    p.add_argument("--platform", help="平台名")
    p.add_argument("--type", required=True,
                   choices=["bug", "requirement", "code-summary", "project-overview"],
                   help="文档类型")
    p.set_defaults(func=_cmd_verify)


# ── wiki 子命令集 ─────────────────────────────────────────

def _cmd_wiki_lint(args):
    """检查 wiki 一致性。"""
    from wiki.lint import lint_wiki, format_report
    wiki_root = os.path.join(KNOWLEDGE_ROOT, "wiki")
    if args.wiki_root:
        wiki_root = args.wiki_root
    report = lint_wiki(wiki_root)
    print(format_report(report))
    sys.exit(0 if report.is_clean() else 1)


def _cmd_wiki_status(args):
    """wiki 覆盖率统计。"""
    wiki_root = os.path.join(KNOWLEDGE_ROOT, "wiki")
    if not os.path.isdir(wiki_root):
        print(f"wiki 目录不存在: {wiki_root}")
        print("归档后让 agent 按 wiki/guide.py 维护 wiki 即可生成。")
        return

    import glob
    entries = glob.glob(os.path.join(wiki_root, "entries", "**", "*.md"), recursive=True)
    concepts = glob.glob(os.path.join(wiki_root, "concepts", "*.md"))

    print(f"wiki 目录: {wiki_root}")
    print()
    print(f"  entries/ 精炼页: {len(entries)}")
    # 按类型分组
    by_type = {}
    for e in entries:
        rel = os.path.relpath(e, os.path.join(wiki_root, "entries"))
        type_name = rel.split(os.sep)[0] if os.sep in rel else "other"
        by_type[type_name] = by_type.get(type_name, 0) + 1
    for t, c in sorted(by_type.items()):
        print(f"    {t}: {c}")
    print(f"  concepts/ 概念页: {len(concepts)}")

    has_index = os.path.isfile(os.path.join(wiki_root, "INDEX.md"))
    has_home = os.path.isfile(os.path.join(wiki_root, "Home.md"))
    print()
    print(f"  INDEX.md: {'有' if has_index else '无'}")
    print(f"  Home.md:  {'有' if has_home else '无'}")


def _cmd_wiki_guide(args):
    """打印完整 wiki 维护指南（GUIDE_TEXT）。"""
    from wiki.guide import get_guide_text
    print(get_guide_text())


def _add_wiki_parser(subparsers):
    wiki_parser = subparsers.add_parser("wiki", help="wiki 维护工具集")
    wiki_sub = wiki_parser.add_subparsers(dest="wiki_command", required=True)

    lint_p = wiki_sub.add_parser("lint", help="检查 wiki 一致性")
    lint_p.add_argument("--wiki-root", help="wiki 目录路径（默认 knowledge/wiki）")
    lint_p.set_defaults(func=_cmd_wiki_lint)

    status_p = wiki_sub.add_parser("status", help="wiki 覆盖率统计")
    status_p.set_defaults(func=_cmd_wiki_status)

    guide_p = wiki_sub.add_parser("guide", help="打印 wiki 维护指南")
    guide_p.set_defaults(func=_cmd_wiki_guide)


# ── status 子命令 ─────────────────────────────────────────

def _cmd_status(args):
    """知识库总状态（raw 区规模）。"""
    cfg = load_config()

    print(f"知识库: {KNOWLEDGE_ROOT}")
    print()

    # 扫描所有平台（统一从 raw/platform/ 读）
    platform_dir = os.path.join(KNOWLEDGE_ROOT, "raw", "platform")
    if not os.path.isdir(platform_dir):
        print("无 raw/platform/ 目录（请先归档）")
        return

    print("平台矩阵:")
    print(f"  {'平台':<20s} {'bug':>6s} {'requirement':>12s} {'code-summary':>13s} {'overview':>9s}")
    for platform in sorted(os.listdir(platform_dir)):
        pdir = os.path.join(platform_dir, platform)
        if not os.path.isdir(pdir):
            continue
        bug_count = _count_entries(pdir, "bug-solutions")
        req_count = _count_entries(pdir, "requirement-solutions")
        cs_count = _count_code_summary(pdir)
        overview = "有" if _has_overview(pdir) else "无"
        print(f"  {platform:<20s} {bug_count:>6d} {req_count:>12d} {cs_count:>13d} {overview:>9s}")

    # wiki 状态
    wiki_root = os.path.join(KNOWLEDGE_ROOT, "wiki")
    print()
    if os.path.isdir(wiki_root):
        import glob
        entries = glob.glob(os.path.join(wiki_root, "entries", "**", "*.md"), recursive=True)
        concepts = glob.glob(os.path.join(wiki_root, "concepts", "*.md"))
        print(f"wiki: {len(entries)} entries, {len(concepts)} concepts")
    else:
        print("wiki: 未建立（归档后让 agent 维护）")


def _count_entries(base_dir, type_name):
    """从 .archive_meta.json 读条目数。"""
    meta = os.path.join(base_dir, type_name, META_FILE)
    if not os.path.isfile(meta):
        return 0
    try:
        import json
        with open(meta, "r", encoding="utf-8") as f:
            return len(json.load(f).get("entries", {}))
    except Exception:
        return 0


def _count_code_summary(base_dir):
    cs_dir = os.path.join(base_dir, "code-summary")
    if not os.path.isdir(cs_dir):
        return 0
    return sum(1 for d in os.listdir(cs_dir)
               if os.path.isdir(os.path.join(cs_dir, d)))


def _has_overview(base_dir):
    return os.path.isfile(os.path.join(base_dir, "项目概览.md"))


def _add_status_parser(subparsers):
    p = subparsers.add_parser("status", help="知识库状态总览")
    p.set_defaults(func=_cmd_status)


# ── 主入口 ────────────────────────────────────────────────

def main(argv=None):
    """CLI 入口。"""
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        prog="kb.py",
        description="spec-embedded-iot 知识库统一 CLI（LLM-Wiki 路线）",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    _add_archive_parser(subparsers)
    _add_verify_parser(subparsers)
    _add_wiki_parser(subparsers)
    _add_status_parser(subparsers)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, KeyboardInterrupt) as e:
        print(f"错误: {e}", file=sys.stderr)
        sys.exit(2)
