#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_profile.py 单元测试。运行：python -m pytest test_check_profile.py -q"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import pytest
from check_profile import (
    check_profile,
    parse_sections,
    check_generic_tokens,
)


VALID_BODY = (
    "## 根注解\nARM Cortex-M + FreeRTOS.\n\n"
    "## 代码地图\n业务逻辑在 components/app/.\n\n"
    "## 检索清单\n`memp_malloc` | `trace_node` | `ASSERT`\n"
)


def _errors(violations):
    return [m for lv, m in violations if lv == "error"]


def test_parse_sections():
    s = parse_sections("# Title\n## A\na1\n## B\nb1\n")
    assert s == {"A": "a1", "B": "b1"}


def test_parse_sections_ignores_h3():
    s = parse_sections("## A\na1\n### sub\ndeep\n")
    assert "sub" not in s
    assert "deep" in s["A"]


def test_valid_profile_passes(tmp_path):
    kb = tmp_path / "platform"
    (kb / "EC626").mkdir(parents=True)
    assert _errors(check_profile("EC626", VALID_BODY, str(kb))) == []


def test_missing_required_section(tmp_path):
    v = check_profile("EC626", "## 检索清单\n`a`\n", None)
    assert any("代码地图" in m for m in _errors(v))


def test_required_section_ok_with_comment_only(tmp_path):
    """代码地图段即使只有注释占位（空框架）也合规——段存在即合法声明。"""
    body = "## 代码地图\n> 暂无积累，待补充\n"
    v = check_profile("EC626", body, None)
    assert _errors(v) == []


def test_generic_token_in_checklist(tmp_path):
    body = "## 代码地图\nx\n## 检索清单\n`ERROR` | `fail` | `timeout`\n"
    v = check_profile("EC626", body, None)
    errs = _errors(v)
    assert any("通用串" in m for m in errs)
    assert "ERROR" in "".join(errs)


def test_generic_token_clean(tmp_path):
    body = "## 代码地图\nx\n## 检索清单\n`memp_malloc` | `dlmalloc` | `ASSERT`\n"
    assert not any("通用串" in m for m in _errors(check_profile("EC626", body, None)))


def test_wdtimeout_not_flagged(tmp_path):
    """词边界匹配：WdTimeout 是合法平台串，不应被 timeout 黑名单误伤。"""
    assert check_generic_tokens("`WdTimeout` | `tx_thread`") == []


def test_chinese_generic_token(tmp_path):
    body = "## 代码地图\nx\n## 检索清单\n`断连` | `重连`\n"
    v = check_profile("EC626", body, None)
    assert any("通用串" in m for m in _errors(v))


def test_unknown_section_is_warning(tmp_path):
    body = "## 代码地图\nx\n## 某未知段\ny\n"
    v = check_profile("EC626", body, None)
    assert any(lv == "warn" and "未知段" in m for lv, m in v)
    assert _errors(v) == []  # 未知段不致命


def test_three_name_mismatch(tmp_path):
    kb = tmp_path / "platform"
    (kb / "EC626").mkdir(parents=True)
    body = "## 代码地图\nx\n"
    v = check_profile("WRONG_NAME", body, str(kb))
    assert any("三名不一致" in m for m in _errors(v))


def test_three_name_skipped_without_kb(tmp_path):
    """无知识库目录时不校验三名一致（不报 error）。"""
    body = "## 代码地图\nx\n"
    v = check_profile("EC626", body, None)
    assert not any("三名" in m for lv, m in v if lv == "error")
