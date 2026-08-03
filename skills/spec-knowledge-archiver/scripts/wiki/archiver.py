"""多文件保真归档器（LLM-Wiki 路线）。

设计原则：
- raw 区只做"原文保真拷贝"，不做任何合并/改写/摘要提取
- 一个条目 = 一个目录（bug/requirement）；或保持原有结构（code-summary/项目概览）
- meta schema v2：记录 files 清单 + role，不再记录 summary（摘要交给 wiki 层）
- 增量归档基于 hash（同 v1 语义）
- 不再调 merge_md_files / extract_summary / 向量索引

支持的归档类型：
- bug-solutions:      源 .spec/bug/{wid}_{desc}/*.md → raw/bug-solutions/{wid}_{desc}/*.md
- requirement-solutions: 源 .spec/requirement/{id}_{desc}/*.md → raw/requirement-solutions/{id}_{desc}/*.md
- code-summary:       源 .spec/code-summary/{模块}/代码总结.md → raw/code-summary/{模块}/代码总结.md
- project-overview:   源 .spec/项目概览.md → raw/项目概览.md
- official-docs:      手动放置，不归档（保持 raw/official-docs-md/ 现有结构）

文件角色识别（用于 meta.files[].role）：
- bug-analysis / dump / solution / supplement / overview
"""

import hashlib
import os
import shutil
import sys
from datetime import datetime

from common import (
    KNOWLEDGE_ROOT, META_FILE, META_SCHEMA_VERSION,
    load_config, get_doc_type_config,
    infer_platform, atomic_write_text, exclusive_lock,
    load_meta, save_meta, extract_title, extract_work_item_id,
)

# 文件名 → 角色 启发式映射（用于 meta.files[].role 字段）
FILE_ROLE_PATTERNS = {
    "bug-analysis": ["Bug分析.md", "Bug日志分析.md", "AP分析.md", "日志分析.md"],
    "dump":         ["Dump分析.md", "dump分析.md", "Crash分析.md", "死机分析.md"],
    "solution":     ["修改方案.md", "解决方案.md", "修复方案.md", "方案.md"],
    "test":         ["测试报告.md", "验证报告.md", "验证步骤.md"],
    "summary":      ["代码总结.md"],
    "overview":     ["项目概览.md"],
}
DEFAULT_ROLE = "supplement"


def infer_role(filename):
    """根据文件名推断角色（bug-analysis / dump / solution / ...）。"""
    for role, names in FILE_ROLE_PATTERNS.items():
        if filename in names:
            return role
    return DEFAULT_ROLE


def is_log_file(filename):
    """判断是否日志文件（不归档）。

    注意：只判断"纯日志文件"（无扩展名或 .log 扩展，名为 log/logs），
    不误杀 log_notes.md / logging_分析.md 等以 "log" 开头的正常 .md 文档。
    .md 文档是否归档由调用点的 f.endswith(".md") 控制，本函数不重复判断扩展名。
    """
    lower = filename.lower()
    # 扩展名判定（这些扩展名一律不当文档归档）
    if lower.endswith((".txt", ".log", ".pcap", ".bin", ".elf", ".axf", ".map")):
        return True
    # basename（去扩展名）严格等于 log/logs（避免误杀 log_xxx.md）
    import os as _os
    stem = _os.path.splitext(_os.path.basename(lower))[0]
    return stem in ("log", "logs")


def list_source_entries(source_dir):
    """列出 .spec/{type}/ 下所有条目目录（每个子目录 = 一个条目）。

    用于 bug-solutions / requirement-solutions。
    返回 [{"name", "path", "md_files"}, ...]
    """
    if not os.path.isdir(source_dir):
        return []
    entries = []
    for item in sorted(os.listdir(source_dir)):
        item_path = os.path.join(source_dir, item)
        if not os.path.isdir(item_path):
            continue
        # 收集 .md 文件（排除 logs/ 子目录）
        md_files = []
        for f in sorted(os.listdir(item_path)):
            full = os.path.join(item_path, f)
            if os.path.isfile(full) and f.endswith(".md") and not is_log_file(f):
                md_files.append(f)
        if md_files:
            entries.append({"name": item, "path": item_path, "md_files": md_files})
    return entries


