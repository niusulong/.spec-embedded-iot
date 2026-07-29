#!/usr/bin/env python3
"""spec-knowledge-archiver LLM-Wiki 路线单元测试。

覆盖：
- 文件角色识别（infer_role）
- 日志识别（is_log_file）
- 借鉴模块（helpers / references / frontmatter / chunker）
- archiver 多文件保真归档（端到端：模拟 .spec/bug/ 归档到临时 KNOWLEDGE_ROOT）
- lint 一致性检查（合规/缺字段/失效链接/孤儿）
- guide 触发语生成

不依赖 chromadb / 模型（向量已废弃）。归档测试用临时 KNOWLEDGE_ROOT 隔离。

运行：
    python test_wiki.py
"""

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


class TestRoleInference(unittest.TestCase):
    def test_known_roles(self):
        from wiki.archiver import infer_role
        self.assertEqual(infer_role("Bug分析.md"), "bug-analysis")
        self.assertEqual(infer_role("Dump分析.md"), "dump")
        self.assertEqual(infer_role("修改方案.md"), "solution")
        self.assertEqual(infer_role("代码总结.md"), "summary")
        self.assertEqual(infer_role("项目概览.md"), "overview")

    def test_default_role(self):
        from wiki.archiver import infer_role
        self.assertEqual(infer_role("未知文件.md"), "supplement")
        self.assertEqual(infer_role("random.md"), "supplement")

    def test_log_detection(self):
        from wiki.archiver import is_log_file
        self.assertTrue(is_log_file("at.log"))
        self.assertTrue(is_log_file("dump.txt"))
        self.assertTrue(is_log_file("trace.pcap"))
        self.assertTrue(is_log_file("ram.bin"))
        self.assertFalse(is_log_file("Bug分析.md"))
        self.assertFalse(is_log_file("代码总结.md"))


class TestHelpers(unittest.TestCase):
    """借鉴自 llmwiki helpers.py。"""

    def test_glob_match(self):
        from wiki.helpers import glob_match
        self.assertTrue(glob_match("Bug分析.md", "*.md"))
        self.assertTrue(glob_match("a/b.md", "a/*.md"))
        self.assertFalse(glob_match("Bug.txt", "*.md"))

    def test_resolve_path(self):
        from wiki.helpers import resolve_path
        self.assertEqual(resolve_path("c.md"), ("/", "c.md"))
        self.assertEqual(resolve_path("a/b/c.md"), ("/a/b/", "c.md"))

    def test_parse_page_range(self):
        from wiki.helpers import parse_page_range
        self.assertEqual(parse_page_range("1-3,5,7-9", 20), [1, 2, 3, 5, 7, 8, 9])
        self.assertEqual(parse_page_range("", 20), [])
        self.assertEqual(parse_page_range("abc", 20), [])  # 非法静默跳过


class TestReferences(unittest.TestCase):
    """借鉴自 llmwiki references.py。"""

    def test_citation_filename_with_page(self):
        from wiki.references import parse_citation_filename
        self.assertEqual(parse_citation_filename("Bug分析.md, p.3"), ("Bug分析.md", 3))
        self.assertEqual(parse_citation_filename("Bug分析.md, p3"), ("Bug分析.md", 3))

    def test_citation_filename_no_page(self):
        from wiki.references import parse_citation_filename
        self.assertEqual(parse_citation_filename("paper.pdf"), ("paper.pdf", None))

    def test_parse_citations(self):
        from wiki.references import parse_citations
        content = "text\n[^1]: Bug分析.md, p.3\n[^2]: Dump.md\n"
        cites = parse_citations(content)
        self.assertIn(("Bug分析.md", 3), cites)
        self.assertIn(("Dump.md", None), cites)

    def test_wiki_links(self):
        from wiki.references import parse_wiki_links
        content = "see [foo](./foo.md) and [bar](/wiki/bar.md) and [ext](http://x.com)"
        links = parse_wiki_links(content, "")
        self.assertIn("foo.md", links)
        self.assertNotIn("http://x.com", links)  # 外部跳过


