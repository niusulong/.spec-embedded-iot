"""spec-embedded-iot LLM-Wiki 子包。

架构（借鉴 Karpathy LLM-Wiki 理念 + lucasastorian/llmwiki 实现）：
  - archiver.py:    多文件保真归档（条目=目录）
  - guide.py:       wiki 维护 SOP 提示词（语境化）
  - lint.py:        wiki 一致性检查
  - frontmatter.py: YAML frontmatter 解析（借鉴 llmwiki write.py）
  - references.py:  引用/链接解析（借鉴 llmwiki references.py）
  - helpers.py:     纯工具（借鉴 llmwiki helpers.py）
  - chunker.py:     文本分块（借鉴 llmwiki chunker.py，lint 用）

详见 SKILL.md 与各模块 docstring。
"""
