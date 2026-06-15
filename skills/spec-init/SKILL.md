---
name: spec-init
description: >-
  Spec 工作流环境初始化器。创建 .spec 基础目录结构。
  在首次使用工作流前或需要重置环境时调用。
  当用户说"使用spec 技能初始化环境"、"spec 初始化"、"spec 准备环境"时使用。
version: 2.0
author: niusulong
---

## 核心原则
1. **非破坏性**：初始化前检查已存在的目录，经用户确认后才执行
2. **幂等性**：多次执行初始化是安全的，不会重复创建或破坏已有结构
3. **按需创建**：只创建基础目录，功能目录由各技能按需创建

## 执行流程

### Step 1：检查环境状态
检查 `.spec` 目录是否已存在。如已存在，询问用户是否重新初始化。

### Step 2：创建基础目录

```powershell
New-Item -ItemType Directory -Force -Path ".spec" | Out-Null
New-Item -ItemType Directory -Force -Path ".spec/logs" | Out-Null
```

**目录说明**：
| 目录 | 用途 |
|------|------|
| `.spec/` | 根目录 |
| `.spec/logs/` | 日志文件（用户手动放置） |
| `.spec/bug/` | Bug 分析报告（由 spec-bug-analyzer、spec-asr-dump-analyzer、spec-ec-dump-analyzer 按需创建） |

**中央知识库**（跨项目持久化，独立于代码仓库）：
```
~/.agents/knowledge/platform/{平台名}/
  ├── 项目概览.md                        -- 由 spec-project-overview 生成
  └── code-summary/{模块名}/代码总结.md   -- 由 spec-code-summary 生成
```

**项目功能目录**（由各技能按需创建，不在此预创建）：
```
.spec/bug/{工作项ID}_{问题描述}/     -- 由 spec-bug-analyzer、spec-asr-dump-analyzer、spec-ec-dump-analyzer 按需创建
  Bug分析.md / Dump分析.md
  logs/                   -- 相关日志归档
  dump/                   -- dump 文件归档
.spec/{工作项ID}_{功能名}/          -- 由 spec-requirement-generator 等技能按需创建
  需求.md
  实现方案.md
  任务规划.md
  ...
```

### Step 3：输出初始化报告

```
✓ Spec 环境初始化完成

项目级目录：
  .spec/
  ├── logs/          (日志文件)
  └── bug/           (Bug 分析报告，按需创建)

中央知识库（跨项目持久化）：
  ~/.agents/knowledge/platform/{平台名}/
  ├── 项目概览.md
  └── code-summary/  (模块代码总结，按需创建)

项目功能目录（如 .spec/6974423486_MQTT_SSL双向认证/）和 Bug 目录（如 .spec/bug/6974423486_UDP链路未关闭/）由各技能按需创建。
工作项 ID 必须向用户确认，未提供时需询问。
现在可以开始使用 Spec 工作流了。
```

## 交互规则
- 目录不存在 → 直接创建，无需确认
- 目录已存在 → 询问用户是否继续
- 用户选择取消 → 中止执行
