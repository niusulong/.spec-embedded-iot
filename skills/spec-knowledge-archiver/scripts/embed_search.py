#!/usr/bin/env python3
"""
知识库向量检索工具。

用法:
  # 默认跨所有 collection 搜索
  python embed_search.py "MQTT QoS"

  # 指定某个 collection
  python embed_search.py "MQTT QoS" --collection protocols

  # 限定平台
  python embed_search.py "MQTT 连接失败" --collection bug-solutions --platform EC626

  # 指定返回数量
  python embed_search.py "死机" --top 10

  # JSON 输出（供脚本调用）
  python embed_search.py "死机" --json
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from common import (
    load_config, get_collection_config, list_collections,
    get_vector_client, get_embedding_function,
)


def _query_collection(query, collection_name, platform=None, top=5):
    """查询单个 collection，返回标准化结果列表。"""
    client = get_vector_client()
    ef = get_embedding_function()

    try:
        collection = client.get_collection(name=collection_name, embedding_function=ef)
    except Exception:
        return []

    if collection.count() == 0:
        return []

    where_filter = {"platform": platform} if platform else None

    results = collection.query(
        query_texts=[query],
        n_results=min(top, collection.count()),
        where=where_filter,
        include=["documents", "metadatas", "distances"]
    )

    if not results["ids"] or not results["ids"][0]:
        return []

    output_items = []
    for i, doc_id in enumerate(results["ids"][0]):
        distance = results["distances"][0][i]
        similarity = round(1 - distance, 4)
        metadata = results["metadatas"][0][i]
        document = results["documents"][0][i]

        output_items.append({
            "id": doc_id,
            "collection": metadata.get("collection", collection_name),
            "similarity": similarity,
            "title": metadata.get("title", metadata.get("section_title", "")),
            "work_item_id": metadata.get("work_item_id", ""),
            "platform": metadata.get("platform", ""),
            "file": metadata.get("file", metadata.get("rel_path", "")),
            "module": metadata.get("module", ""),
            "bug_type": metadata.get("bug_type", ""),
            "root_cause": metadata.get("root_cause", ""),
            "summary_text": document,
        })

    return output_items


def search(query, collections=None, platform=None, top=5, output_json=False):
    """搜索知识库向量索引。默认跨所有 collection。"""
    if collections is None or len(collections) == 0:
        collections = list_collections()

    all_items = []
    for collection_name in collections:
        try:
            items = _query_collection(query, collection_name, platform, top)
            all_items.extend(items)
        except Exception as e:
            if not output_json:
                print(f"  [{collection_name}] 查询失败: {e}")

    # 全局按相似度排序
    all_items.sort(key=lambda x: x["similarity"], reverse=True)
    all_items = all_items[:top]

    if output_json:
        print(json.dumps(all_items, ensure_ascii=False, indent=2))
        return

    if not all_items:
        print("未找到匹配案例")
        return

    print(f"搜索: \"{query}\"")
    if platform:
        print(f"平台: {platform}")
    print(f"找到 {len(all_items)} 个相关结果:\n")

    for i, item in enumerate(all_items, 1):
        print(f"  [{i}] [{item['collection']}] {item['title'] or item['file']}")
        if item.get('work_item_id'):
            print(f"      单号: {item['work_item_id']}")
        if item['platform']:
            print(f"      平台: {item['platform']} | 模块: {item['module']} | 相似度: {item['similarity']}")
        if item['root_cause']:
            print(f"      根因: {item['root_cause'][:80]}")
        if item['file']:
            print(f"      文件: {item['file']}")
        print()


def main():
    # Windows 控制台默认 GBK，遇到中文/特殊字符会 UnicodeEncodeError；统一用 UTF-8 容错
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding='utf-8', errors='replace')
        except Exception:
            pass

    cfg = load_config()
    collection_choices = list_collections()

    # 兼容旧的 --type
    parser = argparse.ArgumentParser(description="知识库向量检索")
    parser.add_argument("query", help="搜索查询文本")
    parser.add_argument("--collection", choices=collection_choices,
                        help="限定 collection（默认搜索全部）")
    parser.add_argument("--type", choices=collection_choices,
                        help="同 --collection（兼容旧参数）")
    parser.add_argument("--platform", help="限定平台（如 EC626）")
    parser.add_argument("--top", type=int, default=5, help="返回结果数量（默认 5）")
    parser.add_argument("--json", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    collections = [args.collection or args.type] if (args.collection or args.type) else None
    search(args.query, collections=collections, platform=args.platform,
           top=args.top, output_json=args.json)


if __name__ == "__main__":
    main()
