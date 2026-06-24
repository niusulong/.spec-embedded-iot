#!/usr/bin/env python3
"""
知识库脚本共享模块 - 常量、配置、工具函数。
"""

import contextlib
import copy
import json
import os
import re
import sys
import tempfile
import threading

# ── 路径常量 ──────────────────────────────────────────────

KNOWLEDGE_ROOT = os.path.join(
    os.environ.get("USERPROFILE", os.path.expanduser("~")),
    ".spec-embedded-iot", "knowledge"
)
VECTOR_DB_PATH = os.path.join(KNOWLEDGE_ROOT, "vector_db")
CONFIG_FILE = os.path.join(KNOWLEDGE_ROOT, "knowledge_config.json")
META_FILE = ".archive_meta.json"

# 归档元数据 .archive_meta.json 的 schema 版本（演进时递增，load_meta 检查兼容性）
META_SCHEMA_VERSION = 1

# 「结构化摘要」节标题匹配：允许前导序号（"0."/"0 "）和尾部内容（"（专项）"）。
# merge 选主文档与 ensure 注入字段共用此正则，避免两处写法漂移。
SUMMARY_HEADING_RE = re.compile(r"^##\s*\d*[\.\s]*结构化摘要", re.MULTILINE)

# ── 默认配置 ──────────────────────────────────────────────

