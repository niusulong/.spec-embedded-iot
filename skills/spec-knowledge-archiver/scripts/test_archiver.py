#!/usr/bin/env python3
"""spec-knowledge-archiver 纯逻辑单元测试（unittest，无第三方/模型依赖）。

覆盖：chunk_markdown / build_summary_text / 表格解析 / safe_filename /
extract_*id/title / ensure_summary_field_row / _deep_merge / _validate_config。

运行：
    python -m unittest test_archiver -v
    # 或直接
    python test_archiver.py
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import common
from common import chunk_markdown, build_summary_text, _deep_merge, _validate_config, safe_filename
from extract_summary import _parse_table_rows, DEFAULT_FIELD_MAP, DEFAULT_LIST_FIELDS
from knowledge_archiver import ensure_summary_field_row, extract_title, extract_work_item_id


class TestChunkMarkdown(unittest.TestCase):
    def test_empty_returns_empty(self):
        self.assertEqual(chunk_markdown(""), [])
        self.assertEqual(chunk_markdown("   \n  \t "), [])

    def test_no_heading_only_garbage_chunk(self):
        # 标题 + 单个超大正文段：修复前会产出 "## MySection" 独立垃圾块
        big = "## MySection\n\n" + ("B" * 1500)
        chunks = chunk_markdown(big, chunk_size=500, chunk_overlap=80)
        texts = [c[0] for c in chunks]
        self.assertFalse(any(t.strip() == "## MySection" for t in texts),
                         f"发现标题独立垃圾块: {texts}")
        # 标题应附着到含真实内容的块
        self.assertTrue(any("MySection" in t and "B" * 100 in t for t in texts))
        # 每个块都含真实内容
        self.assertTrue(all("B" * 50 in t for t in texts))

    def test_multi_para(self):
        multi = "## Sec\n\n" + "\n\n".join(("para%d " % i) * 120 for i in range(6))
        chunks = chunk_markdown(multi, chunk_size=400, chunk_overlap=60)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(t.strip() for t, _ in chunks))

    def test_small_doc_one_chunk(self):
        self.assertEqual(len(chunk_markdown("## T\n\nshort body", chunk_size=1500)), 1)


class TestBuildSummaryText(unittest.TestCase):
    def test_joins_non_empty(self):
        cfg = {"summary_fields": {"模块": "module", "根因概述": "root_cause"}}
        s = build_summary_text({"module": "MQTT", "root_cause": "x"}, cfg)
        self.assertIn("模块: MQTT", s)
        self.assertIn("根因概述: x", s)

    def test_list_field_joined(self):
        s = build_summary_text({"keywords": ["a", "b"]}, {"summary_fields": {"k": "keywords"}})
        self.assertIn("a, b", s)

    def test_empty_returns_empty(self):
        self.assertEqual(build_summary_text({}, {"summary_fields": {"x": "y"}}), "")
        self.assertEqual(build_summary_text(None, None), "")

    def test_fallback_without_config(self):
        # doc_type_config 为 None 时用 summary 自身 key 构建
        s = build_summary_text({"module": "MQTT"}, None)
        self.assertIn("module: MQTT", s)


class TestParseTableRows(unittest.TestCase):
    def parse(self, text):
        return _parse_table_rows(text, DEFAULT_FIELD_MAP, DEFAULT_LIST_FIELDS)

    def test_two_col_basic(self):
        self.assertEqual(self.parse("| 模块 | LWIP |").get("module"), "LWIP")

    def test_two_col_bold_field(self):
        self.assertEqual(self.parse("| **模块** | LWIP |").get("module"), "LWIP")

    def test_three_col_not_corrupted(self):
        r = self.parse("| 模块 | LWIP | 备注 |")
        v = r.get("module")
        self.assertTrue(v is None or "|" not in v,
                        f"3 列表格被污染: {v!r}")

    def test_list_field(self):
        self.assertEqual(self.parse("| 症状关键词 | a, b, c |").get("symptoms"),
                         ["a", "b", "c"])

    def test_separator_skipped(self):
        self.assertEqual(self.parse("|---|---|"), {})


class TestFilenameAndIds(unittest.TestCase):
    def test_safe_filename_special_chars(self):
        self.assertEqual(safe_filename('a/b:c*d'), "a_b_c_d.md")

    def test_safe_filename_platform_prefix(self):
        self.assertEqual(safe_filename("COAP死机", "EC626"), "EC626_COAP死机.md")

    def test_extract_work_item_id(self):
        self.assertEqual(extract_work_item_id("6977185133_TCP死机"), "6977185133")
        self.assertIsNone(extract_work_item_id("TCP死机"))

    def test_extract_title(self):
        self.assertEqual(extract_title("6977185133_TCP死机"), "TCP死机")
        self.assertEqual(extract_title("TCP死机"), "TCP死机")


class TestEnsureSummaryField(unittest.TestCase):
    def test_normal_heading_inject(self):
        doc = "## 结构化摘要\n\n| 字段 | 值 |\n|---|---|\n"
        out = ensure_summary_field_row(doc, "工作项 ID", "6977185133")
        self.assertIn("| **工作项 ID** | 6977185133 |", out)

    def test_suffixed_heading_inject(self):
        # 带后缀标题：修复 ensure 的 \s*$ 严格锚点后应能注入
        doc = "## 0. 结构化摘要（专项）\n\n| 字段 | 值 |\n|---|---|\n\n正文\n"
        out = ensure_summary_field_row(doc, "工作项 ID", "NA")
        self.assertIn("| **工作项 ID** | NA |", out)

    def test_idempotent(self):
        doc = "## 结构化摘要\n\n| 工作项 ID | X |\n|---|---|\n"
        out = ensure_summary_field_row(doc, "工作项 ID", "NA")
        self.assertEqual(out.count("工作项 ID"), 1)

    def test_no_table_unchanged(self):
        doc = "## 结构化摘要\n\n纯文字无表格\n"
        self.assertEqual(ensure_summary_field_row(doc, "工作项 ID", "NA"), doc)


class TestConfigUtils(unittest.TestCase):
    def test_deep_merge(self):
        base = {"a": 1, "nested": {"x": 1, "y": 2}}
        over = {"a": 9, "nested": {"y": 99, "z": 3}}
        m = _deep_merge(base, over)
        self.assertEqual(m, {"a": 9, "nested": {"x": 1, "y": 99, "z": 3}})
        self.assertEqual(base["nested"]["y"], 2)  # 原对象未被污染

    def test_validate_config_warns_orphan(self):
        import io
        import contextlib
        bad = {"doc_types": {"bug": {"dest_dir": "bug-solutions", "source_dir": "bug"}},
               "collections": {"bug-solutions": {"strategy": "summary"},
                               "orphan": {"strategy": "summary"}}}
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            _validate_config(bad)
        self.assertIn("orphan", buf.getvalue())

    def test_load_config_deepcopy_isolation(self):
        c1 = common.load_config()
        c1["doc_types"]["bug"]["dest_dir"] = "POLLUTED"
        c2 = common.load_config()
        self.assertEqual(c2["doc_types"]["bug"]["dest_dir"], "bug-solutions")


if __name__ == "__main__":
    unittest.main(verbosity=2)