def compute_entry_hash(entry_path, md_files):
    """计算条目所有 .md 文件的联合 hash（用于增量判断）。"""
    h = hashlib.md5()
    for md_file in md_files:
        filepath = os.path.join(entry_path, md_file)
        try:
            with open(filepath, "rb") as f:
                h.update(f.read())
        except OSError as e:
            # 不可读文件不能静默——会让 hash 偏小、增量归档误判"未变"而永远跳过该条目
            print("[warn] compute_entry_hash 跳过不可读文件 %s: %s" % (md_file, e))
    return h.hexdigest()


def copy_entry_files(src_entry_path, dest_entry_path, md_files):
    """把条目的所有 .md 原样拷贝到 dest（覆盖式）。返回拷贝的文件数。"""
    os.makedirs(dest_entry_path, exist_ok=True)
    count = 0
    for md_file in md_files:
        src = os.path.join(src_entry_path, md_file)
        dst = os.path.join(dest_entry_path, md_file)
        if os.path.isfile(src):
            shutil.copy2(src, dst)
            count += 1
    return count


def cleanup_orphan_files(dest_entry_path, current_md_files):
    """删除 dest_entry_path 下不在 current_md_files 列表里的 .md（迁移/改名后的孤儿）。"""
    if not os.path.isdir(dest_entry_path):
        return
    keep = set(current_md_files)
    for f in os.listdir(dest_entry_path):
        if f.endswith(".md") and f not in keep:
            try:
                os.remove(os.path.join(dest_entry_path, f))
            except OSError:
                pass


# ── 归档核心：bug-solutions / requirement-solutions（多文件条目）──────────────

def archive_multi_file_entry(entry, dest_type_dir, platform, meta_entries, doc_type):
    """归档一个多文件条目（bug/requirement）到 raw/{type}/{entry_name}/。

    返回 (entry_name, title, is_new) 或 None（跳过）。
    """
    entry_name = entry["name"]
    title = extract_title(entry_name)
    work_item_id = extract_work_item_id(entry_name)
    dest_entry_path = os.path.join(dest_type_dir, entry_name)

    content_hash = compute_entry_hash(entry["path"], entry["md_files"])
    existing = meta_entries.get(entry_name)

    # 增量跳过
    if existing and existing.get("hash") == content_hash:
        return None  # 无变化

    # 原样拷贝
    copy_entry_files(entry["path"], dest_entry_path, entry["md_files"])
    # 清理孤儿（上次归档的文件本次没收集到 → 删除）
    cleanup_orphan_files(dest_entry_path, entry["md_files"])

    # 更新 meta（schema v2）
    files_meta = [{"name": f, "role": infer_role(f)} for f in entry["md_files"]]
    meta_entries[entry_name] = {
        "title": title,
        "work_item_id": work_item_id or "NA",
        "platform": platform,
        "doc_type": doc_type,
        "files": files_meta,
        "primary_file": _pick_primary_file(files_meta),
        "hash": content_hash,
        "archived_at": datetime.now().isoformat(),
        "schema_version": META_SCHEMA_VERSION,
    }
    return (entry_name, title, existing is None)


def _pick_primary_file(files_meta):
    """从 files_meta 选主文件（bug-analysis 角色优先，否则第一个）。"""
    for f in files_meta:
        if f["role"] == "bug-analysis":
            return f["name"]
    return files_meta[0]["name"] if files_meta else None


# ── 归档核心：code-summary（单文件 + 模块=目录）────────────────────────────

def archive_code_summary(project_path, platform, meta_entries_by_section):
    """归档 .spec/code-summary/{模块}/代码总结.md → raw/code-summary/{模块}/代码总结.md。

    meta_entries_by_section: dict，key=模块名，value=该模块的 meta dict（被原地更新）。
    返回 [(模块名, is_new), ...]
    """
    source_root = os.path.join(project_path, ".spec", "code-summary")
    if not os.path.isdir(source_root):
        return []

    results = []
    for module_name in sorted(os.listdir(source_root)):
        module_path = os.path.join(source_root, module_name)
        if not os.path.isdir(module_path):
            continue
        summary_file = os.path.join(module_path, "代码总结.md")
        if not os.path.isfile(summary_file):
            continue

        content_hash = _hash_files([summary_file])
        existing = meta_entries_by_section.get(module_name)
        if existing and existing.get("hash") == content_hash:
            continue  # 无变化

        dest_module_dir = os.path.join(
            KNOWLEDGE_ROOT, "raw", "platform", platform, "code-summary", module_name
        )
        os.makedirs(dest_module_dir, exist_ok=True)
        shutil.copy2(summary_file, os.path.join(dest_module_dir, "代码总结.md"))

        meta_entries_by_section[module_name] = {
            "title": f"{platform}/{module_name} 代码总结",
            "platform": platform,
            "module": module_name,
            "doc_type": "code-summary",
            "files": [{"name": "代码总结.md", "role": "summary"}],
            "primary_file": "代码总结.md",
            "hash": content_hash,
            "archived_at": datetime.now().isoformat(),
            "schema_version": META_SCHEMA_VERSION,
        }
        results.append((module_name, existing is None))

    return results