_DEFAULT_CONFIG = {
    "version": 3,
    "doc_types": {
        "bug": {
            "source_dir": "bug",
            "dest_dir": "bug-solutions",
            "index_columns": {
                "work_item_id": "工作项ID",
                "module": "模块",
                "symptoms": "症状关键词",
                "root_cause": "根因方向"
            },
            "max_field_lengths": {"root_cause": 60},
            "summary_section": "结构化摘要",
            "summary_fields": {
                "工作项 ID": "work_item_id",
                "平台": "platform", "模块": "module", "问题分类": "bug_type",
                "症状关键词": "symptoms", "根因概述": "root_cause",
                "调用链摘要": "call_chain_summary", "检索关键词": "keywords",
            },
            "list_fields": ["symptoms", "keywords"],
        },
        "requirement": {
            "source_dir": "requirement",
            "dest_dir": "requirement-solutions",
            "index_columns": {"work_item_id": "项目ID", "module": "模块", "summary": "摘要"},
            "max_field_lengths": {"summary": 80},
            "summary_section": "结构化摘要",
            "summary_fields": {"项目 ID": "work_item_id", "模块": "module", "需求描述": "summary", "优先级": "priority"},
            "list_fields": [],
        }
    },
    "collections": {
        "bug-solutions": {
            "strategy": "summary",
            "sources": ["platform/*/bug-solutions/.archive_meta.json"],
            "index_body": True,
            "chunk_size": 1200,
            "chunk_overlap": 200,
        },
        "requirement-solutions": {
            "strategy": "summary",
            "sources": ["platform/*/requirement-solutions/.archive_meta.json"],
            "index_body": True,
            "chunk_size": 1200,
            "chunk_overlap": 200,
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


def _deep_merge(base, override):
    """递归合并：override 覆盖 base 同名键，dict 递归合并，其余直接覆盖。"""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def _validate_config(cfg):
    """校验配置结构，发现问题打印 [config] 警告到 stderr（不阻断，避免误伤）。"""
    import sys
    doc_types = cfg.get("doc_types", {})
    collections = cfg.get("collections", {})
    if not doc_types:
        print("[config] 警告: doc_types 为空，归档将无法工作", file=sys.stderr)
    if not collections:
        print("[config] 警告: collections 为空，向量索引/检索将无法工作", file=sys.stderr)
    # summary 策略的 collection 必须能反查到一个 doc_type（dest_dir == collection 名），
    # 否则 _collect_summary_entries 拿不到 dt_cfg，摘要向量静默降级。
    dest_to_dtype = {dt.get("dest_dir"): name for name, dt in doc_types.items()}
    for col_name, col in collections.items():
        if col.get("strategy") == "summary" and col_name not in dest_to_dtype:
            print(f"[config] 警告: collection '{col_name}' 为 summary 策略但无对应 "
                  f"doc_type（dest_dir=='{col_name}'），摘要向量将降级", file=sys.stderr)
    # 版本检查（目前仅提示，迁移逻辑见 Tier 3）
    ver = cfg.get("version")
    if ver is not None and ver != _DEFAULT_CONFIG.get("version"):
        print(f"[config] 提示: 配置 version={ver}，默认={_DEFAULT_CONFIG.get('version')}，"
              f"注意兼容性", file=sys.stderr)


def load_config():
    """加载配置：用户配置深度合并到默认值之上（缺键回退默认，防静默瘫痪）。
    返回深拷贝，避免外部修改污染缓存。"""
    global _config_cache
    if _config_cache is None:
        if os.path.isfile(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                try:
                    user_cfg = json.load(f)
                except (json.JSONDecodeError, ValueError) as e:
                    print(f"[config] 警告: 配置文件解析失败 ({e})，回退默认配置", file=sys.stderr)
                    user_cfg = {}
            _config_cache = _deep_merge(_DEFAULT_CONFIG, user_cfg)
        else:
            _config_cache = copy.deepcopy(_DEFAULT_CONFIG)
        _validate_config(_config_cache)
    return copy.deepcopy(_config_cache)


def reload_config():
    """清除配置缓存并重新加载（测试/外部更新配置后调用）。"""
    global _config_cache
    _config_cache = None
    return load_config()


def get_doc_type_config(doc_type):
    """获取指定文档类型的配置，不存在则抛 ValueError（由 CLI 边界处理，便于作为库复用）。"""
    cfg = load_config()
    dtypes = cfg.get("doc_types", {})
    if doc_type not in dtypes:
        raise ValueError(f"不支持的文档类型 '{doc_type}'，支持: {list(dtypes.keys())}")
    return dtypes[doc_type]


def get_collection_config(collection_name):
    """获取指定 collection 的配置，不存在则抛 ValueError（由 CLI 边界处理，便于作为库复用）。"""
    cfg = load_config()
    cols = cfg.get("collections", {})
    if collection_name not in cols:
        raise ValueError(f"不支持的 collection '{collection_name}'，支持: {list(cols.keys())}")
    return cols[collection_name]


def list_collections():
    """返回所有配置的 collection 名称。"""
    cfg = load_config()
    return list(cfg.get("collections", {}).keys())


# ── ChromaDB 延迟加载 ────────────────────────────────────

_ef_instance = None
_ef_lock = threading.Lock()
_chromadb_module = None


def get_embedding_function():
    """延迟加载 embedding 函数（首次调用约 2-3s）。双检锁保证并发安全。"""
    global _ef_instance
    if _ef_instance is None:
        with _ef_lock:
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
        metadata={"description": f"{collection_name} 向量索引", "hnsw:space": "cosine"}
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
    """加载归档元数据，文件不存在时返回空结构。检查 schema_version 兼容性。"""
    meta = load_json(os.path.join(dest_dir, META_FILE), {"entries": {}})
    ver = meta.get("schema_version", 1)
    if ver > META_SCHEMA_VERSION:
        print(f"[meta] 警告: {dest_dir} 的 schema_version={ver} 高于当前支持 "
              f"{META_SCHEMA_VERSION}，可能需要升级工具", file=sys.stderr)
    return meta


def save_meta(dest_dir, meta):
    """保存归档元数据（标记当前 schema_version）。"""
    meta["schema_version"] = META_SCHEMA_VERSION
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


def extract_work_item_id(entry_name):
    """从条目名提取工作项ID（单号）。6977185133_TCP连接 -> 6977185133；无单号返回 None。"""
    match = re.match(r"^(\d+)_", entry_name)
    return match.group(1) if match else None


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
    if not content or not content.strip():
        return []
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
            # 按段落切大节（基于 body，避免 "## 标题" 被切成独立垃圾块）
            paragraphs = body.split("\n\n")
            # 标题附加到首段，提供上下文但不单独成块
            if title and paragraphs:
                paragraphs[0] = f"## {title}\n\n{paragraphs[0]}"
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

@contextlib.contextmanager
def exclusive_lock(lock_path):
    """跨平台独占文件锁（Windows msvcrt / Unix fcntl），无第三方依赖。
    用于防止并发归档导致 .archive_meta.json last-write-wins 丢条目。"""
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    f = open(lock_path, "w")
    try:
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)  # 阻塞至获取
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        f.close()  # 关闭即释放


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
