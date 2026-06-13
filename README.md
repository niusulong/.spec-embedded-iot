# Spec Embedded IoT — 嵌入式 IoT 开发技能库 & 知识库

面向嵌入式软件开发的 Claude Code 插件，提供 Bug 根因分析、Crash Dump 分析、代码总结、知识库语义检索等专业技能，按芯片平台组织知识库。

## 安装

在 Claude Code 会话中执行：

```
/plugin marketplace add niusulong/spec_v2
/plugin install spec-embedded-iot@spec-embedded-iot
```

## 技能列表

| 技能 | 触发词 | 说明 |
|------|--------|------|
| `spec-bug-analyzer` | spec 分析bug、spec 诊断问题 | Bug 根因分析：从 AT 命令日志、模块 AP 日志中定位根本原因，支持正常/异常日志对比分析，条件触发知识库历史案例检索 |
| `spec-asr-dump-analyzer` | spec 分析dump、crash dump、死机分析 | ASR 平台 (ARM Cortex-R + ThreadX) Crash Dump 分析：AXF 反汇编、DDR 栈分析、静态栈深度分析、WDT 追踪、堆扫描 |
| `spec-ec-dump-analyzer` | EC dump、EC626崩溃、HardFault | EC 平台 (ARM Cortex-M + FreeRTOS) Crash Dump 分析：excep_store 解析、Fault Status 解码、FreeRTOS TCB 解析、LWIP memp 泄漏检测、DWARF 源码行号映射、objdump 反汇编上下文 |
| `spec-code-summary` | spec 模块实现、spec 代码分析 | 单模块代码实现分析总结，输出结构化代码总结文档 |
| `spec-project-overview` | spec 项目概览、spec 了解项目 | 项目概览文档生成：目录结构映射、模块清单、技术栈识别、构建系统分析 |
| `spec-init` | spec 初始化、spec 准备环境 | `.spec` 工作流环境初始化，创建基础目录结构 |
| `spec-knowledge-archiver` | 归档bug、同步知识库、archive bug | 文档归档到持久化知识库，支持批量归档、向量索引更新、完整性校验 |
| `spec-neoway-coding-standards` | spec 编码规范、spec 代码规范 | Neoway 嵌入式 C 语言编码规范查询：编码风格、命名规范、注释规范 |
| `spec-requirement-generator` | spec 整理需求、spec 生成需求文档 | 零散需求（口头描述、会议记录）→ 结构化需求文档 |
| `spec-requirement-splitter` | spec 拆分需求、拆分需求 | 大需求按功能模块拆分为小需求单元，生成拆分清单 |
| `esafenet-file-io` | esafenet、加密文件、绿盾 | EsafeNet（天锐绿盾）加密文件透明读写（仅 Windows） |
| `skill-creator` | 创建技能、create skill | 技能创建指南（元技能） |

## 知识库

知识库按芯片平台组织，存储在 `knowledge/platform/{平台名}/` 下：

```
knowledge/
└── platform/
    ├── EC626/
    │   ├── 项目概览.md
    │   ├── code-summary/
    │   │   ├── AT命令模块/代码总结.md
    │   │   ├── MQTT模块/代码总结.md
    │   │   ├── CoAP模块/代码总结.md
    │   │   └── ...
    │   └── bug-solutions/
    └── UIS8852/
        └── code-summary/
```

| 内容 | 路径 | 来源 |
|------|------|------|
| 项目概览 | `{平台}/项目概览.md` | `spec-project-overview` 生成 |
| 代码总结 | `{平台}/code-summary/{模块}/代码总结.md` | `spec-code-summary` 生成 |
| Bug 解决方案 | `{平台}/bug-solutions/` | `spec-knowledge-archiver` 归档 |
| 向量索引 | `knowledge/vector_db/` | ChromaDB 语义检索 |

**知识库搜索**：

```bash
python ~/.agents/skills/spec-knowledge-archiver/scripts/embed_search.py "{关键词}" --platform {平台} --top 5
```

## 项目结构

```
.spec/                          # 项目工作目录（需 spec-init 创建）
├── logs/                       # 日志文件
├── bug/{工作项ID}_{描述}/       # Bug 分析报告和日志归档
├── code-summary/{模块}/        # 代码总结输出
└── requirement/                # 需求文档

.claude-plugin/                 # Claude Code 插件描述
.codex-plugin/                  # OpenAI Codex 插件描述
.cursor-plugin/                 # Cursor 插件描述
skills/                         # 技能定义（每个技能一个目录）
knowledge/                      # 跨项目持久化知识库
```

## 典型工作流

### Bug 分析

```
1. spec 初始化              → 创建 .spec 目录结构
2. 将日志放入 .spec/logs/
3. spec 分析bug              → 日志分析 → 条件知识库检索 → 根因定位 → 生成报告
4. 归档bug                   → 归档到知识库，更新向量索引
```

### 项目接入

```
1. spec 了解项目             → 生成项目概览文档
2. spec 模块实现              → 逐模块生成代码总结
3. 同步知识库                 → 归档到持久化知识库
```

## 许可证

Apache-2.0
