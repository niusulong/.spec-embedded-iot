# 中央知识库

跨项目持久化的知识文档，按芯片平台组织，独立于任何代码仓库。

## 目录结构

```
~/.agents/knowledge/
├── platform/
│   └── {平台名}/                       # 如 EC626、ASR1603、UIS8852
│       ├── 项目概览.md                 # 由 spec-project-overview 生成
│       ├── bug-solutions/              # Bug 解决方案（由 spec-knowledge-archiver 归档）
│       │   ├── index.md
│       │   ├── .archive_meta.json
│       │   └── {bug标题}.md
│       ├── requirement-solutions/      # 需求方案（由 spec-knowledge-archiver 归档）
│       ├── code-summary/               # 模块代码总结（由 spec-code-summary 生成）
│       │   └── {模块名}/代码总结.md
│       ├── official-docs/              # 平台原生官方文档原始文件（PDF/HTML/Excel）
│       │   └── cn/ en/ EC626_Driver_API_Reference_Manual/
│       └── official-docs-md/           # 官方文档 .md 转换版（供检索）
│           └── cn/ en/ EC626_Driver_API_Reference_Manual/
├── protocols/                          # 通用协议官方资料（跨平台）
│   ├── uart/ spi/ i2c/                 # 硬件通信协议
│   └── mqtt/ tcp/ http/ ftp/           # 网络协议
├── vector_db/                          # ChromaDB 向量索引（语义检索，**不入 git**）
├── knowledge_config.json               # 文档类型与 collection 配置
└── README.md                           # 本文件
```

## 知识类型与管理者

| 位置 | 内容 | 管理者 | 索引策略 |
|------|------|--------|---------|
| `platform/{平台}/bug-solutions/` | 某平台具体 bug 及根因 | `spec-knowledge-archiver` | 结构化摘要向量化 |
| `platform/{平台}/requirement-solutions/` | 某平台需求方案 | `spec-knowledge-archiver` | 结构化摘要向量化 |
| `platform/{平台}/code-summary/` | 某平台模块代码实现总结 | `spec-code-summary` | Markdown 分块向量化 |
| `platform/{平台}/official-docs/` | 平台官方文档原始文件 | 手动/工具归档 | 不索引（保留原始文件） |
| `platform/{平台}/official-docs-md/` | 官方文档 .md 转换版 | 用户提供转换工具 | Markdown 分块向量化 |
| `platform/{平台}/项目概览.md` | 项目整体概览 | `spec-project-overview` | Markdown 分块向量化 |
| `protocols/{协议}/` | 协议通用规范 | 手动/工具归档 | Markdown 分块向量化 |
| 项目 `.spec/` | 项目级工作数据（bug、需求、日志） | 各项目自身 | 不索引 |

## 与项目 .spec 目录的关系

`~/.agents/knowledge/` 是跨项目持久化的，不随项目删除；项目 `.spec/` 是项目级工作数据。

## 使用规则

- 平台目录和子目录由各技能按需创建
- 平台名由用户提供或从项目路径推断
- 文档间使用相对路径引用
- 同名文档更新时覆盖旧版本
- `vector_db/` 是生成产物，不纳入 git 管理

## 知识库搜索

```bash
# 默认跨所有 collection 搜索
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "MQTT QoS"

# 限定某个 collection
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "MQTT QoS" --collection protocols
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "AT命令" --collection code-summary

# 限定平台
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "MQTT 连接失败" --collection bug-solutions --platform EC626

# 查看索引状态
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_indexer.py status
```

## 配置文件

`knowledge_config.json` 定义：
- `doc_types`: 归档脚本使用的文档类型
- `collections`: 向量索引的 collection 配置（索引策略、源文件路径、分块大小）

新增知识类型时，只需在 `collections` 中增加条目，索引和搜索即可自动生效。
