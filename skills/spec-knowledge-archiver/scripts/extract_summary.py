#!/usr/bin/env python3
"""
从文档中提取结构化摘要。

支持多种文档类型的摘要提取：
- 优先解析 summary_section 中的 key-value 表格
- 兜底：全文搜索匹配字段名的表格
- 最终兜底：提取首个有意义段落

用法:
  python extract_summary.py <文档路径>
  python extract_summary.py <文档路径> --type bug
"""

import json
import os
import re
import sys

# ── 默认字段映射（向后兼容，无 config 时使用）────────────────

DEFAULT_FIELD_MAP = {
    "平台": "platform",
    "模块": "module",
    "问题分类": "bug_type",
    "症状关键词": "symptoms",
    "根因概述": "root_cause",
    "调用链摘要": "call_chain_summary",
    "检索关键词": "keywords",
}
DEFAULT_LIST_FIELDS = {"symptoms", "keywords"}

# ── 表格解析 ─────────────────────────────────────────────

# 匹配 | **字段** | 值 | 或 | 字段 | 值 |
TABLE_ROW_RE = re.compile(
    r"^\|\s*\*{0,2}(.+?)\*{0,2}\s*\|\s*(.+?)\s*\|$", re.MULTILINE
)


def _parse_table_rows(text, field_map, list_fields):
    """从文本中解析表格行，返回 {en_key: value} 字典。
    field_map: {中文字段名: 英文key}
    list_fields: 需要按逗号拆分的字段集合
    """
    summary = {}
    for row in TABLE_ROW_RE.finditer(text):
        field_cn = row.group(1).strip().rstrip("：:")
        value = row.group(2).strip()
        field_en = field_map.get(field_cn)
        if not field_en:
            continue
        if field_en in list_fields:
            summary[field_en] = [v.strip() for v in value.split(",") if v.strip()]
        else:
            summary[field_en] = value
    return summary


def _find_summary_section(content, section_hint=None):
    """查找摘要节，返回节内容文本。
    section_hint: 配置中的 summary_section 值（如 "结构化摘要"）
    """
    hints = [section_hint] if section_hint else []
    # 默认尝试的节标题模式
    default_hints = ["结构化摘要", "摘要", "概述", "Summary", "Abstract"]
    for h in default_hints:
        if h not in hints:
            hints.append(h)

    for hint in hints:
        pattern = re.compile(
            r"^##\s*\d*[\.\s]*" + re.escape(hint), re.MULTILINE
        )
        match = pattern.search(content)
        if match:
            start = match.end()
            next_section = re.search(r"^## ", content[start:], re.MULTILINE)
            if next_section:
                return content[start:start + next_section.start()]
            return content[start:]

    return None


def _extract_first_paragraph(content):
    """兜底：提取文档中首个有意义段落（跳过标题和空行）。"""
    lines = content.split("\n")
    paragraph_lines = []
    for line in lines:
        stripped = line.strip()
        # 跳过标题、空行、分隔线、表格行
        if not stripped or stripped.startswith("#") or stripped.startswith("---") or stripped.startswith("|"):
            if paragraph_lines:
                break
            continue
        paragraph_lines.append(stripped)
        if len(paragraph_lines) >= 5:
            break
    text = " ".join(paragraph_lines)
    return text[:300] if text else None


# ── 主提取函数 ───────────────────────────────────────────

def extract_summary(filepath, doc_type_config=None):
    """从文档提取结构化摘要。

    doc_type_config: 来自 knowledge_config.json 的 doc_type 配置对象。
    为 None 时使用默认 bug 字段映射（向后兼容）。

    提取策略:
    1. 在 summary_section 中找 table，按 summary_fields 解析
    2. 全文搜索匹配字段名的 table
    3. 兜底：提取首个段落作为 summary
    """
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        content = f.read()

    # 从配置读取字段定义，或使用默认
    if doc_type_config and "summary_fields" in doc_type_config:
        field_map = doc_type_config["summary_fields"]
        list_fields = set(doc_type_config.get("list_fields", []))
        section_hint = doc_type_config.get("summary_section")
    else:
        field_map = DEFAULT_FIELD_MAP
        list_fields = DEFAULT_LIST_FIELDS
        section_hint = None

    # 策略 1：在摘要节中解析表格
    section_text = _find_summary_section(content, section_hint)
    if section_text:
        summary = _parse_table_rows(section_text, field_map, list_fields)
        if summary:
            return summary

    # 策略 2：全文搜索匹配字段名的表格
    summary = _parse_table_rows(content, field_map, list_fields)
    if summary:
        return summary

    # 策略 3：兜底 - 提取首个段落
    fallback = _extract_first_paragraph(content)
    if fallback:
        return {"summary": fallback}

    return None


# ── CLI 入口 ─────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("用法: python extract_summary.py <文档路径> [--type bug]", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    if not os.path.isfile(filepath):
        print(f"错误: 文件不存在: {filepath}", file=sys.stderr)
        sys.exit(1)

    # 可选加载配置
    doc_type_config = None
    if "--type" in sys.argv:
        idx = sys.argv.index("--type")
        if idx + 1 < len(sys.argv):
            doc_type = sys.argv[idx + 1]
            try:
                sys.path.insert(0, os.path.dirname(__file__))
                from common import load_config, get_doc_type_config
                doc_type_config = get_doc_type_config(doc_type)
            except Exception:
                pass

    summary = extract_summary(filepath, doc_type_config)
    if summary:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print("错误: 未能提取摘要", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
