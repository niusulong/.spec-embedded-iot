---
name: spec-knowledge-archiver
description: >
  项目文档归档与 LLM-Wiki 维护工具。把项目 .spec/ 下的 bug 分析、需求方案、代码总结等
  原文保真归档到持久知识库（platform/{平台}/raw/），并引导 agent 按 LLM-Wiki 理念维护
  全局 wiki/（entries 精炼页 + concepts 跨案例综合）。当用户说"归档bug"、"归档文档"、
  "同步知识库"、"archive"、"搜索知识库"、"维护wiki"、"lint wiki"时使用。
---

# Spec Knowledge Archiver（LLM-Wiki 路线）

把项目 `.spec/` 下的文档**原文保真**归档到持久知识库的 raw 区，并按 [Karpathy LLM-Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) 理念维护全局 wiki。

> **设计原则**：
> - **归档是单向、永久的**。源是临时输入，KB 是持久产物；归档器只做"新增/更新"，不删除。
> - **raw 区绝对保真**（取证需要：bug 报告的寄存器/栈/AT 时序原值不可改写）。
> - **wiki 区是 LLM 维护的精炼层**（entries 单条目精炼 + concepts 跨案例综合）。
> - **检索靠 agent 读 wiki/INDEX.md 渐进加载**，不使用向量数据库。

## 目录结构（知识库布局）

```
~/.spec-embedded-iot/knowledge/
├── raw/                                 # ★ 统一 raw 根（所有原文，只读，归档器写）
│   ├── platform/{平台}/                 # 平台特定原文
│   │   ├── bug-solutions/{wid}_{desc}/  # 1 个 bug = 1 个目录（多文件保真）
│   │   │   ├── Bug分析.md
│   │   │   ├── Dump分析.md
│   │   │   └── 修改方案.md
│   │   ├── code-summary/{模块名}/代码总结.md
│   │   ├── requirement-solutions/{id}_{desc}/
│   │   ├── official-docs-md/...
│   │   └── 项目概览.md
│   └── protocols/{协议名}/...            # 全局协议原文（跨平台共享）
└── wiki/                                # ★ 全局唯一 wiki（跨平台、跨类型综合）
    ├── Home.md                          # 知识库入口
    ├── INDEX.md                         # 全部条目轻量目录（检索入口）
    ├── entries/                         # 单条目精炼页
    │   ├── bug-solutions/{平台}_{wid}_{desc}.md
    │   ├── code-summary/{平台}_{模块名}.md
    │   └── ...
    └── concepts/                        # 跨案例/跨平台概念页（LLM-Wiki 灵魂）
        ├── MQTT连接失败.md
        ├── 内存泄漏排查方法论.md
        └── ...
```

## 统一 CLI：kb.py

```bash
# 归档（5 类知识统一入口）
python scripts/kb.py archive --project {项目} --type bug --all           # 全量归档 bug
python scripts/kb.py archive --project {项目} --type all --incremental   # 增量归档所有类型
python scripts/kb.py archive --project {项目} --type bug --name "COAP"   # 指定条目

# 校验 raw 区完整性
python scripts/kb.py verify --project {项目} --type bug

# wiki 维护工具
python scripts/kb.py wiki lint                  # 检查 wiki 一致性（frontmatter/链接/孤儿）
python scripts/kb.py wiki status                # wiki 覆盖率统计
python scripts/kb.py wiki guide                 # 打印完整 wiki 维护指南

# 知识库状态
python scripts/kb.py status
```

### 归档类型

| `--type` | 源路径 | 目标 raw 路径 | 说明 |
|---------|--------|--------------|------|
| `bug` | `.spec/bug/{wid}_{desc}/*.md` | `raw/bug-solutions/{wid}_{desc}/*.md` | 多文件条目保真 |
| `requirement` | `.spec/requirement/{id}_{desc}/*.md` | `raw/requirement-solutions/{id}_{desc}/*.md` | 多文件条目保真 |
| `code-summary` | `.spec/code-summary/{模块}/代码总结.md` | `raw/code-summary/{模块}/代码总结.md` | 模块=目录 |
| `project-overview` | `.spec/项目概览.md` | `raw/项目概览.md` | 单文件 |
| `all` | 以上全部 | | 按类型分别归档 |

