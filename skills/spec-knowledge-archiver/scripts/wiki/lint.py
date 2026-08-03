"""wiki 一致性检查器（lint）。

Derived from lucasastorian/llmwiki (Apache-2.0).
Source: mcp/tools/lint.py (LintHandler class)
Modifications for spec-embedded-iot:
  - 移除 MCP FastMCP 注册（register 函数）
  - 移除 VaultFS 依赖，改为直接读 wiki/ 文件系统
  - 检查项裁剪：保留 frontmatter / type / 失效链接 / 孤儿页 / INDEX 同步
  - 移除 citation graph 检查（我们不用 footnote citation，用 wikilink）
  - 移除 stale_page 检查（依赖 DB 跟踪，暂不需要）

检查项（按严重度）：
  🔴 ERROR:
    - missing-frontmatter:    wiki/*.md 缺 frontmatter
    - missing-required-field: frontmatter 缺 title/date/tags/type
    - invalid-type:           type 不在 {concept, entry, index, home, log, schema}
    - broken-link:            wikilink/file link 指向不存在的文件
  🟡 WARN:
    - orphan-entry:           entries/*.md 未被任何 concept 或 INDEX 引用
    - index-out-of-sync:      INDEX.md 列出的条目无对应 entries/ 文件（或反之）
    - missing-home:           wiki/Home.md 不存在
    - missing-index:          wiki/INDEX.md 不存在
    - raw-without-wiki-entry: raw/platform/ 有条目但 wiki/entries/ 无对应精炼页
                              （raw 自动归档、wiki 人工补写，本项对账防漂移）
"""

import os
import re
from dataclasses import dataclass, field

from wiki.frontmatter import parse_frontmatter
from wiki.references import parse_wiki_links


# type 集合：concept/entry/index/home 是 wiki 主体类型；
# log（操作日志）/ schema（规范文档）是辅助类型，对齐 llm-wiki.md 三层抽象
VALID_TYPES = {"concept", "entry", "index", "home", "log", "schema"}
REQUIRED_FIELDS = ("title", "date", "tags", "type")

# wikilink [[xxx]] 或 [[xxx|alias]]
_WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")


@dataclass
class LintIssue:
    severity: str       # "ERROR" | "WARN" | "INFO"
    code: str           # 如 "missing-frontmatter"
    message: str        # 人类可读
    file: str = ""      # 被检查的 wiki 文件相对路径


@dataclass
class LintReport:
    wiki_root: str
    issues: list = field(default_factory=list)

    def add(self, severity, code, message, file=""):
        self.issues.append(LintIssue(severity, code, message, file))

    @property
    def error_count(self):
        return sum(1 for i in self.issues if i.severity == "ERROR")

    @property
    def warn_count(self):
        return sum(1 for i in self.issues if i.severity == "WARN")

    def is_clean(self):
        return self.error_count == 0

    def summary(self):
        return (f"{len(self.issues)} 个问题（{self.error_count} ERROR, "
                f"{self.warn_count} WARN）{'[OK]' if self.is_clean() else '[需修复]'}")


def _list_md_files(root):
    """递归列出 root 下所有 .md 文件，返回相对 root 的 posix 相对路径集合。"""
    result = set()
    for dirpath, _, filenames in os.walk(root):
        for f in filenames:
            if f.endswith(".md"):
                full = os.path.join(dirpath, f)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                result.add(rel)
    return result


def _resolve_wikilink(target, current_file, all_md_files):
    """解析 [[target]] 到 all_md_files 中的实际文件。

    匹配优先级：
      1. target 完全等于某 .md 相对路径（去掉 .md 后缀）
      2. target 等于某文件 basename（去 .md）
      3. target 加 .md 后等于某 basename
    """
    # 去掉 .md 后缀（用户可能写或不写）
    target_clean = target[:-3] if target.endswith(".md") else target
    target_with_md = target if target.endswith(".md") else target + ".md"

    # 完整路径匹配
    for f in all_md_files:
        if f[:-3] == target_clean or f == target_with_md:
            return f

    # basename 匹配
    for f in all_md_files:
        base = os.path.basename(f)
        if base == target_with_md or base[:-3] == target_clean:
            return f

    return None


def _norm_name(s):
    """标准化名称用于宽松匹配：去分隔符、小写。"""
    return re.sub(r"[_\-\s]+", "", str(s)).lower()