# ── 归档核心：project-overview ──────────────────────────────────────────────

def archive_project_overview(project_path, platform, meta_entries):
    """归档 .spec/项目概览.md → raw/项目概览.md。"""
    source = os.path.join(project_path, ".spec", "项目概览.md")
    if not os.path.isfile(source):
        return None

    content_hash = _hash_files([source])
    existing = meta_entries.get("项目概览.md")
    if existing and existing.get("hash") == content_hash:
        return None

    dest = os.path.join(KNOWLEDGE_ROOT, "raw", "platform", platform, "项目概览.md")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    shutil.copy2(source, dest)

    meta_entries["项目概览.md"] = {
        "title": f"{platform} 项目概览",
        "platform": platform,
        "doc_type": "project-overview",
        "files": [{"name": "项目概览.md", "role": "overview"}],
        "primary_file": "项目概览.md",
        "hash": content_hash,
        "archived_at": datetime.now().isoformat(),
        "schema_version": META_SCHEMA_VERSION,
    }
    return ("项目概览.md", existing is None)


def _hash_files(filepaths):
    h = hashlib.md5()
    for fp in filepaths:
        try:
            with open(fp, "rb") as f:
                h.update(f.read())
        except OSError:
            pass
    return h.hexdigest()


# ── wiki 区骨架初始化 ──────────────────────────────────────────────────────

# 骨架文件内容（首次归档时创建，agent 后续会修改/扩充）
_HOME_SKELETON = """\
---
title: spec-embedded-iot 知识库
date: {date}
type: home
tags: [home, 入口]
---

# spec-embedded-iot 知识库

> 嵌入式 IoT 模组 bug 案例 + 代码理解 + 协议参考。LLM-Wiki 路线，跨平台综合。

## 平台矩阵

> 由 agent 维护。归档后从 `kb.py status` 抄数据。

| 平台 | Bug 案例 | 代码总结 | 入口 |
|------|---------|---------|------|
| EC626 | TBD | TBD | [raw](../raw/platform/EC626/) |
| ... | ... | ... | ... |

## 检索入口
- 找具体案例 → INDEX.md
- 找某类问题共性 → concepts/
- 看某平台代码结构 → 该平台 `raw/code-summary/`

## 高频概念
> 由 agent 维护（concept 页累积到 3+ 时列出 Top 10）
- 待补充

---
*首次归档自动创建于 {date}。请 agent 按需修改本文件。*
"""

_INDEX_SKELETON = """\
---
title: spec-embedded-iot 知识库索引
date: {date}
type: index
tags: [index, 目录]
---

# 知识库索引

> 全部条目的一行摘要。检索时先读这里锁定候选，再读 entries/ 或 concepts/。
> **由 agent 维护**：归档后追加新行；保持精简（一行一条，让 agent 一屏读完）。
> 注：本骨架模板里 `TBD` 是占位符，agent 维护时替换为实际内容并删除占位行。

## Bug 案例

> 格式：`| WID | 标题 | 平台 | 模块 | 一句话根因 | 链接 |`
> 链接形如 `entries/bug-solutions/EC626_xxx.md`（agent 写真实条目文件名）

| WID | 标题 | 平台 | 模块 | 一句话根因 | 链接 |
|-----|------|------|------|-----------|------|
| TBD | （agent 维护：删除此占位行，按上面格式追加真实条目） | | | | |

## 代码总结

| 平台 | 模块 | 链接 |
|------|------|------|
| TBD | （agent 维护） | |

## 跨案例概念

> 由 agent 在 concepts/ 综合后在这里登记

| 概念 | 涉及案例数 | 最后更新 | 链接 |
|------|-----------|---------|------|
| TBD | （agent 维护） | | |

---
*首次归档自动创建于 {date}。agent 维护本文件。*
"""


