"""wiki 操作日志写入工具（对齐 llm-wiki.md 的 log.md 机制）。

来源：https://github.com/nashsu/llm_wiki/blob/main/llm-wiki.md
理念：log.md 是逆向时间序的操作历史，格式 `## [YYYY-MM-DD] op | 详情`，
      可被 grep 解析：`grep "^## \\[" wiki/log.md | head -10`。

只记录不可逆操作（归档/lint/重大维护），不记录只读操作（检索/浏览）。
"""
import os
import re
from datetime import datetime


_LOG_SKELETON_TEMPLATE = """\
---
title: Wiki 操作日志
date: {date}
tags: [log, 操作历史]
type: log
---

# Wiki 操作日志

> 逆向时间序（最新在上）。格式：`## [YYYY-MM-DD] op | 详情`
> 可用 `grep "^## \\[" wiki/log.md | head -10` 查看最近 10 条。
> 来源对齐 [llm-wiki.md](https://github.com/nashsu/llm_wiki/blob/main/llm-wiki.md) 的 log 机制。

"""


def _format_entry(op, detail, platform=None, count=None):
    """构造一条 log 条目文本（不含尾部空行，由调用方处理）。"""
    today = datetime.now().strftime("%Y-%m-%d")
    lines = [f"## [{today}] {op} | {detail}"]
    meta_parts = []
    if platform:
        meta_parts.append(f"platform={platform}")
    if count is not None:
        meta_parts.append(f"count={count}")
    if meta_parts:
        lines.append(f"  ({', '.join(meta_parts)})")
    return "\n".join(lines)


def append_log(knowledge_root, op, detail, platform=None, count=None):
    """向 wiki/log.md 追加一条操作记录（逆向时间序，最新插在标题后）。

    Args:
        knowledge_root: knowledge/ 根路径
        op: 操作类型（ingest / lint / schema / refactor / synthesize）
        detail: 一句话详情
        platform: 可选，涉及的平台
        count: 可选，涉及的条目数

    Returns:
        True 表示写入成功。

    若 log.md 不存在则按骨架创建（含 frontmatter）。
    """
    log_path = os.path.join(knowledge_root, "wiki", "log.md")
    today = datetime.now().strftime("%Y-%m-%d")
    entry = _format_entry(op, detail, platform, count)

    if not os.path.exists(log_path):
        # 首次创建：写骨架 + 首条记录
        skeleton = _LOG_SKELETON_TEMPLATE.format(date=today)
        # 确保 wiki/ 目录存在
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(skeleton + entry + "\n")
        return True

    # 已存在：在第一个 `## [` 操作条目前插入（保持最新在上）
    with open(log_path, "r", encoding="utf-8") as f:
        text = f.read()

    m = re.search(r"(^|\n)(## \[)", text)
    entry_block = entry + "\n"
    if m:
        insert_at = m.start() + len(m.group(1))
        new_text = text[:insert_at] + entry_block + text[insert_at:]
    else:
        # 没找到任何操作条目（可能只有骨架），追加到末尾
        new_text = text.rstrip() + "\n\n" + entry_block

    with open(log_path, "w", encoding="utf-8") as f:
        f.write(new_text)
    return True
