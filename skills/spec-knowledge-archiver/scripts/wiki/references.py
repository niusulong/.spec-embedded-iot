"""引用与 wiki 链接的正则解析（纯函数，无 DB 依赖）。

Derived from lucasastorian/llmwiki (Apache-2.0).
Source: mcp/tools/references.py
Modifications for spec-embedded-iot:
  - 仅保留 _parse_citation_filename 和 _parse_wiki_links 两个纯函数
  - 移除 update_references / get_backlinks_summary（依赖 VaultFS 异步存储层）
  - 应用层需要时基于这两个函数自行实现链接图
"""

import re

# footnote 定义行：[^1]: Bug分析.md, p.3
_CITATION_RE = re.compile(r"\[\^\d+\]:\s*(.+)$", re.MULTILINE)
# markdown 链接 [text](href)，排除图片 ![alt](href)
_WIKI_LINK_RE = re.compile(r"(?<!!)\[(?:[^\]]*)\]\(([^)]+)\)")


def parse_citation_filename(raw: str) -> tuple:
    """从 'Bug分析.md, p.3' 解析出 (filename, page)。

    page 为 int 或 None。支持：
      - 'paper.pdf, p.3'       → ('paper.pdf', 3)
      - 'paper.pdf, p3'        → ('paper.pdf', 3)
      - '[link](url) suffix'   → 解包链接文本
      - 'a - b'                → 取破折号前 ('a', None)
      - 'paper.pdf'            → ('paper.pdf', None)
    """
    raw = raw.strip().lstrip("*").rstrip("*")
    link_match = re.match(r"\[([^\]]+)\]\([^)]*\)(.*)$", raw)
    if link_match:
        raw = f"{link_match.group(1)}{link_match.group(2)}"
    raw = re.split(r"\s+[-–—]\s+", raw, maxsplit=1)[0].strip()
    page_match = re.search(r",\s*p\.?\s*(\d+)\b", raw)
    if page_match:
        filename = raw[:page_match.start()].strip()
        page = int(page_match.group(1))
    else:
        filename = raw
        page = None
    return filename, page


def parse_citations(content: str) -> list:
    """从 markdown 内容提取所有 footnote 引用，返回 [(filename, page), ...]。"""
    results = []
    for match in _CITATION_RE.finditer(content):
        filename, page = parse_citation_filename(match.group(1))
        if filename:
            results.append((filename, page))
    return results


def parse_wiki_links(content: str, current_dir: str) -> list:
    """提取内部 wiki 链接路径，相对 current_dir 解析。

    支持四种形式（与 llmwiki 一致）：
      - '/wiki/foo.md'         → 'foo.md'（去掉 /wiki/ 前缀）
      - './bar.md'             → current_dir + 'bar.md'
      - '../baz.md'            → 路径回溯
      - 'qux.md'（无斜杠）     → current_dir + 'qux.md'
    跳过：http/mailto/data 外部链接、图片链接。
    """
    paths = []
    for match in _WIKI_LINK_RE.finditer(content):
        href = match.group(1)
        if href.startswith(("http", "#", "mailto:", "data:")):
            continue
        if re.search(r"\.(png|jpg|jpeg|gif|webp|svg)$", href, re.IGNORECASE):
            continue

        if href.startswith("/wiki/"):
            resolved = href.replace("/wiki/", "", 1)
        elif href.startswith("./"):
            resolved = (current_dir + href[2:]) if current_dir else href[2:]
        elif href.startswith("../"):
            parts = (current_dir.rstrip("/") + "/" + href).split("/")
            resolved_parts = []
            for p in parts:
                if p == "..":
                    if resolved_parts:
                        resolved_parts.pop()
                elif p and p != ".":
                    resolved_parts.append(p)
            resolved = "/".join(resolved_parts)
        elif "/" not in href:
            resolved = (current_dir + href) if current_dir else href
        else:
            resolved = href

        if resolved:
            paths.append(resolved)
    return paths
