#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_profile.py - 校验 spec-bug-analyzer 的 profiles/{平台}.md 是否符合 _schema.md 契约。

校验项：
  1. 必填段「代码地图」存在（段标题存在即合规；空框架的「暂无积累」注释占位属合法声明） [error]
  2. 「检索清单」段不含通用串黑名单（通用串属通用基线，DRY）              [error]
  3. 三名一致：profile 文件名 == 知识库 raw/platform/{X}/ 目录名          [error]
  4. 段标题规范化后只能是契约六段（未知段警告，不致命）                   [warn]

> 本脚本只做静态 schema 合规校验，**查不了代码地图过期**（脚本访问不到实际项目代码）。
> 过期只能靠 SKILL.md Step 5 运行时 glob 探测发现。

用法：
  python check_profile.py
  python check_profile.py --profiles-dir ./profiles --kb-platform-dir <dir>
退出码：0=全部通过，1=存在 error 级违规，2=环境问题（目录缺失）
"""

import argparse
import re
import sys
from pathlib import Path

# 通用串黑名单：检索清单段禁止出现（它们属通用基线，列这里是 DRY 违规）
# 英文用词边界匹配（\b），避免误伤 WdTimeout / freeHeap 等合法平台专属串
GENERIC_TOKENS_EN = [
    "ERROR", "fail", "timeout", "overflow",
    "PARAM_ERROR", "INVALID_PARAM",
    "disconn", "closed", "reconnect",
]
GENERIC_TOKENS_ZH = ["断连", "重连", "超时"]

# 契约允许的六段（规范化后的段标题）
ALLOWED_SECTIONS = {
    "根注解", "日志差异", "代码地图", "检索清单",
    "差异化定位手法", "平台专属问题模式",
}
REQUIRED_SECTIONS = ["代码地图"]


def _norm_title(title):
    """去掉段标题的中文/英文括号后缀，返回契约段名。
    例：「检索清单（仅平台独有，通用串不列）」→「检索清单」。允许 profile 标题带可读性后缀。"""
    return re.split(r"[（(]", title, maxsplit=1)[0].strip()


def parse_sections(text):
    """解析 markdown，返回 {规范化段标题: 段正文}，只取 ## 级标题（### 子段并入父段正文）。"""
    sections = {}
    current = None
    buf = []
    for line in text.splitlines():
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            if current is not None:
                sections[current] = "\n".join(buf)
            current = _norm_title(m.group(1).strip())
            buf = []
        elif current is not None:
            buf.append(line)
    if current is not None:
        sections[current] = "\n".join(buf)
    return sections


def check_generic_tokens(checklist_text):
    """返回检索清单段里命中的通用串列表（英文词边界 / 中文子串）。"""
    plain = checklist_text.replace("`", "")
    hit = []
    for tok in GENERIC_TOKENS_EN:
        if re.search(r"\b" + re.escape(tok) + r"\b", plain, re.IGNORECASE):
            hit.append(tok)
    for tok in GENERIC_TOKENS_ZH:
        if tok in plain:
            hit.append(tok)
    return sorted(set(hit))


def check_profile(name, text, kb_platform_dir=None):
    """校验单个 profile，返回 [(level, message), ...]，level in {error, warn}。"""
    violations = []
    sections = parse_sections(text)

    # 1. 必填段（段存在即合规；空框架的「暂无积累」注释占位属合法声明）
    for req in REQUIRED_SECTIONS:
        if req not in sections:
            violations.append(("error", f"缺必填段「{req}」"))

    # 2. 未知段（规范化后比对；警告不致命）
    for title in sections:
        if title not in ALLOWED_SECTIONS:
            violations.append(("warn", f"未知段标题「{title}」（不在契约六段内）"))

    # 3. 检索清单通用串黑名单
    hit = check_generic_tokens(sections.get("检索清单", ""))
    if hit:
        violations.append(("error", f"检索清单含通用串（属通用基线，DRY 违规）：{', '.join(hit)}"))

    # 4. 三名一致
    if kb_platform_dir is not None:
        expected = Path(kb_platform_dir) / name
        if not expected.is_dir():
            violations.append(("error", f"三名不一致：profile「{name}」无对应知识库目录 {expected}"))

    return violations


def main():
    # Windows GBK 终端下强制 UTF-8 输出，避免中文/符号编码崩溃（详见 pcap-analyzer-guide.md 避坑）
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="校验 spec-bug-analyzer profiles 契约")
    here = Path(__file__).resolve().parent
    parser.add_argument("--profiles-dir", default=str(here.parent / "profiles"))
    parser.add_argument(
        "--kb-platform-dir",
        default=str(Path.home() / ".spec-embedded-iot" / "knowledge" / "raw" / "platform"),
    )
    args = parser.parse_args()

    profiles_dir = Path(args.profiles_dir)
    if not profiles_dir.is_dir():
        print(f"profiles 目录不存在：{profiles_dir}", file=sys.stderr)
        return 2

    kb_dir = Path(args.kb_platform_dir)
    kb_provided = kb_dir.is_dir()
    if not kb_provided:
        print(f"[W] 知识库平台目录不存在，跳过三名一致校验：{kb_dir}", file=sys.stderr)

    files = sorted(p for p in profiles_dir.glob("*.md") if not p.name.startswith("_"))
    if not files:
        print(f"未找到 profile 文件（{profiles_dir}/*.md，跳过 _ 开头）", file=sys.stderr)
        return 2

    total_errors = 0
    for f in files:
        name = f.stem
        text = f.read_text(encoding="utf-8")
        violations = check_profile(name, text, str(kb_dir) if kb_provided else None)
        errs = [v for v in violations if v[0] == "error"]
        print(f"[{'OK' if not errs else f'{len(errs)} error(s)'}] {name}")
        for level, msg in violations:
            print(f"    {'[E]' if level == 'error' else '[W]'} {msg}")
        total_errors += len(errs)

    print(f"\n{'PASS' if total_errors == 0 else 'FAIL'}：{len(files)} profile，{total_errors} error")
    return 1 if total_errors else 0


if __name__ == "__main__":
    sys.exit(main())