def init_wiki_skeleton(knowledge_root):
    """首次归档时初始化 wiki/ 骨架（Home.md / INDEX.md / entries/ / concepts/）。

    幂等：wiki/ 已存在则不覆盖任何文件，只补缺失的子目录。
    返回 True 表示新建了骨架，False 表示已存在未动。
    """
    wiki_root = os.path.join(knowledge_root, "wiki")
    already_existed = os.path.isdir(wiki_root)

    # 创建子目录
    for sub in ("entries/bug-solutions", "entries/code-summary",
                "entries/requirement-solutions", "concepts"):
        os.makedirs(os.path.join(wiki_root, sub), exist_ok=True)

    today = datetime.now().strftime("%Y-%m-%d")
    created_files = []

    # Home.md（不存在才写）
    home_path = os.path.join(wiki_root, "Home.md")
    if not os.path.isfile(home_path):
        atomic_write_text(home_path, _HOME_SKELETON.format(date=today))
        created_files.append("Home.md")

    # INDEX.md（不存在才写）
    index_path = os.path.join(wiki_root, "INDEX.md")
    if not os.path.isfile(index_path):
        atomic_write_text(index_path, _INDEX_SKELETON.format(date=today))
        created_files.append("INDEX.md")

    return not already_existed, created_files


# ── 命令实现 ────────────────────────────────────────────────────────────────

def cmd_archive(args):
    """执行 archive 命令（多类型归档 + 触发 wiki 维护提示词）。"""
    platform = args.platform or infer_platform(args.project)
    doc_type = getattr(args, "type", None)

    if doc_type not in ("bug", "requirement", "code-summary", "project-overview", "all"):
        print(f"不支持的 type '{doc_type}'，支持: bug, requirement, code-summary, project-overview, all")
        sys.exit(1)

    archived_summary = []  # 用于最后输出给 agent 的触发语

    print(f"平台: {platform}")
    print(f"项目: {args.project}")
    print()

    # 选择要归档的类型
    types_to_archive = (
        ["bug", "requirement", "code-summary", "project-overview"]
        if doc_type == "all" else [doc_type]
    )

    for dt in types_to_archive:
        result = _archive_one_type(args, platform, dt)
        if result:
            archived_summary.extend(result)

    # wiki 区骨架初始化（首次归档自动建，已存在则幂等跳过）
    wiki_created, created_files = init_wiki_skeleton(KNOWLEDGE_ROOT)
    if created_files:
        print()
        print(f"[wiki 初始化] 首次归档，已创建 wiki/ 骨架: {', '.join(created_files)}")
        print("             agent 应按 wiki/guide.py 维护这些文件")

    # 输出给 agent 的 wiki 维护触发语
    if archived_summary and not getattr(args, "no_wiki_prompt", False):
        from wiki.guide import format_archive_trigger
        trigger = format_archive_trigger(archived_summary, "wiki/guide.py::GUIDE_TEXT")
        print()
        print(trigger)

    # 追加 log.md 条目（对齐 llm-wiki.md 的 log 机制）
    if archived_summary:
        from wiki.log_writer import append_log
        # 按 (platform, type) 聚合条目数
        plat_type_counts = {}
        for item in archived_summary:
            key = (item.get("platform", "unknown"), item.get("type", "bug"))
            plat_type_counts[key] = plat_type_counts.get(key, 0) + 1
        for (p, t), c in plat_type_counts.items():
            append_log(
                KNOWLEDGE_ROOT,
                op="ingest",
                detail=f"归档 {c} 个 {t} 条目",
                platform=p,
                count=c,
            )

    print()
    print(f"归档完成: {len(archived_summary)} 个新/更新条目")


def _archive_one_type(args, platform, doc_type):
    """归档单一类型。返回 archived_summary 列表（供触发语用）。"""
    project_path = args.project

    if doc_type in ("bug", "requirement"):
        return _archive_multi_file_type(args, platform, doc_type)
    elif doc_type == "code-summary":
        return _archive_code_summary_wrapper(args, platform)
    elif doc_type == "project-overview":
        return _archive_project_overview_wrapper(args, platform)
    return []