def _raw_entry_has_wiki(raw_entry, plat, wiki_files):
    """raw 条目目录名是否在 wiki_files（{plat}_*.md）中有对应精炼页。

    宽松匹配（wid 优先；NA/无 wid 用标准化 desc 子串），宁可漏报不误报。
    """
    tokens = re.split(r"[_\s]+", raw_entry)
    wid = next((t for t in tokens if t.isdigit() and len(t) >= 6), None)
    for wf in wiki_files:
        if wid and wid in wf:
            return True
        wf_body = wf[len(plat) + 1:-3] if wf.endswith(".md") else wf[len(plat) + 1:]
        wf_body_norm = _norm_name(wf_body)
        raw_norm = _norm_name(raw_entry)
        if len(wf_body_norm) >= 8 and (wf_body_norm in raw_norm or raw_norm in wf_body_norm):
            return True
    return False


def _check_raw_wiki_drift(report, wiki_root, raw_root):
    """检测 raw→wiki 漂移：raw 有条目但 wiki/entries 无对应精炼页。

    raw 由 kb.py archive 自动写入，wiki entry 靠人工/agent 补写——两者解耦，
    本检查在 lint 时对账，防止"raw 增长、wiki 落后"的隐形漏洞。
    """
    if not raw_root or not os.path.isdir(raw_root):
        return
    platform_root = os.path.join(raw_root, "platform")
    if not os.path.isdir(platform_root):
        return

    type_dirs = ("bug-solutions", "code-summary", "requirement-solutions")
    drift_count = 0
    for plat in sorted(os.listdir(platform_root)):
        plat_path = os.path.join(platform_root, plat)
        if not os.path.isdir(plat_path):
            continue
        for typ in type_dirs:
            typ_path = os.path.join(plat_path, typ)
            if not os.path.isdir(typ_path):
                continue
            raw_entries = [d for d in os.listdir(typ_path)
                           if os.path.isdir(os.path.join(typ_path, d))]
            if not raw_entries:
                continue
            wiki_type_dir = os.path.join(wiki_root, "entries", typ)
            wiki_files = []
            if os.path.isdir(wiki_type_dir):
                wiki_files = [f for f in os.listdir(wiki_type_dir)
                              if f.endswith(".md") and f.startswith(plat + "_")]
            for entry in sorted(raw_entries):
                if _raw_entry_has_wiki(entry, plat, wiki_files):
                    continue
                drift_count += 1
                report.add("WARN", "raw-without-wiki-entry",
                           f"raw 有条目但 wiki/entries/{typ}/ 无对应精炼页："
                           f"{plat}/{typ}/{entry}（归档后需补写 entry + 更新 INDEX）",
                           f"entries/{typ}/")
    if drift_count:
        report.add("INFO", "raw-wiki-drift-summary",
                   f"共 {drift_count} 个 raw 条目疑似缺少 wiki 精炼页"
                   f"（宽松匹配，可能含少量误报，请人工核对）", "")


