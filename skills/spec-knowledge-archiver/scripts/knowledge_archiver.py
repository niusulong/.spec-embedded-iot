#!/usr/bin/env python3
"""
知识库归档工具 - 将项目 .spec/bug 等文档归档到知识库。

用法:
  # 归档单个条目
  python knowledge_archiver.py archive --project D:/EC626 --type bug --name "COAP"

  # 批量归档
  python knowledge_archiver.py archive --project D:/EC626 --type bug --all

  # 增量归档（仅归档新增或更新的条目）
  python knowledge_archiver.py archive --project D:/EC626 --type bug --incremental

  # 校验知识库完整性
  python knowledge_archiver.py verify --project D:/EC626 --type bug

  # 查看归档状态
  python knowledge_archiver.py status --project D:/EC626
"""

import argparse
import hashlib
import os
import re
import sys
from datetime import datetime

from extract_summary import extract_summary as extract_summary_from_md

from common import (
    KNOWLEDGE_ROOT, META_FILE,
    load_config, get_doc_type_config,
    infer_platform, get_dest_dir, get_source_dir,
    load_meta, save_meta,
    safe_filename, extract_title, extract_work_item_id,
    atomic_write_text,
)

# 向量索引更新（可选依赖）
try:
    from embed_indexer import update_index as update_vector_index
    HAS_VECTOR_INDEXER = True
except ImportError:
    HAS_VECTOR_INDEXER = False


# ── 文件操作 ──────────────────────────────────────────────

def list_entries(source_dir):
    """列出源目录下所有条目（子目录）"""
    if not os.path.isdir(source_dir):
        return []
    entries = []
    for item in sorted(os.listdir(source_dir)):
        item_path = os.path.join(source_dir, item)
        if os.path.isdir(item_path):
            md_files = [f for f in os.listdir(item_path)
                        if f.endswith(".md") and os.path.isfile(os.path.join(item_path, f))]
            if md_files:
                entries.append({
                    "name": item,
                    "path": item_path,
                    "md_files": sorted(md_files),
                })
    return entries


def merge_md_files(entry):
    """合并一个条目下所有 md 文件为一个文档。

    主文档置顶规则：含「结构化摘要」节的文档作为主文档排在首位（文件名
    随意，适配需求文档/方案等多文件场景）；若无，则兼容旧主文件名
    (Bug分析.md/需求分析.md)。主文档原样保留，其余文档作为分节拼接。
    """
    items = []
    for md_file in entry["md_files"]:
        filepath = os.path.join(entry["path"], md_file)
        with open(filepath, "r", encoding="utf-8", errors="replace") as f:
            content = f.read().strip()
        if content:
            items.append((md_file, content))
    if not items:
        return None

    summary_re = re.compile(r"^##\s*\d*[\.\s]*结构化摘要", re.MULTILINE)
    primary_idx = next(
        (i for i, (_, c) in enumerate(items) if summary_re.search(c)), None
    )

    def order_key(i):
        md_file = items[i][0]
        if primary_idx is not None:
            return (0,) if i == primary_idx else (1, i)
        return (0,) if md_file in ("Bug分析.md", "需求分析.md") else (1, i)

    ordered = sorted(range(len(items)), key=order_key)

    sections = []
    for pos, i in enumerate(ordered):
        md_file, content = items[i]
        if pos == 0:
            sections.append(content)  # 主文档原样
        else:
            section_title = os.path.splitext(md_file)[0]
            if content.startswith("#"):
                sections.append(content)
            else:
                sections.append(f"## {section_title}\n\n{content}")
    if len(sections) == 1:
        return sections[0]
    merged = sections[0]
    for section in sections[1:]:
        merged += f"\n\n---\n\n{section}"
    return merged


def compute_content_hash(entry):
    """计算条目内容的哈希，用于增量归档判断"""
    h = hashlib.md5()
    for md_file in entry["md_files"]:
        filepath = os.path.join(entry["path"], md_file)
        with open(filepath, "rb") as f:
            h.update(f.read())
    return h.hexdigest()


# ── 归档核心逻辑 ─────────────────────────────────────────

def _summary_field_label(doc_type_config, key):
    """返回 summary_fields 中 key 对应的中文 label（如 work_item_id -> '工作项 ID'），无则 None。"""
    sf = (doc_type_config or {}).get("summary_fields", {})
    for label, k in sf.items():
        if k == key:
            return label
    return None


def ensure_summary_field_row(content, label, value):
    """若「结构化摘要」表格缺少 label 行，则在表头分隔线后插入 | **label** | value |。
    已存在该字段则原样返回（不覆盖已有值）；找不到摘要表格则原样返回。"""
    lines = content.split("\n")
    try:
        head = next(i for i, ln in enumerate(lines)
                    if re.match(r"^##\s*\d*[\.\s]*结构化摘要\s*$", ln))
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