class TestFrontmatter(unittest.TestCase):
    """借鉴自 llmwiki write.py。"""

    def test_parse_frontmatter(self):
        from wiki.frontmatter import parse_frontmatter, has_frontmatter, strip_frontmatter
        content = "---\ntitle: Test\ntags: [a, b]\n---\n\nbody"
        self.assertEqual(parse_frontmatter(content)["title"], "Test")
        self.assertTrue(has_frontmatter(content))
        self.assertEqual(strip_frontmatter(content).strip(), "body")

    def test_no_frontmatter(self):
        from wiki.frontmatter import parse_frontmatter, has_frontmatter
        self.assertEqual(parse_frontmatter("plain body"), {})
        self.assertFalse(has_frontmatter("plain body"))

    def test_ensure_wiki_frontmatter(self):
        from wiki.frontmatter import ensure_wiki_frontmatter, has_frontmatter
        no_fm = "plain body"
        result = ensure_wiki_frontmatter(
            no_fm, "Title", ["t1"], "2026-07-21",
            {"work_item_id": "WID123", "platform": "EC626"}
        )
        self.assertTrue(has_frontmatter(result))
        self.assertIn("WID123", result)
        self.assertIn("EC626", result)
        self.assertIn("plain body", result)

    def test_ensure_skip_if_exists(self):
        from wiki.frontmatter import ensure_wiki_frontmatter
        content = "---\ntitle: Existing\n---\n\nbody"
        result = ensure_wiki_frontmatter(content, "New", ["t"], "2026-07-21")
        self.assertEqual(result, content)  # 已有 frontmatter 不覆盖


class TestChunker(unittest.TestCase):
    def test_empty(self):
        from wiki.chunker import chunk_text
        self.assertEqual(chunk_text(""), [])
        self.assertEqual(chunk_text("   "), [])

    def test_basic_chunking(self):
        from wiki.chunker import chunk_text
        text = "## Section\n\n" + ("para " * 200)
        chunks = chunk_text(text)
        self.assertGreater(len(chunks), 0)
        # 每个 chunk 应该有 breadcrumb
        for c in chunks:
            self.assertIsInstance(c.header_breadcrumb, str)