def lint_wiki(wiki_root):
    """检查 wiki 目录一致性。返回 LintReport。

    wiki_root: wiki/ 目录的绝对路径
    """
    report = LintReport(wiki_root=wiki_root)

    if not os.path.isdir(wiki_root):
        report.add("ERROR", "wiki-not-found", f"wiki 目录不存在: {wiki_root}")
        return report

    all_md = _list_md_files(wiki_root)
    if not all_md:
        report.add("WARN", "empty-wiki", "wiki 目录为空（无任何 .md 文件）")
        return report

    # ── 必要文件检查 ─────────────────────────────────────
    if "Home.md" not in all_md:
        report.add("WARN", "missing-home", "缺少 wiki/Home.md（知识库入口）")
    if "INDEX.md" not in all_md:
        report.add("WARN", "missing-index", "缺少 wiki/INDEX.md（检索入口目录）")

    # ── 收集所有 wikilink 引用关系（用于孤儿页检查）───────
    referenced_files = set()  # 被任何文件引用过的文件

    # ── 逐文件检查 ───────────────────────────────────────
    for rel_path in sorted(all_md):
        full_path = os.path.join(wiki_root, rel_path)
        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            report.add("ERROR", "unreadable", f"无法读取: {e}", rel_path)
            continue

        # 1. frontmatter 检查
        meta = parse_frontmatter(content)
        if not meta:
            report.add("ERROR", "missing-frontmatter",
                       "wiki/*.md 必须有 frontmatter（--- ... ---）", rel_path)
        else:
            for field_name in REQUIRED_FIELDS:
                if field_name not in meta or meta[field_name] in (None, "", []):
                    report.add("ERROR", "missing-required-field",
                               f"frontmatter 缺必填字段 '{field_name}'", rel_path)
            type_val = meta.get("type")
            if type_val and type_val not in VALID_TYPES:
                report.add("ERROR", "invalid-type",
                           f"type='{type_val}' 不在 {sorted(VALID_TYPES)}", rel_path)

        # 2. wikilink [[xxx]] 检查（失效链接）
        current_dir = os.path.dirname(rel_path).replace(os.sep, "/")
        if current_dir == ".":
            current_dir = ""
        else:
            current_dir = current_dir + "/"

        for m in _WIKILINK_RE.finditer(content):
            target = m.group(1).strip()
            resolved = _resolve_wikilink(target, rel_path, all_md)
            if resolved is None:
                report.add("ERROR", "broken-link",
                           f"wikilink [[{target}]] 指向不存在的页面", rel_path)
            else:
                referenced_files.add(resolved)

        # 3. markdown link [text](path) 检查（仅 wiki 内部链接）
        for link_path in parse_wiki_links(content, current_dir):
            # 跳过外部（../platform/... 这种指向 raw 的，不检查——raw 文件可能很多）
            if link_path.startswith("../") or link_path.startswith("platform/"):
                continue
            resolved = _resolve_wikilink(link_path, rel_path, all_md)
            if resolved is None and not link_path.endswith((".md",)):
                # 可能是目录链接，跳过
                continue
            if resolved is None:
                # 严格匹配：link_path 在 all_md 中（加 .md）
                candidates = [f for f in all_md if f == link_path or f == link_path + ".md"
                              or os.path.basename(f) == link_path
                              or os.path.basename(f) == link_path + ".md"]
                if not candidates:
                    # 不报 wiki 内部的 broken markdown link（太常见、误报多）
                    # 只报 wikilink 的（上面已报）
                    pass
            else:
                referenced_files.add(resolved)

    # 4. 孤儿页检查：entries/ 下未被引用的页（warning，不是 error）
    entries_files = {f for f in all_md if f.startswith("entries/")}
    orphan_entries = entries_files - referenced_files - {"INDEX.md", "Home.md"}
    for f in sorted(orphan_entries):
        report.add("WARN", "orphan-entry",
                   "entries/ 页未被任何 concept 或 INDEX 引用（建议在 INDEX.md 添加链接）", f)

    # 5. INDEX.md 与 entries/ 同步（粗检查：INDEX.md 提到的 entries/ 文件名应存在）
    index_path = os.path.join(wiki_root, "INDEX.md")
    if os.path.isfile(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            index_content = f.read()
        # 找 INDEX.md 里所有 markdown 链接指向 entries/ 的
        for m in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", index_content):
            href = m.group(1)
            if href.startswith("entries/"):
                # 检查是否存在
                target_in_wiki = href
                # 解析为相对 wiki/ 的路径
                target_clean = target_inwiki_resolve(href)
                full_target = os.path.join(wiki_root, target_clean)
                if not os.path.isfile(full_target):
                    report.add("WARN", "index-out-of-sync",
                               f"INDEX.md 链接 '{href}' 指向的文件不存在", "INDEX.md")

    # 6. raw→wiki 漂移检测：raw 区有条目但 wiki/entries 无对应精炼页
    raw_root = os.path.join(os.path.dirname(os.path.abspath(wiki_root)), "raw")
    _check_raw_wiki_drift(report, wiki_root, raw_root)

    return report


def target_inwiki_resolve(href):
    """把 INDEX.md 里的链接（可能是 entries/foo.md 或 ./entries/foo.md）规范化。"""
    if href.startswith("./"):
        return href[2:]
    if href.startswith("/"):
        return href.lstrip("/")
    return href


def format_report(report):
    """格式化 LintReport 为可打印的字符串。"""
    lines = [f"wiki 一致性检查: {report.wiki_root}"]
    lines.append(f"结果: {report.summary()}")
    lines.append("")
    if not report.issues:
        lines.append("[OK] 无问题")
        return "\n".join(lines)

    # 按严重度分组
    for severity in ("ERROR", "WARN", "INFO"):
        sev_issues = [i for i in report.issues if i.severity == severity]
        if not sev_issues:
            continue
        lines.append(f"── {severity}（{len(sev_issues)}）──")
        for issue in sev_issues:
            loc = f" [{issue.file}]" if issue.file else ""
            lines.append(f"  [{issue.code}] {issue.message}{loc}")
        lines.append("")

    return "\n".join(lines)
