# Agents — 嵌入式 IoT 开发技能库 & 知识库

本插件提供嵌入式软件开发的专业技能和持久化知识库，按芯片平台组织。

## 知识库

路径：`~/.spec-embedded-iot/knowledge/platform/{平台名}/`

| 内容 | 路径 | 说明 |
|------|------|------|
| 项目概览 | `{平台}/项目概览.md` | 由 spec-project-overview 生成 |
| 代码总结 | `{平台}/code-summary/{模块}/代码总结.md` | 由 spec-code-summary 生成 |
| Bug 解决方案 | `{平台}/bug-solutions/` | 由 spec-knowledge-archiver 归档 |
| 向量索引 | `~/.spec-embedded-iot/knowledge/vector_db/` | ChromaDB 语义检索 |

**当前平台**：从项目路径自动推断（如 `D:\EC626\` → `EC626`），也可手动指定。

### 知识库搜索

```bash
python skills/spec-knowledge-archiver/scripts/embed_search.py "{关键词}" --platform {平台} --top 5
```

## 可用技能

| 技能 | 触发词 | 说明 |
|------|--------|------|
| `spec-bug-analyzer` | spec 分析bug、spec 诊断问题、根因分析、定位问题、帮我看看日志、设备/模组异常 | Bug 根因分析（日志+知识库检索，怀疑内存泄漏时转交 spec-memory-leak-analyzer） |
| `spec-asr1603-dump-analyzer` | spec 分析dump、crash dump、死机分析 | ASR 平台 crash dump 分析（Cortex-R + ThreadX） |
| `spec-ec-dump-analyzer` | EC dump、EC626崩溃、HardFault | EC 平台 crash dump 分析（Cortex-M + FreeRTOS） |
| `spec-memory-leak-analyzer` | 分析内存泄漏、内存只增不减、free heap 一直掉、memory leak | 内存泄漏定位（call-stack 追踪：埋点记录 caller 地址，配对找泄漏点，MAP 映射到源码） |
| `spec-code-summary` | spec 模块实现、spec 代码分析 | 单模块代码实现分析总结 |
| `spec-project-overview` | spec 项目概览、spec 了解项目 | 项目概览文档生成 |
| `spec-init` | spec 初始化、spec 准备环境 | .spec 工作流环境初始化 |
| `spec-knowledge-archiver` | 归档bug、同步知识库、archive bug | 文档归档到持久化知识库 |
| `spec-neoway-coding-standards` | spec 编码规范、spec 代码规范 | Neoway C 编码规范查询 |
| `spec-requirement-generator` | spec 整理需求、spec 生成需求文档 | 零散需求 → 结构化需求文档 |
| `spec-requirement-splitter` | spec 拆分需求、拆分需求 | 大需求拆分为小单元 |
| `spec-solution-designer` | spec 设计方案、spec 技术方案、spec 出方案、技术选型 | 需求 → 嵌入式技术方案（架构/RTOS任务/资源预算/协议栈兼容/接口/风险） |
| `spec-implementation-planner` | spec 实施计划、spec 编写计划、spec 排期、spec 任务拆解 | 技术方案 → 委托 superpowers:writing-plans 产出代码级可执行计划（编码规范合规 + spec 路径 + 去 TDD 改嵌入式验证） |
| `skill-creator` | 创建技能、create skill | 技能创建指南（元技能） |
| `esafenet-file-io` | esafenet、加密文件、绿盾 | EsafeNet 加密文件透明读写（仅 Windows） |

## 技能调用规则

1. 用户请求匹配触发词时，**必须**调用对应技能
2. 知识库搜索优先使用向量语义检索（embed_search.py）
3. Bug 分析流程自动检索知识库历史案例
4. 分析完成后可归档到知识库（spec-knowledge-archiver）

## 工作目录约定

| 目录 | 用途 |
|------|------|
| `.spec/logs/` | 项目日志文件 |
| `.spec/bug/{工作项ID}_{描述}/` | Bug 分析报告和日志归档 |
| `.spec/requirement/{项目ID}_{核心功能}/` | 需求(需求.md)、技术方案(方案.md)、实施计划(计划.md) 同目录并排 |
| `~/.spec-embedded-iot/knowledge/` | 跨项目持久化知识库 |
