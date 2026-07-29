"""YAML frontmatter 解析与补全。

Derived from lucasastorian/llmwiki (Apache-2.0).
Source: mcp/tools/write.py（frontmatter 相关函数）
Modifications for spec-embedded-iot:
  - 移除 FastMCP / VaultFS 依赖
  - 保留 _parse_frontmatter / _extract_metadata / _extract_frontmatter_tags /
    _ensure_wiki_frontmatter / _default_description / _is_footnote_suffix_line
  - 公开函数去掉前导下划线（改为 parse_frontmatter / ensure_wiki_frontmatter 等）
"""

import re
from datetime import date

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.+?\r?\n)---[ \t]*\r?\n", re.DOTALL)
_FOOTNOTE_DEF_RE = re.compile(r"^\[\^([^\]]+)\]:", re.MULTILINE)


def parse_frontmatter(content: str) -> dict:
    """提取 YAML frontmatter。无 frontmatter 或解析失败返回 {}。"""
    if not _HAS_YAML:
        return {}
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return {}
    try:
        meta = yaml.safe_load(m.group(1))
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def extract_metadata(meta: dict) -> tuple:
    """从 frontmatter 提取 (date_str, metadata_dict)。

    metadata_dict 含 description 等额外字段。始终返回 dict（可能为空），
    以便 frontmatter 变化时显式清空旧 metadata。
    """
    date_str = None
    if "date" in meta:
        d = meta["date"]
        date_str = d.isoformat() if hasattr(d, "isoformat") else str(d)

    metadata = {}
    if isinstance(meta.get("description"), str) and meta["description"].strip():
        metadata["description"] = meta["description"].strip()

    return date_str, metadata


def extract_frontmatter_tags(meta: dict) -> list:
    """规范化 frontmatter tags。无 tags 键返回 None（区分"空列表"和"无键"）。"""
    if "tags" not in meta:
        return None
    raw_tags = meta.get("tags")
    if isinstance(raw_tags, list):
        return [str(t).strip() for t in raw_tags if str(t).strip()]
    if isinstance(raw_tags, str):
        return [t.strip() for t in raw_tags.split(",") if t.strip()]
    return []


def effective_tags(content: str, provided=None) -> list:
    """frontmatter tags 优先；缺失时回退 provided。"""
    fm_tags = extract_frontmatter_tags(parse_frontmatter(content))
    if fm_tags is not None:
        return fm_tags
    return provided


def effective_date(content: str, provided=None) -> str:
    """frontmatter date 优先；缺失时回退 provided。"""
    fm_date, _ = extract_metadata(parse_frontmatter(content))
    return fm_date or provided or None


def ensure_wiki_frontmatter(content: str, title: str, tags: list, date_str: str,
                            extra_fields: dict = None) -> str:
    """若无 frontmatter 则补全标准 wiki frontmatter。已有则原样返回。

    spec-embedded-iot 语境的标准字段：title / date / tags + extra_fields
    （如 work_item_id / platform / module / bug_type）。
    """
    if not _HAS_YAML:
        return content  # 无 yaml 时跳过补全（不报错）
    if _FRONTMATTER_RE.match(content):
        return content

    metadata = {
        "title": title,
        "date": (date_str or "").strip() or date.today().isoformat(),
        "tags": [str(tag).strip() for tag in tags if str(tag).strip()],
    }
    if extra_fields:
        for k, v in extra_fields.items():
            if v is not None and v != "":
                metadata[k] = v

    frontmatter = yaml.safe_dump(
        metadata, sort_keys=False, allow_unicode=True, default_flow_style=False
    ).strip()
    body = content.lstrip("\n")
    return f"---\n{frontmatter}\n---\n\n{body}"


def default_description(content: str, title: str) -> str:
    """从内容首行非空非 footnote 行生成 description（截 180 字符）。"""
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("[^"):
            continue
        line = re.sub(r"^#+\s*", "", line).strip()
        if line:
            return line[:180]
    return f"Notes about {title}."


def is_footnote_suffix_line(line: str) -> bool:
    """判断是否 footnote 定义行或后续缩进行（用于 split trailing footnotes）。"""
    return line.strip() == "" or line.startswith((" ", "\t")) or bool(_FOOTNOTE_DEF_RE.match(line))


def split_trailing_footnotes(content: str) -> tuple:
    """拆分 markdown 为 (body, footnote_block)。

    Footnote 定义惯例放在文末。在末尾追加新章节会把引用夹在中间，
    所以 append 操作要先拆出末尾的 footnote 块。
    """
    lines = content.split("\n")
    cut = len(lines)
    while cut > 0 and is_footnote_suffix_line(lines[cut - 1]):
        cut -= 1
    if cut == len(lines):
        return content, ""
    body = "\n".join(lines[:cut]).rstrip()
    footnotes = "\n".join(lines[cut:]).strip()
    return body, footnotes


def has_frontmatter(content: str) -> bool:
    return bool(_FRONTMATTER_RE.match(content))


def strip_frontmatter(content: str) -> str:
    """去掉 frontmatter，返回纯正文。无 frontmatter 则原样返回。"""
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return content
    return content[m.end():]
