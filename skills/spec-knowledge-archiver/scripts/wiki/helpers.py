"""纯工具函数（无 DB、无状态、无第三方依赖）。

Derived from lucasastorian/llmwiki (Apache-2.0).
Source: mcp/tools/helpers.py
Modifications for spec-embedded-iot:
  - 移除 deep_link（依赖 hosted config）
  - 保留 glob_match / resolve_path / parse_page_range（零依赖纯函数）
"""

from fnmatch import fnmatch


def glob_match(filepath: str, pattern: str) -> bool:
    """fnmatch 封装，支持 * ? [] 通配。"""
    return fnmatch(filepath, pattern)


def resolve_path(path: str) -> tuple:
    """把 'a/b/c.md' 拆成 ('/a/b/', 'c.md')；'c.md' 拆成 ('/', 'c.md')。

    返回 (dir_path, filename) 二元组，dir_path 总是以 / 开头和结尾。
    """
    path_clean = path.lstrip("/")
    if "/" in path_clean:
        dir_path = "/" + path_clean.rsplit("/", 1)[0] + "/"
        filename = path_clean.rsplit("/", 1)[1]
    else:
        dir_path = "/"
        filename = path_clean
    return dir_path, filename


def parse_page_range(pages_str: str, max_page: int) -> list:
    """解析 "1-3,5,7-9" 字符串为页号列表，受 max_page 上限约束。

    非法输入静默跳过（不抛异常）。用于从原文档引用 "Bug分析.md, p.3"
    提取页号，便于 agent 按需读特定页。
    """
    result = set()
    for part in pages_str.split(","):
        part = part.strip()
        if "-" in part:
            start, end = part.split("-", 1)
            start, end = start.strip(), end.strip()
            if not start.isdigit() or not end.isdigit():
                continue
            s, e = int(start), int(end)
            for p in range(max(1, s), min(max_page, e) + 1):
                result.add(p)
        elif part.isdigit():
            p = int(part)
            if 1 <= p <= max_page:
                result.add(p)
    return sorted(result)