def archive_entry(entry, dest_dir, meta, platform, content_hash=None, doc_type_config=None):
    """归档单个条目，返回 (title, output_filename, is_new)。
    content_hash 可传入以避免重复读文件。
    doc_type_config 传给 extract_summary 以支持不同文档类型的摘要提取。"""
    title = extract_title(entry["name"])
    work_item_id = extract_work_item_id(entry["name"])
    output_file = safe_filename(title, platform)
    output_path = os.path.join(dest_dir, output_file)

    merged = merge_md_files(entry)
    if merged is None:
        return None

    if content_hash is None:
        content_hash = compute_content_hash(entry)

    existing = meta["entries"].get(entry["name"])

    # 文件名是否变更（如新增平台前缀的迁移场景）：变更时必须重写新文件
    filename_changed = bool(
        existing and existing.get("file") and existing["file"] != output_file
    )

    # 内容未变且文件名未变才跳过
    if existing and not filename_changed and existing.get("hash") == content_hash:
        return (title, output_file, False)

    # 确保结构化摘要含「工作项 ID」字段：缺失则注入，无单号填 NA
    wid_label = _summary_field_label(doc_type_config, "work_item_id")
    if wid_label:
        merged = ensure_summary_field_row(merged, wid_label, work_item_id or "NA")

    # 原子写入新文件
    atomic_write_text(output_path, merged)

    # 迁移：新文件写入成功后，再删除旧文件名（避免 hash 命中时删旧却不写新导致丢失）
    if filename_changed:
        old_path = os.path.join(dest_dir, existing["file"])
        if old_path != output_path and os.path.isfile(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    # 提取结构化摘要
    summary = None
    try:
        summary = extract_summary_from_md(output_path, doc_type_config)
    except Exception:
        pass

    # 工作项 ID 兜底：摘要缺失则用文件夹单号，再缺失填 NA
    wid_value = (summary.get("work_item_id") if summary else None) or work_item_id or "NA"
    if summary:
        summary["work_item_id"] = wid_value

    entry_meta = {
        "title": title,
        "work_item_id": wid_value,
        "file": output_file,
        "hash": content_hash,
        "archived_at": datetime.now().isoformat(),
        "source_files": entry["md_files"],
    }
    if summary:
        entry_meta["summary"] = summary

    meta["entries"][entry["name"]] = entry_meta
    is_new = existing is None
    return (title, output_file, is_new)


def cleanup_deleted_entries(meta, source_entries, dest_dir):
    """清理源目录中已删除的条目：从 meta 移除，删除对应文件。
    返回被清理的 entry_name 列表。"""
    source_names = {e["name"] for e in source_entries}
    removed = []
    for entry_name in list(meta["entries"].keys()):
        if entry_name not in source_names:
            info = meta["entries"].pop(entry_name)
            filepath = os.path.join(dest_dir, info.get("file", ""))
            if os.path.isfile(filepath):
                try:
                    os.remove(filepath)
                except OSError:
                    pass
            removed.append(info.get("title", entry_name))
    return removed


# ── 索引生成 ─────────────────────────────────────────────

def generate_index(dest_dir, platform, doc_type, meta=None):
    """生成索引文件，列从配置动态读取。meta 可传入避免重复加载。"""
    if meta is None:
        meta = load_meta(dest_dir)
    if not meta["entries"]:
        print(f"  无已归档条目，跳过索引生成")
        return

    cfg = get_doc_type_config(doc_type)
    columns = cfg.get("index_columns", {})
    max_lengths = cfg.get("max_field_lengths", {})

    # 构建表头
    header_cols = ["#"]
    sep_cols = ["---"]
    for _, label in columns.items():
        header_cols.append(label)
        sep_cols.append("---")
    header_cols.append("文件")
    sep_cols.append("------")

    lines = [
        f"# {doc_type} 索引 - {platform}",
        "",
        f"> 自动生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}，共 {len(meta['entries'])} 条",
        "",
        "| " + " | ".join(header_cols) + " |",
        "|" + "|".join(sep_cols) + "|",
    ]

    for idx, (entry_name, info) in enumerate(
        sorted(meta["entries"].items(), key=lambda x: x[1].get("title", "")), 1
    ):
        title = info["title"]
        file = info["file"]
        summary = info.get("summary", {})

        row = [str(idx)]
        for key, _ in columns.items():
            value = summary.get(key, "-")
            if isinstance(value, list):
                value = ", ".join(value) or "-"
            max_len = max_lengths.get(key)
            if max_len and isinstance(value, str) and len(value) > max_len:
                value = value[:max_len - 3] + "..."
            row.append(str(value))
        row.append(f"[{title}]({file})")

        lines.append("| " + " | ".join(row) + " |")

    lines.append("")
    index_path = os.path.join(dest_dir, "index.md")
    atomic_write_text(index_path, "\n".join(lines))
    print(f"  索引已生成: {index_path}")


# ── 命令实现 ─────────────────────────────────────────────

def cmd_archive(args):
    """执行归档命令"""
    platform = args.platform or infer_platform(args.project)
    doc_type = args.type

    source_dir = get_source_dir(args.project, doc_type)
    dest_dir = get_dest_dir(platform, doc_type)

    if not os.path.isdir(source_dir):
        print(f"源目录不存在: {source_dir}")
        sys.exit(1)

    entries = list_entries(source_dir)
    if not entries:
        print(f"未找到任何 {doc_type} 条目")
        return

    meta = load_meta(dest_dir)
    dt_config = get_doc_type_config(doc_type)

    # 筛选要归档的条目
    if args.name:
        target_entries = [e for e in entries if args.name in e["name"]
                          or args.name in extract_title(e["name"])]
        if not target_entries:
            print(f"未找到匹配 '{args.name}' 的条目")
            print(f"可用条目:")
            for e in entries:
                print(f"  - {e['name']}")
            sys.exit(1)
    elif args.incremental:
        target_entries = []
        for e in entries:
            existing = meta["entries"].get(e["name"])
            if not existing:
                target_entries.append(e)
            else:
                current_hash = compute_content_hash(e)
                if existing.get("hash") != current_hash:
                    target_entries.append(e)
        if not target_entries:
            print("所有条目已是最新，无需归档")
            return
    else:
        target_entries = entries

    # 执行归档
    print(f"平台: {platform}")
    print(f"类型: {doc_type}")
    print(f"目标: {dest_dir}")
    print(f"待归档: {len(target_entries)} 条")
    print()

    archived_new = 0
    archived_updated = 0
    for entry in target_entries:
        # 增量模式下复用已算过的 hash
        content_hash = None
        if args.incremental:
            content_hash = compute_content_hash(entry)
        result = archive_entry(entry, dest_dir, meta, platform, content_hash, dt_config)
        if result:
            title, filename, is_new = result
            if is_new:
                archived_new += 1
                status = "新增"
            else:
                archived_updated += 1
                status = "更新"
            print(f"  [{status}] {title}")
        else:
            print(f"  [跳过] {entry['name']} (无有效内容)")

    # 清理已删除的条目
    removed = cleanup_deleted_entries(meta, entries, dest_dir)
    for title in removed:
        print(f"  [清理] {title} (源文件已删除)")

    # 保存元数据（原子写入）
    save_meta(dest_dir, meta)

    # 生成索引（传入 meta 避免重复加载）
    generate_index(dest_dir, platform, doc_type, meta)

    # 自动更新向量索引
    if not getattr(args, "no_vector", False):
        if HAS_VECTOR_INDEXER:
            try:
                print()
                print("正在更新向量索引...")
                update_vector_index()
            except Exception as e:
                print(f"  向量索引更新失败（归档不受影响）: {e}")
        else:
            print()
            print("  提示: chromadb 未安装，跳过向量索引更新")

    print()
    print(f"完成: 新增 {archived_new}, 更新 {archived_updated}, 清理 {len(removed)}")


def cmd_verify(args):
    """校验知识库完整性"""
    platform = args.platform or infer_platform(args.project)
    doc_type = args.type
    cfg = get_doc_type_config(doc_type)
    dest_dir = get_dest_dir(platform, doc_type)
    source_dir = get_source_dir(args.project, doc_type) if args.project else None

    print(f"平台: {platform}")
    print(f"类型: {doc_type}")
    print()

    issues = []
    meta = load_meta(dest_dir)
    entries = meta.get("entries", {})

    # 检查 1: meta 中的条目是否有对应文件
    for name, info in entries.items():
        filepath = os.path.join(dest_dir, info.get("file", ""))
        if not os.path.isfile(filepath):
            issues.append(f"  [缺失文件] {info.get('title', name)} -> {info.get('file', '?')}")

    # 检查 2: 目录中的 md 文件是否有 meta 记录
    if os.path.isdir(dest_dir):
        meta_files = {info.get("file") for info in entries.values()}
        for f in os.listdir(dest_dir):
            if f.endswith(".md") and f != "index.md" and f not in meta_files:
                issues.append(f"  [孤儿文件] {f} (无 meta 记录)")

    # 检查 3: 源目录已删除的条目
    if source_dir and os.path.isdir(source_dir):
        source_names = {e["name"] for e in list_entries(source_dir)}
        for name in entries:
            if name not in source_names:
                issues.append(f"  [源已删除] {entries[name].get('title', name)}")

    # 检查 4: 向量索引一致性（仅 bug 类型）
    if doc_type == "bug":
        try:
            from embed_indexer import COLLECTION_NAME
            from common import get_vector_client, get_embedding_function
            client = get_vector_client()
            collection = client.get_collection(COLLECTION_NAME, embedding_function=get_embedding_function())
            indexed_ids = set(collection.get()["ids"])
            for name, info in entries.items():
                doc_id = f"{platform}/{info.get('file', '')}"
                if doc_id not in indexed_ids and info.get("summary"):
                    issues.append(f"  [未索引] {info.get('title', name)}")
        except Exception:
            pass  # chromadb 不可用时跳过

    if issues:
        print(f"发现 {len(issues)} 个问题:")
        for issue in issues:
            print(issue)
    else:
        print("[OK] 完整性校验通过")


def cmd_status(args):
    """查看归档状态"""
    platform = args.platform or infer_platform(args.project)
    cfg = load_config()

    print(f"平台: {platform}")
    print(f"知识库: {KNOWLEDGE_ROOT}")
    print()

    for doc_type, dt_cfg in cfg.get("doc_types", {}).items():
        dest_dir = get_dest_dir(platform, doc_type)
        source_dir = get_source_dir(args.project, doc_type) if args.project else None

        source_count = len(list_entries(source_dir)) if source_dir and os.path.isdir(source_dir) else 0
        meta = load_meta(dest_dir)
        archived_count = len(meta.get("entries", {}))

        status = "+" if archived_count > 0 else "-"
        print(f"  [{status}] {dt_cfg.get('dest_dir', doc_type)}: 已归档 {archived_count} 条" +
              (f", 源目录 {source_count} 条" if source_dir else ""))


# ── CLI 入口 ─────────────────────────────────────────────

def main():
    cfg = load_config()
    doc_type_choices = list(cfg.get("doc_types", {}).keys())

    parser = argparse.ArgumentParser(description="知识库归档工具")
    subparsers = parser.add_subparsers(dest="command", help="命令")

    # archive 命令
    archive_parser = subparsers.add_parser("archive", help="归档文档")
    archive_parser.add_argument("--project", required=True, help="项目根目录路径")
    archive_parser.add_argument("--platform", help="平台名（默认从项目路径推断）")
    archive_parser.add_argument("--type", required=True, choices=doc_type_choices,
                                help="文档类型")
    archive_parser.add_argument("--name", help="指定条目名称（支持部分匹配）")
    archive_parser.add_argument("--all", action="store_true", help="归档所有条目")
    archive_parser.add_argument("--incremental", action="store_true",
                                help="仅归档新增或更新的条目")
    archive_parser.add_argument("--no-vector", action="store_true", dest="no_vector",
                                help="跳过自动更新向量索引")

    # verify 命令
    verify_parser = subparsers.add_parser("verify", help="校验知识库完整性")
    verify_parser.add_argument("--project", help="项目根目录路径")
    verify_parser.add_argument("--platform", help="平台名")
    verify_parser.add_argument("--type", required=True, choices=doc_type_choices,
                               help="文档类型")

    # index 命令
    index_parser = subparsers.add_parser("index", help="生成/更新索引")
    index_parser.add_argument("--project", help="项目根目录路径")
    index_parser.add_argument("--platform", help="平台名")
    index_parser.add_argument("--type", required=True, choices=doc_type_choices,
                              help="文档类型")

    # status 命令
    status_parser = subparsers.add_parser("status", help="查看归档状态")
    status_parser.add_argument("--project", help="项目根目录路径")
    status_parser.add_argument("--platform", help="平台名")

    args = parser.parse_args()

    if args.command == "archive":
        if not args.name and not args.all and not args.incremental:
            print("请指定 --name, --all 或 --incremental")
            archive_parser.print_help()
            sys.exit(1)
        cmd_archive(args)
    elif args.command == "verify":
        platform = args.platform or (infer_platform(args.project) if args.project else None)
        if not platform:
            print("请指定 --platform 或 --project")
            sys.exit(1)
        args.platform = platform
        cmd_verify(args)
    elif args.command == "index":
        platform = args.platform or (infer_platform(args.project) if args.project else None)
        if not platform:
            print("请指定 --platform 或 --project")
            sys.exit(1)
        dest_dir = get_dest_dir(platform, args.type)
        generate_index(dest_dir, platform, args.type)
    elif args.command == "status":
        cmd_status(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