class TestArchiverEndToEnd(unittest.TestCase):
    """端到端：模拟 .spec/bug/ 多文件条目归档到临时 KNOWLEDGE_ROOT。"""

    def setUp(self):
        # 临时项目目录 + 临时 KNOWLEDGE_ROOT
        self.tmp_project = tempfile.mkdtemp(prefix="archiver_proj_")
        self.tmp_knowledge = tempfile.mkdtemp(prefix="archiver_kb_")

        # Monkey-patch KNOWLEDGE_ROOT（archiver 从 common 模块读）
        import common
        self._original_root = common.KNOWLEDGE_ROOT
        common.KNOWLEDGE_ROOT = self.tmp_knowledge

        # 创建一个多文件 bug 条目
        bug_dir = os.path.join(self.tmp_project, ".spec/bug/6974423486_UDP链路未关闭")
        os.makedirs(bug_dir)
        for filename, content in [
            ("Bug分析.md", "# UDP 链路未关闭 原因分析\n\n## 根因\nXIIC=0 触发死锁"),
            ("Dump分析.md", "# Dump 分析\n\n寄存器: PC=0x1234"),
            ("修改方案.md", "# 修复方案\n\n修改 lwip_close 加超时"),
            ("at.log", "raw log content"),  # 日志不归档
        ]:
            with open(os.path.join(bug_dir, filename), "w", encoding="utf-8") as f:
                f.write(content)
        # logs/ 子目录（应被忽略）
        os.makedirs(os.path.join(bug_dir, "logs"))

    def tearDown(self):
        import common
        common.KNOWLEDGE_ROOT = self._original_root
        shutil.rmtree(self.tmp_project, ignore_errors=True)
        shutil.rmtree(self.tmp_knowledge, ignore_errors=True)

    def test_archive_multi_file_entry(self):
        from wiki.archiver import (
            list_source_entries, archive_multi_file_entry, infer_role,
        )
        from common import load_meta, META_FILE

        source_dir = os.path.join(self.tmp_project, ".spec/bug")
        entries = list_source_entries(source_dir)
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        # 4 个文件中 .log 被排除 → 3 个 .md
        self.assertEqual(len(entry["md_files"]), 3)

        # 归档
        dest_type_dir = os.path.join(self.tmp_knowledge, "raw/platform/EC626/bug-solutions")
        os.makedirs(dest_type_dir, exist_ok=True)
        meta = {"entries": {}}
        result = archive_multi_file_entry(entry, dest_type_dir, "EC626", meta["entries"], "bug")

        self.assertIsNotNone(result)
        entry_name, title, is_new = result
        self.assertEqual(entry_name, "6974423486_UDP链路未关闭")
        self.assertEqual(title, "UDP链路未关闭")
        self.assertTrue(is_new)

        # raw 区应有 3 个 .md（保真拷贝，不合并）
        dest_entry = os.path.join(dest_type_dir, entry_name)
        archived_files = sorted(os.listdir(dest_entry))
        self.assertEqual(archived_files, ["Bug分析.md", "Dump分析.md", "修改方案.md"])

        # meta schema v2
        meta_entry = meta["entries"][entry_name]
        self.assertEqual(meta_entry["schema_version"], 2)
        self.assertEqual(meta_entry["platform"], "EC626")
        self.assertEqual(meta_entry["work_item_id"], "6974423486")
        self.assertEqual(len(meta_entry["files"]), 3)
        # primary_file 应是 bug-analysis 角色
        self.assertEqual(meta_entry["primary_file"], "Bug分析.md")
        # files[] 应有 role
        roles = {f["name"]: f["role"] for f in meta_entry["files"]}
        self.assertEqual(roles["Bug分析.md"], "bug-analysis")
        self.assertEqual(roles["Dump分析.md"], "dump")
        self.assertEqual(roles["修改方案.md"], "solution")

    def test_incremental_skip_unchanged(self):
        """第二次归档相同内容应跳过。"""
        from wiki.archiver import list_source_entries, archive_multi_file_entry
        source_dir = os.path.join(self.tmp_project, ".spec/bug")
        entries = list_source_entries(source_dir)
        dest_type_dir = os.path.join(self.tmp_knowledge, "raw/platform/EC626/bug-solutions")
        os.makedirs(dest_type_dir, exist_ok=True)
        meta = {"entries": {}}

        # 第一次
        archive_multi_file_entry(entries[0], dest_type_dir, "EC626", meta["entries"], "bug")
        # 第二次（相同 hash）
        result = archive_multi_file_entry(entries[0], dest_type_dir, "EC626", meta["entries"], "bug")
        self.assertIsNone(result)  # 无变化 → None

    def test_update_after_content_change(self):
        """修改文件后归档应更新，并清理孤儿文件。"""
        from wiki.archiver import list_source_entries, archive_multi_file_entry
        source_dir = os.path.join(self.tmp_project, ".spec/bug")
        entries = list_source_entries(source_dir)
        dest_type_dir = os.path.join(self.tmp_knowledge, "raw/platform/EC626/bug-solutions")
        os.makedirs(dest_type_dir, exist_ok=True)
        meta = {"entries": {}}

        # 第一次归档
        archive_multi_file_entry(entries[0], dest_type_dir, "EC626", meta["entries"], "bug")

        # 删除"修改方案.md"，加新文件
        bug_dir = entries[0]["path"]
        os.remove(os.path.join(bug_dir, "修改方案.md"))
        with open(os.path.join(bug_dir, "测试报告.md"), "w", encoding="utf-8") as f:
            f.write("# 测试报告\n...")

        # 重新 list + 归档
        entries2 = list_source_entries(source_dir)
        result = archive_multi_file_entry(entries2[0], dest_type_dir, "EC626", meta["entries"], "bug")
        self.assertIsNotNone(result)
        _, _, is_new = result
        self.assertFalse(is_new)  # 更新不是新增

        # 孤儿"修改方案.md"应被清理
        dest_entry = os.path.join(dest_type_dir, entries[0]["name"])
        archived_files = sorted(os.listdir(dest_entry))
        self.assertIn("测试报告.md", archived_files)
        self.assertNotIn("修改方案.md", archived_files)


