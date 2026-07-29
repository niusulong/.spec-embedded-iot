"""文本分块器（lint 工具用，不再用于建向量索引）。

Derived from lucasastorian/llmwiki (Apache-2.0).
Source: mcp/services/chunker.py
Modifications for spec-embedded-iot:
  - 移除 store_chunks_pg / store_chunks_sqlite（DB 持久化）
  - 保留 chunk_text + Chunk dataclass + 所有辅助纯函数
  - 用途变更：原用于向量索引，现仅用于 lint（检查超长段落、估算文档体量）
"""

import re
from dataclasses import dataclass

CHUNK_SIZE = 512       # token 估算（1 token ≈ 4 字符）
CHUNK_OVERLAP = 128
MIN_CHUNK_TOKENS = 32
MAX_CHUNK_CHARS = 10_000

SENTENCE_RE = re.compile(r"(?<=[.!?。！？])\s+")
HEADER_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def _estimate_tokens(text: str) -> int:
    """粗估 token 数（4 字符 ≈ 1 token）。CJK 也按此估，误差可接受（仅用于 lint）。"""
    return max(1, len(text) // 4)


@dataclass
class Chunk:
    index: int
    content: str
    page: int = None
    start_char: int = 0
    token_count: int = 0
    header_breadcrumb: str = ""


def chunk_text(content: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP, page: int = None) -> list:
    """把 markdown 切成 ~chunk_size token 的块，含 overlap 和 header 面包屑。

    段落级切分（按空行），跟踪 H1-H6 面包屑，超长段自动二次切分。
    返回 Chunk 列表（按文档顺序）。空内容返回 []。
    """
    if not content or not content.strip():
        return []

    paragraphs = _split_paragraphs(content)
    header_stack = []
    chunks = []
    current_blocks = []
    current_tokens = 0
    current_start = 0
    char_pos = 0

    for para in paragraphs:
        para_tokens = _estimate_tokens(para)

        m = HEADER_RE.match(para)
        if m:
            level = len(m.group(1))
            heading = m.group(2).strip()
            header_stack = [(l, t) for l, t in header_stack if l < level]
            header_stack.append((level, heading))

        if current_tokens + para_tokens > chunk_size and current_blocks:
            text = "\n\n".join(current_blocks)
            if _estimate_tokens(text) >= MIN_CHUNK_TOKENS:
                breadcrumb = " > ".join(t for _, t in header_stack)
                chunks.append(Chunk(
                    index=len(chunks), content=text, page=page,
                    start_char=current_start, token_count=_estimate_tokens(text),
                    header_breadcrumb=breadcrumb,
                ))
            overlap_blocks, overlap_tokens = _get_overlap(current_blocks, overlap)
            current_blocks = overlap_blocks
            current_tokens = overlap_tokens
            current_start = char_pos - sum(len(b) + 2 for b in overlap_blocks)

        current_blocks.append(para)
        current_tokens += para_tokens
        char_pos += len(para) + 2

    if current_blocks:
        text = "\n\n".join(current_blocks)
        if _estimate_tokens(text) >= MIN_CHUNK_TOKENS:
            breadcrumb = " > ".join(t for _, t in header_stack)
            chunks.append(Chunk(
                index=len(chunks), content=text, page=page,
                start_char=current_start, token_count=_estimate_tokens(text),
                header_breadcrumb=breadcrumb,
            ))

    return _enforce_max_chars(chunks)


def _enforce_max_chars(chunks: list) -> list:
    """拆分超过 MAX_CHUNK_CHARS 的块（CJK / 长代码块常见）。"""
    if not any(len(c.content) > MAX_CHUNK_CHARS for c in chunks):
        return chunks

    result = []
    for c in chunks:
        if len(c.content) <= MAX_CHUNK_CHARS:
            result.append(Chunk(
                index=len(result), content=c.content, page=c.page,
                start_char=c.start_char, token_count=c.token_count,
                header_breadcrumb=c.header_breadcrumb,
            ))
            continue
        base = c.start_char or 0
        offset = 0
        for piece in _split_oversized(c.content):
            result.append(Chunk(
                index=len(result), content=piece, page=c.page,
                start_char=base + offset, token_count=_estimate_tokens(piece),
                header_breadcrumb=c.header_breadcrumb,
            ))
            offset += len(piece)
    return result


def _split_oversized(text: str) -> list:
    parts = SENTENCE_RE.split(text)
    pieces = []
    current = ""
    for part in parts:
        candidate = (current + " " + part).strip() if current else part
        if len(candidate) <= MAX_CHUNK_CHARS:
            current = candidate
        else:
            if current:
                pieces.append(current)
            if len(part) <= MAX_CHUNK_CHARS:
                current = part
            else:
                for i in range(0, len(part), MAX_CHUNK_CHARS):
                    pieces.append(part[i:i + MAX_CHUNK_CHARS])
                current = ""
    if current:
        pieces.append(current)
    return pieces


def _split_paragraphs(text: str) -> list:
    parts = re.split(r"\n\s*\n", text)
    return [p.strip() for p in parts if p.strip()]


def _get_overlap(blocks: list, target_tokens: int) -> tuple:
    """从 blocks 末尾向前取不超过 target_tokens 的重叠块。"""
    result = []
    tokens = 0
    for block in reversed(blocks):
        bt = _estimate_tokens(block)
        if tokens + bt > target_tokens:
            break
        result.insert(0, block)
        tokens += bt
    return result, tokens


# 便利函数：估算文档总 token 数（lint 用，检查超长 dump 报告）
def estimate_doc_tokens(content: str) -> int:
    return _estimate_tokens(content) if content else 0
