#!/usr/bin/env python3
"""
知识库脚本共享模块 - 常量、配置、工具函数。
"""

import json
import os
import re
import tempfile

# ── 路径常量 ──────────────────────────────────────────────

KNOWLEDGE_ROOT = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    ".agents", "knowledge"
)
VECTOR_DB_PATH = os.path.join(KNOWLEDGE_ROOT, "vector_db")
CONFIG_FILE = os.path.join(KNOWLEDGE_ROOT, "knowledge_config.json")
META_FILE = ".archive_meta.json"

# ── 默认配置 ──────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "version": 3,
    "doc_types": {
        "bug": {
            "source_dir": "bug",
            "dest_dir": "bug-solutions",
            "index_columns": {
                "module": "模块",
                "symptoms": "症状关键词",
                "root_cause": "根因方向"
            },
            "max_field_lengths": {"root_cause": 60},
            "summary_section": "结构化摘要",
            "summary_fields": {
                "平台": "platform", "模块": "module", "问题分类": "bug_type",
                "症状关键词": "symptoms", "根因概述": "root_cause",
                "调用链摘要": "call_chain_summary", "检索关键词": "keywords",
            },
            "list_fields": ["symptoms", "keywords"],
        },
        "requirement": {
            "source_dir": "requirement",
            "dest_dir": "requirement-solutions",
            "index_columns": {"module": "模块", "summary": "摘要"},
            "max_field_lengths": {"summary": 80},
            "summary_section": "结构化摘要",
            "summary_fields": {"模块": "module", "需求描述": "summary", "优先级": "priority"},
            "list_fields": [],
        }
    },
    "collections": {
        "bug-solutions": {
            "strategy": "summary",
            "sources": ["platform/*/bug-solutions/.archive_meta.json"]
        },
        "requirement-solutions": {
            "strategy": "summary",
            "sources": ["platform/*/requirement-solutions/.archive_meta.json"]
        },
        "code-summary": {
            "strategy": "markdown_chunks",
            "sources": ["platform/*/code-summary/**/*.md"],
            "chunk_size": 1200,
            "chunk_overlap": 200
        },
        "official-docs-md": {
            "strategy": "markdown_chunks",
            "sources": ["platform/*/official-docs-md/**/*.md"],
            "chunk_size": 1500,
            "chunk_overlap": 200
        },
        "project-overview": {
            "strategy": "markdown_chunks",
            "sources": ["platform/*/项目概览.md"],
            "chunk_size": 1000,
            "chunk_overlap": 100
        },
        "protocols": {
            "strategy": "markdown_chunks",
            "sources": ["protocols/**/*.md"],
            "chunk_size": 1500,
            "chunk_overlap": 200
        }
    }
}

_config_cache = None


def load_config():
    """加载配置，文件不存在时使用默认值。"""
    global _config_cache
    if _config_cache is not None:
        return _config_cache
    if os.path.isfile(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            _config_cache = json.load(f)
    else:
        _config_cache = _DEFAULT_CONFIG
    return _config_cache


def get_doc_type_config(doc_type):
    """获取指定文档类型的配置，不存在则报错退出。"""
    import sys
    cfg = load_config()
    dtypes = cfg.get("doc_types", {})
    if doc_type not in dtypes:
        print(f"错误: 不支持的文档类型 '{doc_type}'，支持: {list(dtypes.keys())}")
        sys.exit(1)
    return dtypes[doc_type]


def get_collection_config(collection_name):
    """获取指定 collection 的配置，不存在则报错退出。"""
    import sys
    cfg = load_config()
    cols = cfg.get("collections", {})
    if collection_name not in cols:
        print(f"错误: 不支持的 collection '{collection_name}'，支持: {list(cols.keys())}")
        sys.exit(1)
    return cols[collection_name]


def list_collections():
    """返回所有配置的 collection 名称。"""
    cfg = load_config()
    return list(cfg.get("collections", {}).keys())


# ── ChromaDB 延迟加载 ────────────────────────────────────

_ef_instance = None
_chromadb_module = None


def get_embedding_function():
    """延迟加载 embedding 函数（首次调用约 2-3s）。"""
    global _ef_instance
    if _ef_instance is None:
        from chromadb.utils import embedding_functions
        _ef_instance = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _ef_instance


def get_chromadb():
    """延迟加载 chromadb 模块。"""
    global _chromadb_module
    if _chromadb_module is None:
        import chromadb
        _chromadb_module = chromadb
    return _chromadb_module


def get_vector_client():
    """获取 ChromaDB 持久化客户端。"""
    os.makedirs(VECTOR_DB_PATH, exist_ok=True)
    return get_chromadb().PersistentClient(path=VECTOR_DB_PATH)


def get_collection(collection_name):
    """获取指定 collection 实例。"""
    client = get_vector_client()
    return client.get_or_create_collection(
        name=collection_name,
        embedding_function=get_embedding_function(),
        metadata={"description": f"{collection_name} 向量索引"}
    )


# ── 路径工具 ──────────────────────────────────────────────

def infer_platform(project_path):
    """从项目路径推断平台名。D:/EC626 -> EC626"""
    return os.path.basename(os.path.normpath(project_path))


def get_dest_dir(platform, doc_type):
    """获取目标目录路径。"""
    cfg = get_doc_type_config(doc_type)
    return os.path.join(KNOWLEDGE_ROOT, "platform", platform, cfg["dest_dir"])


def get_source_dir(project_path, doc_type):
    """获取源目录路径。"""
    cfg = get_doc_type_config(doc_type)
    return os.path.join(project_path, ".spec", cfg["source_dir"])


# ── 元数据操作 ────────────────────────────────────────────

def load_meta(dest_dir):
    """加载归档元数据，文件不存在时返回空结构。"""
    return load_json(os.path.join(dest_dir, META_FILE), {"entries": {}})


def save_meta(dest_dir, meta):
    """保存归档元数据。"""
    save_json(os.path.join(dest_dir, META_FILE), meta)


# ── 文件名工具 ────────────────────────────────────────────

def safe_filename(title, platform=None):
    """生成安全文件名，可选加平台前缀防冲突。"""
    safe = re.sub(r'[<>:"/\\|?*]', '_', title)
    if platform:
        return f"{platform}_{safe}.md"
    return f"{safe}.md"


def extract_title(entry_name):
    """从条目名提取可读标题。4975090277_COAP死机 -> COAP死机"""
    title = re.sub(r"^\d+_", "", entry_name)
    return title if title else entry_name


# ── Summary 文本构建（供向量索引用）─────────────────────────


def build_summary_text(summary, doc_type_config=None):
    """将结构化摘要组合为可检索的文本。
    doc_type_config: 来自配置的 doc_type 对象，用于读取 summary_fields。
    为 None 时从 summary 的所有 key 构建（兜底）。
    """
    if not summary:
        return ""

    if doc_type_config and "summary_fields" in doc_type_config:
        field_map = doc_type_config["summary_fields"]
    else:
        field_map = {v: v for v in summary.keys()}

    parts = []
    for label, key in field_map.items():
        value = summary.get(key)
        if not value:
            continue
        if isinstance(value, list):
            value = ", ".join(value)
        parts.append(f"{label}: {value}")
    return " ".join(parts)


# ── Markdown 分块（供长文档向量化）──────────────────────────


def _split_by_headings(content):
    """按 Markdown 标题拆分成 (标题, 内容) 段。"""
    lines = content.split("\n")
    sections = []
    current_title = ""
    current_lines = []

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)", line)
        if heading_match:
            if current_lines:
                sections.append((current_title, "\n".join(current_lines).strip()))
            current_title = heading_match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append((current_title, "\n".join(current_lines).strip()))

    # 如果文档没有标题，整体作为一段
    if not sections and content.strip():
        sections.append(("", content.strip()))

    return sections