class TestLint(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lint_test_")
        self.wiki = os.path.join(self.tmp, "wiki")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel_path, content):
        full = os.path.join(self.wiki, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def test_clean_wiki(self):
        from wiki.lint import lint_wiki
        self._write("Home.md",
                    "---\ntitle: Home\ndate: 2026-07-21\ntags: [h]\ntype: home\n---\n\n# Home\n")
        self._write("INDEX.md",
                    "---\ntitle: Idx\ndate: 2026-07-21\ntags: [i]\ntype: index\n---\n\n# Index\n")
        report = lint_wiki(self.wiki)
        self.assertEqual(report.error_count, 0)

    def test_missing_frontmatter(self):
        from wiki.lint import lint_wiki
        self._write("entries/test.md", "# No frontmatter\n")
        report = lint_wiki(self.wiki)
        codes = [i.code for i in report.issues]
        self.assertIn("missing-frontmatter", codes)

    def test_missing_required_field(self):
        from wiki.lint import lint_wiki
        self._write("concepts/x.md",
                    "---\ntitle: X\ndate: 2026-07-21\ntags: [x]\n---\n\n# X\n")
        report = lint_wiki(self.wiki)
        codes = [i.code for i in report.issues]
        self.assertIn("missing-required-field", codes)

    def test_invalid_type(self):
        from wiki.lint import lint_wiki
        self._write("concepts/x.md",
                    "---\ntitle: X\ndate: 2026-07-21\ntags: [x]\ntype: bogus\n---\n\n# X\n")
        report = lint_wiki(self.wiki)
        codes = [i.code for i in report.issues]
        self.assertIn("invalid-type", codes)

    def test_broken_wikilink(self):
        from wiki.lint import lint_wiki
        self._write("concepts/x.md",
                    "---\ntitle: X\ndate: 2026-07-21\ntags: [x]\ntype: concept\n---\n\n"
                    "# X\nsee [[不存在的页]]\n")
        report = lint_wiki(self.wiki)
        codes = [i.code for i in report.issues]
        self.assertIn("broken-link", codes)

    def test_wiki_not_found(self):
        from wiki.lint import lint_wiki
        report = lint_wiki("/nonexistent/wiki/path")
        self.assertEqual(report.error_count, 1)
        self.assertEqual(report.issues[0].code, "wiki-not-found")


class TestGuide(unittest.TestCase):
    def test_guide_text_has_sections(self):
        from wiki.guide import GUIDE_TEXT
        for section in ("目录结构", "Frontmatter", "entries/", "concepts/",
                        "INDEX.md", "Home.md", "维护工作流"):
            self.assertIn(section, GUIDE_TEXT)

    def test_format_archive_trigger(self):
        from wiki.guide import format_archive_trigger
        # 新增场景：应输出完整 SOP
        trigger = format_archive_trigger(
            [{"type": "bug-solutions", "platform": "EC626",
              "name": "6974423486_UDP", "title": "UDP", "files": ["Bug分析.md"],
              "is_new": True}],
            "wiki/guide.py::GUIDE_TEXT"
        )
        self.assertIn("1 新", trigger)
        self.assertIn("EC626/6974423486_UDP", trigger)
        self.assertIn("Bug分析.md", trigger)

    def test_format_archive_trigger_update_only(self):
        """仅更新场景：应输出简化提示。"""
        from wiki.guide import format_archive_trigger
        trigger = format_archive_trigger(
            [{"type": "bug-solutions", "platform": "EC626",
              "name": "6974423486_UDP", "title": "UDP", "files": ["Bug分析.md"],
              "is_new": False}],
            "wiki/guide.py::GUIDE_TEXT"
        )
        self.assertIn("仅内容更新", trigger)
        self.assertIn("EC626/6974423486_UDP", trigger)
        # 不应含完整 SOP
        self.assertNotIn("完整维护指南", trigger)


class TestThinShell(unittest.TestCase):
    """knowledge_archiver.py 薄壳兼容性。"""

    def test_pure_function_preserved(self):
        # ensure_summary_field_row 必须仍可导入（test_archiver 依赖）
        from knowledge_archiver import ensure_summary_field_row
        doc = "## 结构化摘要\n\n| 字段 | 值 |\n|---|---|\n"
        out = ensure_summary_field_row(doc, "工作项 ID", "123")
        self.assertIn("| **工作项 ID** | 123 |", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
