---
name: spec-knowledge-archiver
description: >
  项目文档归档与知识库向量索引工具。将项目 .spec/bug 和 .spec/requirement 目录下的分析文档归档到
  持久化知识库 (~/.agents/knowledge/platform/{平台}/)。支持单个归档、批量归档、
  增量归档、自动合并多文件、生成索引、自动更新向量索引、完整性校验。同时支持对
  code-summary、official-docs-md、project-overview、protocols 等多种知识类型构建
  向量索引与统一语义检索。按芯片平台路径隔离。
  当用户说"归档bug"、"归档文档"、"同步知识库"、"archive bug"、"知识库归档"、
  "搜索知识库"、"向量检索"时使用。
---

# Spec Knowledge Archiver

将项目 `.spec/` 下的 bug 分析文档和需求解决方案归档到持久化知识库，并提供多类型知识的统一向量检索。脚本目录: `scripts/`

## 归档命令

**1. 批量归档所有条目**

```bash
python scripts/knowledge_archiver.py archive --project {项目路径} --type bug --all
```

**2. 增量归档（仅新增/变更的条目）**

```bash
python scripts/knowledge_archiver.py archive --project {项目路径} --type bug --incremental
```

**3. 归档指定条目（支持部分匹配）**

```bash
python scripts/knowledge_archiver.py archive --project {项目路径} --type bug --name "COAP"
```

**4. 查看归档状态**

```bash
python scripts/knowledge_archiver.py status --project {项目路径}
```

**5. 校验知识库完整性**

```bash
python scripts/knowledge_archiver.py verify --project {项目路径} --type bug
```

**6. 手动指定平台名**

```bash
python scripts/knowledge_archiver.py archive --project {项目路径} --platform EC626 --type bug --all
```

## 向量索引命令

**7. 构建全量索引（所有 collection）**

```bash
python scripts/embed_indexer.py build
```

**8. 构建指定 collection**

```bash
python scripts/embed_indexer.py build --collection protocols
python scripts/embed_indexer.py build --collection code-summary
```

**9. 增量更新向量索引**

```bash
python scripts/embed_indexer.py update
```

**10. 查看索引状态**

```bash
python scripts/embed_indexer.py status
```

## 搜索命令

**11. 跨 collection 搜索（默认）**

```bash
python scripts/embed_search.py "MQTT QoS"
```

**12. 限定 collection**

```bash
python scripts/embed_search.py "MQTT QoS" --collection protocols
python scripts/embed_search.py "AT命令" --collection code-summary
python scripts/embed_search.py "LWIP 内存泄漏" --collection bug-solutions
```

**13. 限定平台**

```bash
python scripts/embed_search.py "MQTT 连接失败" --collection bug-solutions --platform EC626
```

**14. JSON 输出**

```bash
python scripts/embed_search.py "死机" --top 10 --json
```

## 归档参数说明

| 参数 | 说明 |
|------|------|
| `--project` | 项目根目录路径（如 `D:/EC626`） |
| `--platform` | 平台名，默认从项目路径推断 |
| `--type` | 文档类型：`bug` 或 `requirement`（见配置文件） |
| `--name` | 指定条目名称，支持部分匹配 |
| `--all` | 归档所有条目 |
| `--incremental` | 仅归档新增或内容变更的条目 |
| `--no-vector` | 跳过自动更新向量索引（仅 archive 命令） |

## 搜索/索引参数说明

| 参数 | 说明 |
|------|------|
| `--collection` | collection 名称：`bug-solutions`、`requirement-solutions`、`code-summary`、`official-docs-md`、`project-overview`、`protocols` |
| `--type` | 同 `--collection`（兼容旧参数） |
| `--platform` | 限定平台（如 `EC626`） |
| `--top` | 返回结果数量（默认 5） |
| `--json` | JSON 格式输出 |

## 配置文件

路径: `~/.agents/knowledge/knowledge_config.json`

配置包含：
- `doc_types`: 归档脚本使用的文档类型（源目录、目标目录、索引列、摘要字段）
- `collections`: 向量索引的 collection 配置（索引策略、源文件路径、分块参数）

新增知识类型时，只需在 `collections` 中增加条目，索引和搜索即可自动生效，无需改代码。

### 索引策略

| 策略 | 适用场景 |
|------|---------|
| `summary` | 小文档 + 结构化摘要，如 bug-solutions、requirement-solutions |
| `markdown_chunks` | 长文档，如 code-summary、official-docs-md、protocols、project-overview |

## 向量索引

归档 bug/requirement 后**自动**调用 `embed_indexer.py update` 更新向量索引（chromadb + paraphrase-multilingual-MiniLM-L12-v2）。使用 `--no-vector` 可跳过。首次使用需下载模型（约 450MB）。

## 完整性校验

`verify` 命令检查：
- meta 中的条目是否有对应 .md 文件
- 目录中的 .md 文件是否有 meta 记录
- 源目录已删除的条目是否已清理
- 向量索引与 meta 是否同步

## 工作流

1. 运行 `status` 查看当前归档状态
2. 根据场景选择 `--all`、`--incremental` 或 `--name` 执行归档
3. 归档完成后可运行 `verify` 校验一致性
4. 运行 `embed_indexer.py update` 或 `build` 更新所有 collection 的向量索引
5. 使用 `embed_search.py` 进行跨 collection 语义检索
6. 向用户报告归档/搜索结果（新增/更新/清理数量）