def chunk_markdown(content, chunk_size=1500, chunk_overlap=200):
    """将 Markdown 内容分块。
    返回 [(chunk_text, section_title)] 列表。
    """
    sections = _split_by_headings(content)
    chunks = []
    current_chunk = []
    current_titles = []
    current_size = 0

    def flush_chunk():
        if not current_chunk:
            return
        text = "\n\n".join(current_chunk)
        title = current_titles[0] if current_titles else ""
        chunks.append((text, title))

    for title, body in sections:
        section_text = f"## {title}\n\n{body}" if title else body
        section_size = len(section_text)

        # 单个节超过 chunk_size，需要再切分
        if section_size > chunk_size and current_chunk:
            flush_chunk()
            current_chunk = []
            current_titles = []
            current_size = 0

        if section_size > chunk_size:
            # 按段落切大节
            paragraphs = section_text.split("\n\n")
            buffer = []
            buffer_size = 0
            for para in paragraphs:
                para_size = len(para)
                if buffer and buffer_size + para_size > chunk_size:
                    chunks.append(("\n\n".join(buffer), title))
                    # 保留 overlap
                    overlap_buffer = []
                    overlap_size = 0
                    for p in reversed(buffer):
                        if overlap_size + len(p) > chunk_overlap:
                            break
                        overlap_buffer.insert(0, p)
                        overlap_size += len(p)
                    buffer = overlap_buffer + [para]
                    buffer_size = sum(len(p) for p in buffer)
                else:
                    buffer.append(para)
                    buffer_size += para_size
            if buffer:
                chunks.append(("\n\n".join(buffer), title))
        else:
            # 普通节，累积到当前 chunk
            if current_chunk and current_size + section_size > chunk_size:
                flush_chunk()
                # overlap：保留上一个 chunk 的最后一段
                overlap_chunk = []
                overlap_size = 0
                for item in reversed(current_chunk):
                    if overlap_size + len(item) > chunk_overlap:
                        break
                    overlap_chunk.insert(0, item)
                    overlap_size += len(item)
                current_chunk = overlap_chunk
                current_titles = current_titles[-1:] if current_titles else []
                current_size = sum(len(c) for c in current_chunk)

            current_chunk.append(section_text)
            if title and title not in current_titles:
                current_titles.append(title)
            current_size += section_size

    flush_chunk()
    return chunks


# ── 文件匹配工具 ──────────────────────────────────────────


def glob_files(patterns, root_dir):
    """根据 glob 模式列表，从 root_dir 递归匹配文件。
    支持 ** 通配（递归匹配任意层目录）。"""
    import glob as _glob
    if isinstance(patterns, str):
        patterns = [patterns]

    matched = set()
    for pattern in patterns:
        full_pattern = os.path.join(root_dir, pattern)
        for filepath in _glob.glob(full_pattern, recursive=True):
            if os.path.isfile(filepath):
                matched.add(filepath)
    return sorted(matched)


# ── 通用文件操作 ──────────────────────────────────────────

def atomic_write_text(filepath, content):
    """原子写入文本文件：先写临时文件再 rename，防止中断导致损坏。"""
    dir_name = os.path.dirname(filepath)
    os.makedirs(dir_name, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(suffix=".tmp", dir=dir_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp_path, filepath)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_json(filepath, data):
    """原子写入 JSON 文件。"""
    atomic_write_text(filepath, json.dumps(data, ensure_ascii=False, indent=2))


def load_json(filepath, default=None):
    """加载 JSON 文件，不存在时返回 default。"""
    if not os.path.isfile(filepath):
        return default if default is not None else {}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(filepath, data):
    """保存 JSON 文件（原子写入）。"""
    atomic_write_json(filepath, data)
