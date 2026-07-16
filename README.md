# Spec Embedded IoT — 嵌入式 IoT 开发技能库 & 知识库

> 仓库：https://github.com/niusulong/.spec-embedded-iot

面向嵌入式软件开发的 Claude Code 插件，提供 Bug 根因分析、Crash Dump 分析、代码总结，以及"需求→方案→实施计划"完整需求实现链路，配合跨项目知识库语义检索等专业技能，按芯片平台组织知识库。

## 安装

在 Claude Code 会话中执行：

```
/plugin marketplace add niusulong/.spec-embedded-iot
/plugin install spec-embedded-iot@spec-embedded-iot
```

## 技能列表

| 技能 | 触发词 | 说明 |
|------|--------|------|
| `spec-bug-analyzer` | spec 分析bug、spec 诊断问题 | Bug 根因分析：从 AT 命令日志、模块 AP 日志中定位根本原因，支持正常/异常日志对比分析，条件触发知识库历史案例检索 |
| `spec-asr1603-dump-analyzer` | spec 分析dump、crash dump、死机分析 | ASR 平台 (ARM Cortex-R + ThreadX) Crash Dump 分析：AXF 反汇编、DDR 栈分析、静态栈深度分析、WDT 追踪、堆扫描 |
| `spec-ec626-dump-analyzer` | EC dump、EC626崩溃、HardFault | EC 平台 (ARM Cortex-M + FreeRTOS) Crash Dump 分析：excep_store 解析、Fault Status 解码、FreeRTOS TCB 解析、LWIP memp 泄漏检测、DWARF 源码行号映射、objdump 反汇编上下文 |
| `spec-qcx216-dump-analyzer` | QCX216 死机、N706D 崩溃、excepInfoStore | QCX216/N706D 平台 (Unisoc ARM Cortex-M3 + FreeRTOS) Crash Dump 分析：excepInfoStore 解析、ASSERT/HardFault 识别、PC/LR→源码行映射（pyelftools，无需 ARM 工具链）、FreeRTOS 任务栈溢出扫描、OSA 协议栈专用池扫描 |
| `spec-code-summary` | spec 模块实现、spec 代码分析 | 单模块代码实现分析总结，输出结构化代码总结文档 |
| `spec-project-overview` | spec 项目概览、spec 了解项目 | 项目概览文档生成：目录结构映射、模块清单、技术栈识别、构建系统分析 |
| `spec-init` | spec 初始化、spec 准备环境 | `.spec` 工作流环境初始化，创建基础目录结构 |
| `spec-knowledge-archiver` | 归档bug、同步知识库、archive bug | 文档归档到持久化知识库，支持批量归档、向量索引更新、完整性校验 |
| `spec-neoway-coding-standards` | spec 编码规范、spec 代码规范 | Neoway 嵌入式 C 语言编码规范查询：编码风格、命名规范、注释规范 |
| `spec-requirement-generator` | spec 整理需求、spec 生成需求文档 | 零散需求（口头描述、会议记录）→ 结构化需求文档 |
| `spec-requirement-splitter` | spec 拆分需求、拆分需求 | 大需求按功能模块拆分为小需求单元，生成拆分清单 |
| `spec-solution-designer` | spec 设计方案、spec 技术方案、spec 出方案 | 需求 → 嵌入式技术方案：架构分层、RTOS 任务/并发、内存·功耗·时序预算、AT 与协议栈兼容、接口、风险 |
| `spec-implementation-planner` | spec 实施计划、spec 编写计划、spec 排期、spec 任务拆解 | 技术方案 → 委托 superpowers:writing-plans 产出代码级实施计划（内嵌代码步骤、no-placeholders）；硬约束：编码规范合规 + 输出 spec 路径 + 去 TDD 改嵌入式验证 |
| `spec-memory-leak-analyzer` | 分析内存泄漏、内存只增不减、free heap 一直掉、memory leak | 内存泄漏定位（call-stack 追踪：埋点记录 caller 地址，配对找泄漏点，MAP 映射到源码），覆盖 GCC/ARMCC/IAR/MSVC 

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
python skills/spec-knowledge-archiver/scripts/embed_search.py "{关键词}" --platform {平台} --top 5
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

### 需求 → 方案 → 计划

```
1. spec 整理需求             → 生成 .spec/requirement/{项目ID}_{功能}/需求.md
2. spec 拆分需求（可选）      → 大需求拆分为子单元
3. spec 设计方案             → 需求.md → 方案.md（嵌入式技术方案，同目录并排）
4. spec 实施计划             → 方案.md → 委托 superpowers:writing-plans 产 计划.md（代码级，去 TDD + 编码规范，同目录）
5. 执行                      → superpowers:executing-plans / subagent-driven-development（+ 可选 spec-neoway-coding-standards 复核）
```

## 许可证

Apache-2.0
