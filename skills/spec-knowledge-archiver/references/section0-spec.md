# 文档归档与检索友好性规范（模板统一基准）

> 本文件是所有"会被归档到知识库"的文档模板的**单一真理源**。
> 所有 spec-* skill 的 references/*-template.md 必须遵循本规范。
> 修改本文件后，需同步检查所有模板的实现一致性。
>
> 维护方：spec-knowledge-archiver（本 skill 负责 wiki 一致性）。
> 归档目标：`~/.spec-embedded-iot/knowledge/raw/platform/{平台}/{类型}/`，wiki 精炼页：`wiki/entries/{类型}/`。

## 一、YAML frontmatter 规范（所有归档文档必填）

所有会被归档到 raw/ 的 `.md` 文档，**第一行起的 YAML frontmatter** 必须包含以下字段（便于归档器/精炼器机器抽取）：

```yaml
---
title: <人类可读标题>           # 必填
date: YYYY-MM-DD                # 必填，文档产出日期
type: <文档类型>                # 必填，见下方枚举
platform: <纯平台名>            # 必填（project-overview 必填，其他视场景）
module: <模块名>                # 选填，但 bug/代码总结强烈建议
tags: [关键词1, 关键词2]        # 必填，3-8 个检索词
work_item_id: <缺陷/项目单号>   # bug/dump 必填，无则填 NA
---
```

### type 枚举（文档类型）

| type 值 | 含义 | 归档目录 | wiki 精炼页目录 |
|---------|------|---------|----------------|
| `bug-analysis` | bug 根因分析报告 | `bug-solutions/{wid}_{desc}/Bug分析.md` | `wiki/entries/bug-solutions/` |
| `dump-analysis` | crash dump 分析报告 | `bug-solutions/{wid}_{desc}/Dump分析.md` | `wiki/entries/bug-solutions/` |
| `bug-solution` | 修复方案文档 | `bug-solutions/{wid}_{desc}/修改方案.md` | （并入 entry） |
| `code-summary` | 模块代码总结 | `code-summary/{模块}/代码总结.md` | `wiki/entries/code-summary/` |
| `project-overview` | 项目概览 | `项目概览.md`（平台根） | `wiki/entries/project-overview/` |
| `requirement` | 需求文档 | `requirement-solutions/{id}_{desc}/需求.md` | `wiki/entries/requirement-solutions/` |
| `solution` | 技术方案 | `requirement-solutions/{id}_{desc}/方案.md` | `wiki/entries/requirement-solutions/` |

### platform 命名规则（强制）

**纯平台名，禁止带架构后缀/括号/版本号**。归档后 frontmatter `platform` 字段直接喂 wiki `--platform` 过滤，带后缀会破坏过滤。

| 正确 | 错误（会破坏检索） |
|------|------------------|
| `EC626` | `EC626 (Cortex-M + FreeRTOS)`、`EC626E`、`EC616` |
| `ASR1603` | `ASR1603 Cortex-R5`、`ASR` |
| `UIS8850` | `UIS8850 / ARM (Cortex-R + FreeRTOS)` |
| `UIS8852` | `UIS8852 (RISC-V)` |
| `QCX216` | `QCX216 / N706D` |
| `N58` | `N58 (展锐)` |

仓库名 → platform 映射见各 skill 的「平台识别」章节。

## 二、Section 0 结构化摘要规范（bug/dump/requirement/solution 必填）

bug/dump/requirement/solution 四类文档，正文**第一节**必须是 `## 0. 结构化摘要`，用固定字段表，供归档器机器抽取为 wiki entry frontmatter + 精炼页内容。

### 通用字段（4 类都必填）

| 字段 | 要求 | 用途 |
|------|------|------|
| **单号** | 必填。bug/dump 填工作项 ID（如 6977185133），无则 NA；requirement/solution 填项目 ID（如 7002450192） | 归档目录名 + INDEX.md + 追溯 |
| **平台** | 必填，纯平台名（见上方命名规则） | wiki `--platform` 过滤 |
| **模块** | 必填，如 MQTT / LWIP / UART / FOTA | wiki `--module` 过滤 + INDEX 分组 |
| **根因/需求概述** | 必填，一句话 | 直接喂 wiki entry「一句话根因」+ INDEX 摘要 |
| **检索关键词** | 必填，5-8 个中英文逗号分隔 | 喂 wiki entry `tags`，提升检索召回 |

### bug/dump 专有字段

| 字段 | 要求 | 备注 |
|------|------|------|
| **bug_type** | **必填，只能从下方 10 个规范值选一个** | 消除多版本枚举（曾经的根因之一） |
| **症状关键词** | 必填，3-5 个，逗号分隔 | 补充检索关键词，偏现象描述 |
| **调用链摘要** | 必填，箭头串（如 `CoAP GET → sock_event_queue → memp_malloc → 耗尽 → HardFault`） | 直接喂 wiki entry「调用链摘要」 |
| **当前分支** | 选填，git 分支名 | bug 追溯基线 |
| **兄弟文件** | 选填，列出同 bug 目录下其它文件 | 多文件关联（Bug分析/Dump分析/方案.md） |

#### bug_type 规范枚举（10 个，**禁止自由发挥**）

| bug_type | 适用场景 |
|----------|---------|
| `时序竞争` | 死锁、事件队列满、状态未同步、概率性失败 |
| `内存越界` | 栈溢出、heap corruption、数组越界、double-free、踩内存 |
| `状态机异常` | 状态转换错误、状态丢失、#if 0 跳过逻辑、阶段间上下文丢失 |
| `参数校验缺失` | 用户输入未校验、返回值未检查、无效地址被接受 |
| `资源泄漏` | 未关闭、未释放、内存/PCB/池累积耗尽、长测耗尽 |
| `编码错误` | 拼写错误、字符串/格式错误、字面量错误、类型转换错误 |
| `配置错误` | 条件编译宏漏配、波特率/缓冲区硬编码、特性未启用 |
| `硬件相关` | GPIO 默认电平、JTAG 复用、外部回环、芯片未响应 |
| `协议实现错误` | 协议栈实现偏离规范、状态机与协议不符 |
| `AT回码格式` | AT 回码格式不符、冒号空格、引号、兼容性 |

> 旧模板里的"资源耗尽/参数错误/协议异常/缓冲区溢出/超时"已废弃，按上表映射：
> - 资源耗尽 → `资源泄漏`
> - 参数错误 → `参数校验缺失`
> - 协议异常 → `协议实现错误`
> - 缓冲区溢出 → `内存越界`
> - 超时 → 视根因选 `时序竞争` 或 `协议实现错误`

### requirement/solution 专有字段

| 字段 | 要求 |
|------|------|
| **优先级** | 必填，高/中/低 |
| **状态** | 选填，草稿/评审中/已定稿/已归档 |
| **关联文档** | 选填，需求↔方案双向链接（如"关联方案：方案.md"） |

## 三、多文件归档约定（bug/requirement 场景）

一个 bug 或一个需求可能有多个相关文档，归档到**同一目录**：

```
bug-solutions/{wid}_{desc}/
├── Bug分析.md          # type: bug-analysis
├── Dump分析.md         # type: dump-analysis
├── 修改方案.md         # type: bug-solution
└── logs/               # 日志文件（不归档）
```

```
requirement-solutions/{id}_{desc}/
├── 需求.md             # type: requirement
├── 方案.md             # type: solution
└── 计划.md             # type: solution（实施计划）
```

**约定**：同目录的多个 .md 共享同一个 `{wid}_{desc}` 标识。归档器会为每个目录生成**一个** wiki entry 精炼页（综合多文件）。

## 四、各 skill 的模板实现要求

| skill | 模板文件 | Section 0 | frontmatter | bug_type |
|-------|---------|-----------|-------------|----------|
| spec-bug-analyzer | bug-report-template.md | 必含（含 bug 专有字段）| 必含 | 必含（10 值枚举） |
| spec-{5个平台}-dump-analyzer | bug-report-template.md / dump-report-template.md / ec-dump-report-template.md | 必含（含 bug 专有字段）| 必含 | 必含（10 值枚举） |
| spec-requirement-generator | requirement-template.md | 必含（含需求专有字段）| 必含 | 不适用 |
| spec-solution-designer | solution-template.md | 必含（含方案专有字段）| 必含 | 不适用 |
| spec-code-summary | code-summary-template.md | 文档信息表对齐字段名 | 必含 | 不适用 |
| spec-project-overview | project-overview-template.md | §1 拆原子字段 | 必含 | 不适用 |
| spec-requirement-splitter | split-checklist-template.md | 过程产物，不归档 | 不强制 | 不适用 |

## 五、维护流程

1. 修改本规范（`section0-spec.md`）→ 改完跑 `kb.py wiki lint` 确认 schema 仍一致
2. 同步检查所有模板实现（grep `bug_type` / `platform` / `## 0` 等关键字）
3. 现有 raw 产出的 frontmatter 不强制回填（向后兼容），但新产出必须遵循
4. 模板版本演化时，在 schema.md 的 log.md 追加一条 `## [日期] template | 详情`