归档后脚本会在 **stdout 输出"给 agent 的 wiki 维护提示词"**（包含新归档条目清单 + 维护步骤）。agent 读到后应按 `wiki/guide.py` 维护 wiki（生成 entries/ 精炼页、更新 INDEX.md、检查 concepts/）。

## 检索工作流（agent 用渐进加载，无向量）

**不使用向量检索**。按需读 markdown 文件：

1. **[轻]** 读全局目录：`Read ~/.spec-embedded-iot/knowledge/wiki/INDEX.md`
   → 锁定 2-3 个候选条目（按一行摘要判断）

2. **[中]** 读候选导读：`Read wiki/entries/{type}/{平台}_{标识}.md`
   → 锁定最相关条目，决定要看原文的哪个章节

3. **[重]** 读原文证据：`Read raw/platform/{平台}/{type}/{wid}_{desc}/Bug分析.md`
   → 获取详细证据（取证保真）

4. **[可选]** 跨案例综合：`Read wiki/concepts/{概念名}.md`
   → 看是否有同类问题的通用模式

## 设计理念（LLM-Wiki 三层抽象）

本技能实现 [Karpathy LLM-Wiki 理念](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)，设计纲领见 [`~/.spec-embedded-iot/knowledge/schema.md`](../../knowledge/schema.md)。三层架构：

| 层 | 路径 | 职责 | 谁写 |
|----|------|------|------|
| **Raw（原始层）** | `raw/platform/{平台}/` | 原文保真，**只读** | kb.py archive 自动拷贝 |
| **Wiki（编译层）** | `wiki/entries/`、`wiki/concepts/` | LLM 精炼的摘要/综合 | agent（LLM）维护 |
| **Schema（规范层）** | `schema.md`、`purpose.md` | wiki 结构约定 + 维护流程 | 人 + LLM 协同 |

三操作：**Ingest（归档）** / **Query（检索）** / **Lint（健康检查）**。详见 schema.md。

不引入任何外部 wiki 工具的代码（nashsu/llm_wiki 是桌面 GUI、Pratiyush/llm-wiki 是独立 pip 包 —— 都不契合我们"agent 原生 + 纯 Python + 无 GUI"的场景）。只借鉴理念文档 [llm-wiki.md](https://github.com/nashsu/llm_wiki/blob/main/llm-wiki.md) 的抽象。

## wiki 维护（agent 职责）

`kb.py archive` 归档后，agent 应按 `wiki/guide.py` 的 GUIDE_TEXT 维护 wiki（`kb.py` 会自动追加 `wiki/log.md` 操作日志）：

- **entries/ 精炼页**：为每个新条目生成一个精炼页（frontmatter + 一句话根因 + 调用链 + 证据链接 + 相关概念）。**不复制原文**，只做精炼和引用。
- **INDEX.md**：追加新条目的一行摘要。保持精简（一行一条），让 agent 一屏读完。
- **concepts/ 概念页**（LLM-Wiki 灵魂）：当 2+ 案例涉及同一根因模式时，创建/更新概念页。综合"共同根因 + 差异化表现 + 通用排查"——这是向量检索做不到的。
- **log.md**：kb.py archive 自动写入，无需 agent 维护。重大重构/schema 演进时 agent 可手动追加。

完整维护指南：`python scripts/kb.py wiki guide`

## 配置

`~/.spec-embedded-iot/knowledge/knowledge_config.json` 定义 5 类知识的源/目标目录映射、角色识别规则。新增文档类型只需改配置 + 在 `wiki/archiver.py` 加归档逻辑。

## 工作流

1. `kb.py status` 查看当前知识库规模
2. `kb.py archive --project {项目} --type all --incremental` 增量归档（自动追加 log.md）
3. **agent 读 stdout 的 wiki 维护提示词**，按 GUIDE_TEXT 维护 wiki/（生成 entries + 更新 INDEX + 检查 concepts）
4. `kb.py wiki lint` 校验 wiki 一致性
5. `kb.py verify --project {项目} --type bug` 校验 raw 区完整性

## 向后兼容

- 旧 `knowledge_archiver.py` 保留为薄壳代理（打印 DeprecationWarning，转发到 kb.py）
- 旧平铺 `.md` 归档（schema v1）保持原样，verify 仍能校验通过
- 向量检索（chromadb）已废弃并删除，老 `vector_db/` 数据保留 30 天供回滚
- `requirements.txt` 已移除 chromadb / sentence-transformers 依赖
