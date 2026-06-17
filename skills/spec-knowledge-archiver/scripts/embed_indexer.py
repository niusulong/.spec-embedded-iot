#!/usr/bin/env python3
"""
知识库向量索引构建工具。

用法:
  # 构建全量索引（所有 collection）
  python embed_indexer.py build

  # 构建指定 collection
  python embed_indexer.py build --collection protocols

  # 增量更新
  python embed_indexer.py update

  # 查看索引状态
  python embed_indexer.py status
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from common import (
    KNOWLEDGE_ROOT, META_FILE,
    load_config, get_doc_type_config, get_collection_config, list_collections,
    get_vector_client, get_embedding_function,
    build_summary_text, chunk_markdown, glob_files, load_json,
)


def make_doc_id(platform, filename):
    """摘要文档 ID: {platform}/{filename}"""
    return f"{platform}/{filename}"


# ── 扫描平台（仅 summary 策略需要）─────────────────────────

def scan_platforms():
    """扫描所有平台目录。"""
    platform_dir = os.path.join(KNOWLEDGE_ROOT, "platform")
    if not os.path.isdir(platform_dir):
        return []
    return [d for d in os.listdir(platform_dir)
            if os.path.isdir(os.path.join(platform_dir, d))]


# ── summary 策略：从 .archive_meta.json 读取 ───────────────

def _collect_summary_entries(platform, collection_name, source_patterns):
    """从 meta 文件读取 summary 条目，返回 (ids, docs, metas)。

    每个条目产出：
      - 1 条「摘要向量」(doc_kind="summary")：来自结构化摘要字段，提精度 + 保结构化过滤。
        ID = {plat}/{file}，与旧索引兼容。
    若 collection 配置了 index_body=true，额外产出：
      - N 条「正文块向量」(doc_kind="body")：读取同目录 .md 正文分块，保召回，
        让正文里的日志片段/寄存器值/AT 命令等细节也可语义检索；摘要为空时仍可检索。
        ID = {plat}/{file}/chunk_{n}。

    既无摘要又未索引到正文（index_body=false 或 .md 缺失）→ 警告并跳过。
    """
    cfg = load_config()
    doc_types = cfg.get("doc_types", {})

    # 根据 collection_name 找到对应的 doc_type
    dt_cfg = None
    for dt_name, dt in doc_types.items():
        if dt.get("dest_dir") == collection_name:
            dt_cfg = dt
            break

    col_cfg = get_collection_config(collection_name)
    index_body = col_cfg.get("index_body", False)
    chunk_size = col_cfg.get("chunk_size", 1200)
    chunk_overlap = col_cfg.get("chunk_overlap", 200)

    ids, documents, metadatas = [], [], []

    # source_patterns 形如 ["platform/*/bug-solutions/.archive_meta.json"]
    meta_files = glob_files(source_patterns, KNOWLEDGE_ROOT)
    for meta_path in meta_files:
        meta = load_json(meta_path, {"entries": {}})
        dest_dir = os.path.dirname(meta_path)
        # 从路径推断平台
        rel = os.path.relpath(meta_path, KNOWLEDGE_ROOT)
        parts = rel.split(os.sep)
        plat = parts[1] if len(parts) > 1 else platform

        for entry_name, info in meta.get("entries", {}).items():
            summary = info.get("summary", {})
            filename = info.get("file", "")
            # 向量 id 以 entry_name 为基础（稳定），与文件名解耦：
            # 文件名迁移（safe_filename 加平台前缀）不会造成 id 漂移 / 僵尸向量
            doc_id = make_doc_id(plat, entry_name)

            # 基础 metadata（摘要中的字符串字段也写入，便于结构化过滤；
            # 列表型字段如 keywords/symptoms 不写 metadata，只进摘要文本）
            base_meta = {
                "platform": plat,
                "title": info.get("title", ""),
                "file": filename,
                "collection": collection_name,
            }
            for key, value in summary.items():
                if isinstance(value, str) and value:
                    base_meta[key] = value[:200]

            # 1) 摘要向量（提精度 + 保结构化过滤）
            summary_text = build_summary_text(summary, dt_cfg)
            if summary_text.strip():
                ids.append(doc_id)
                documents.append(summary_text)
                metadatas.append({**base_meta, "doc_kind": "summary"})

            # 2) 正文分块向量（保召回）
            body_chunks = 0
            if index_body and filename:
                md_path = os.path.join(dest_dir, filename)
                content = ""
                if os.path.isfile(md_path):
                    try:
                        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                    except Exception:
                        content = ""
                if content.strip():
                    chunks = chunk_markdown(content, chunk_size, chunk_overlap)
                    body_chunks = len(chunks)
                    for idx, (chunk_text, section_title) in enumerate(chunks, 1):
                        ids.append(f"{doc_id}/chunk_{idx}")
                        documents.append(chunk_text)
                        metadatas.append({
                            **base_meta,
                            "doc_kind": "body",
                            "section_title": section_title,
                        })

            # 既无摘要又无正文兜底 → 警告并跳过
            if not summary_text.strip() and body_chunks == 0:
                print(f"  警告: {plat}/{filename} 无结构化摘要且未索引到正文，跳过该条目")

    return ids, documents, metadatas


# ── markdown_chunks 策略：分块索引 .md 文件 ───────────────

def _collect_markdown_chunks(collection_name, source_patterns, chunk_size=1500, chunk_overlap=200):
    """遍历 .md 文件并分块，返回 (ids, docs, metas)。"""
    md_files = glob_files(source_patterns, KNOWLEDGE_ROOT)
    ids, documents, metadatas = [], [], []

    for md_path in md_files:
        rel_path = os.path.relpath(md_path, KNOWLEDGE_ROOT)
        parts = rel_path.split(os.sep)

        # 推断平台和子路径
        platform = ""
        module = ""
        if len(parts) > 1 and parts[0] == "platform":
            platform = parts[1]
            sub_path = os.sep.join(parts[2:]) if len(parts) > 2 else os.path.basename(rel_path)
            # code-summary/{模块}/代码总结.md → 提取模块名
            if len(parts) >= 4 and parts[2] == "code-summary":
                module = parts[3]
        elif parts[0] == "protocols":
            platform = "protocols"
            sub_path = os.sep.join(parts[1:]) if len(parts) > 1 else os.path.basename(rel_path)
            # protocols/{协议}/xxx.md → 提取协议名作为模块
            if len(parts) >= 2:
                module = parts[1]
        else:
            sub_path = rel_path

        try:
            with open(md_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            continue

        if not content.strip():
            continue

        chunks = chunk_markdown(content, chunk_size, chunk_overlap)
        for idx, (chunk_text, section_title) in enumerate(chunks, 1):
            doc_id = f"{collection_name}/{rel_path}/chunk_{idx}"
            ids.append(doc_id)
            documents.append(chunk_text)
            metadatas.append({
                "platform": platform,
                "module": module,
                "file": sub_path,
                "rel_path": rel_path,
                "section_title": section_title,
                "collection": collection_name,
            })

    return ids, documents, metadatas


def _collect_collection(collection_name):
    """根据配置收集指定 collection 的全部条目。"""
    col_cfg = get_collection_config(collection_name)
    strategy = col_cfg.get("strategy", "markdown_chunks")
    sources = col_cfg.get("sources", [])

    if strategy == "summary":
        return _collect_summary_entries(None, collection_name, sources)
    elif strategy == "markdown_chunks":
        return _collect_markdown_chunks(
            collection_name,
            sources,
            chunk_size=col_cfg.get("chunk_size", 1500),
            chunk_overlap=col_cfg.get("chunk_overlap", 200),
        )
    else:
        print(f"  未知策略 '{strategy}'，跳过 {collection_name}")
        return [], [], []


# ── 索引主流程 ───────────────────────────────────────────

def index_all(collection_name=None):
    """构建全量索引。"""
    collections = [collection_name] if collection_name else list_collections()
    client = get_vector_client()
    ef = get_embedding_function()
    grand_total = 0

    for col_name in collections:
        print(f"\n[{col_name}] 构建全量索引...")

        # 删除旧集合并重建
        try:
            client.delete_collection(col_name)
        except Exception:
            pass

        collection = client.get_or_create_collection(
            name=col_name,
            embedding_function=ef,
            metadata={"description": f"{col_name} 向量索引", "hnsw:space": "cosine"}
        )

        ids, documents, metadatas = _collect_collection(col_name)
        if ids:
            # 批量 upsert，每批 100 条避免内存过大
            batch_size = 100
            for i in range(0, len(ids), batch_size):
                collection.upsert(
                    ids=ids[i:i+batch_size],
                    documents=documents[i:i+batch_size],
                    metadatas=metadatas[i:i+batch_size]
                )
            print(f"  完成: {len(ids)} 条")
            grand_total += len(ids)
        else:
            print(f"  完成: 0 条")

    print(f"\n全量索引完成: 共 {grand_total} 条")
    print(f"向量数据库: {os.path.join(KNOWLEDGE_ROOT, 'vector_db')}")


def update_index(collection_name=None):
    """增量更新索引：批量 upsert + 自动清理孤儿条目。"""
    collections = [collection_name] if collection_name else list_collections()
    client = get_vector_client()
    ef = get_embedding_function()

    for col_name in collections:
        print(f"\n[{col_name}] 增量更新...")

        collection = client.get_or_create_collection(
            name=col_name,
            embedding_function=ef,
            metadata={"description": f"{col_name} 向量索引", "hnsw:space": "cosine"}
        )

        ids, documents, metadatas = _collect_collection(col_name)
        existing_ids = set(collection.get()["ids"]) if collection.count() > 0 else set()

        # 批量 upsert
        if ids:
            new_ids = [i for i in ids if i not in existing_ids]
            updated_ids = [i for i in ids if i in existing_ids]
            batch_size = 100
            for i in range(0, len(ids), batch_size):
                collection.upsert(
                    ids=ids[i:i+batch_size],
                    documents=documents[i:i+batch_size],
                    metadatas=metadatas[i:i+batch_size]
                )
        else:
            new_ids = []
            updated_ids = []

        # 清理孤儿
        orphan_ids = existing_ids - set(ids)
        if orphan_ids:
            collection.delete(ids=list(orphan_ids))

        print(f"  完成: 新增 {len(new_ids)}, 更新 {len(updated_ids)}, 清理孤儿 {len(orphan_ids)}")


def show_status():
    """显示索引状态"""
    cfg = load_config()
    client = get_vector_client()

    print(f"向量数据库: {os.path.join(KNOWLEDGE_ROOT, 'vector_db')}")
    print()

    for col_name in list_collections():
        try:
            collection = client.get_collection(col_name, embedding_function=get_embedding_function())
            count = collection.count()

            if count > 0:
                all_data = collection.get(include=["metadatas"])
                platform_counts = {}
                for meta in all_data["metadatas"]:
                    p = meta.get("platform", "unknown")
                    platform_counts[p] = platform_counts.get(p, 0) + 1

                print(f"  [{col_name}]: {count} 条")
                for p, c in sorted(platform_counts.items()):
                    print(f"    {p}: {c} 条")
            else:
                print(f"  [{col_name}]: 空")
        except Exception:
            print(f"  [{col_name}]: 不存在")


# ── CLI 入口 ─────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="知识库向量索引工具")
    subparsers = parser.add_subparsers(dest="command")

    collection_choices = list_collections()

    build_parser = subparsers.add_parser("build", help="构建全量索引")
    build_parser.add_argument("--collection", choices=collection_choices,
                              help="collection 名称（默认全部）")

    update_parser = subparsers.add_parser("update", help="增量更新索引")
    update_parser.add_argument("--collection", choices=collection_choices,
                               help="collection 名称（默认全部）")

    subparsers.add_parser("status", help="查看索引状态")

    args = parser.parse_args()

    if args.command == "build":
        index_all(args.collection)
    elif args.command == "update":
        update_index(args.collection)
    elif args.command == "status":
        show_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