def _archive_multi_file_type(args, platform, doc_type):
    """bug / requirement 归档（多文件条目）。"""
    dt_cfg = get_doc_type_config(doc_type)
    source_dir = os.path.join(args.project, ".spec", dt_cfg["source_dir"])
    dest_type_dir = os.path.join(
        KNOWLEDGE_ROOT, "raw", "platform", platform, dt_cfg["dest_dir"]
    )
    meta_path = os.path.join(dest_type_dir, META_FILE)

    if not os.path.isdir(source_dir):
        return []

    entries = list_source_entries(source_dir)
    if not entries:
        return []

    # 筛选
    if args.name:
        entries = [e for e in entries if args.name in e["name"]
                   or args.name in extract_title(e["name"])]
        if not entries:
            print(f"[{doc_type}] 未找到匹配 '{args.name}' 的条目")
            return []

    archived = []
    with exclusive_lock(os.path.join(os.path.dirname(meta_path) or ".", ".archive.lock")):
        os.makedirs(dest_type_dir, exist_ok=True)
        meta = load_meta(dest_type_dir)
        meta_entries = meta.setdefault("entries", {})

        # 增量筛选
        if args.incremental:
            filtered = []
            for e in entries:
                existing = meta_entries.get(e["name"])
                if not existing:
                    filtered.append(e)
                else:
                    current_hash = compute_entry_hash(e["path"], e["md_files"])
                    if existing.get("hash") != current_hash:
                        filtered.append(e)
            entries = filtered

        print(f"[{doc_type}] 待归档: {len(entries)} 条")

        for entry in entries:
            result = archive_multi_file_entry(
                entry, dest_type_dir, platform, meta_entries, doc_type
            )
            if result:
                entry_name, title, is_new = result
                status = "新增" if is_new else "更新"
                print(f"  [{status}] {title}")
                archived.append({
                    "type": dt_cfg["dest_dir"],
                    "platform": platform,
                    "name": entry_name,
                    "title": title,
                    "files": entry["md_files"],
                    "is_new": is_new,
                })
            else:
                print(f"  [跳过] {entry['name']}（内容无变化）")

        save_meta(dest_type_dir, meta)

    return archived


def _archive_code_summary_wrapper(args, platform):
    """code-summary 归档包装（meta 存在 raw/code-summary/.archive_meta.json）。"""
    dest_type_dir = os.path.join(
        KNOWLEDGE_ROOT, "raw", "platform", platform, "code-summary"
    )
    meta_path = os.path.join(dest_type_dir, META_FILE)
    archived = []

    with exclusive_lock(os.path.join(os.path.dirname(meta_path) or ".", ".archive.lock")):
        os.makedirs(dest_type_dir, exist_ok=True)
        meta = load_meta(dest_type_dir)
        entries_by_module = meta.setdefault("entries", {})

        results = archive_code_summary(args.project, platform, entries_by_module)
        for module_name, is_new in results:
            status = "新增" if is_new else "更新"
            print(f"  [code-summary][{status}] {module_name}")
            archived.append({
                "type": "code-summary",
                "platform": platform,
                "name": module_name,
                "title": f"{module_name} 代码总结",
                "files": ["代码总结.md"],
                "is_new": is_new,
            })

        if results:
            save_meta(dest_type_dir, meta)

    return archived


def _archive_project_overview_wrapper(args, platform):
    """project-overview 归档包装。"""
    dest_type_dir = os.path.join(
        KNOWLEDGE_ROOT, "raw", "platform", platform
    )
    meta_path = os.path.join(dest_type_dir, ".overview_meta.json")
    archived = []

    with exclusive_lock(os.path.join(os.path.dirname(meta_path) or ".", ".overview.lock")):
        os.makedirs(dest_type_dir, exist_ok=True)
        # 直接读 meta_path（.overview_meta.json），不走 load_meta（它固定读 .archive_meta.json）
        # 避免 bug：重命名后下次 load_meta 找不到文件导致 existing 恒为 None
        from common import load_json, META_SCHEMA_VERSION
        meta = load_json(meta_path, {"entries": {}})
        entries = meta.setdefault("entries", {})

        result = archive_project_overview(args.project, platform, entries)
        if result:
            name, is_new = result
            status = "新增" if is_new else "更新"
            print(f"  [project-overview][{status}]")
            archived.append({
                "type": "project-overview",
                "platform": platform,
                "name": "项目概览.md",
                "title": f"{platform} 项目概览",
                "files": ["项目概览.md"],
                "is_new": is_new,
            })
            # 直接写到 .overview_meta.json（避免 save_meta + rename 的两步弯路）
            from common import save_json
            meta["schema_version"] = META_SCHEMA_VERSION
            save_json(meta_path, meta)

    return archived
